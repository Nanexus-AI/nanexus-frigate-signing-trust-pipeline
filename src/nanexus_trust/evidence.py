"""Issue and recognize portable recorded results; never perform media verification."""

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
from pathlib import Path, PurePosixPath
import re

from .contracts import (
    CameraId, EvidenceReference, HistoricalResult, Interval, MetadataCoverage,
    RequestedCoverage, RequestedRange, RenditionId, VerificationContext,
    VerificationResult, identifier, utc_timestamp,
)

SCHEMA = "nanexus-recorded-evidence"
VERSION = 1
PROFILE = "recorded-historical-result-v1"
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_ARTIFACT_BYTES = 32 * 1024 * 1024
MAX_TOTAL_BYTES = 128 * 1024 * 1024
MAX_ARTIFACTS = 128


def canonical_json(value) -> bytes:
    """UTF-8, sorted keys, no insignificant whitespace; v1 has no float fields."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _fields(value, names):
    if type(value) is not dict or set(value) != set(names.split()):
        raise ValueError("missing or unsupported object fields")
    return value


def _hash(value):
    if type(value) is not str or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("invalid SHA-256")
    return value


@dataclass(frozen=True)
class Component:
    name: str
    version: str

    def __post_init__(self):
        identifier(self.name)
        identifier(self.version)


@dataclass(frozen=True)
class Provenance:
    issuer: Component
    profile: str
    backend: Component | None
    policy_id: str
    trust_config_id: str | None

    def __post_init__(self):
        if not isinstance(self.issuer, Component):
            raise ValueError("typed issuing component required")
        if self.backend is not None and not isinstance(self.backend, Component):
            raise ValueError("typed backend required")
        identifier(self.profile)
        identifier(self.policy_id)
        if self.trust_config_id is not None:
            identifier(self.trust_config_id)


def _component(value):
    _fields(value, "name version")
    return Component(value["name"], value["version"])


def _provenance(value):
    _fields(value, "issuer profile backend policy_id trust_config_id")
    return Provenance(_component(value["issuer"]), value["profile"],
                      None if value["backend"] is None else _component(value["backend"]),
                      value["policy_id"], value["trust_config_id"])


def _interval(value):
    _fields(value, "start end")
    return Interval(value["start"], value["end"])


def _result(value):
    _fields(value, "request context verification metadata_coverage requested_coverage reason detail")
    q = _fields(value["request"], "request_id camera rendition interval")
    request = RequestedRange(q["request_id"], CameraId(q["camera"]), _interval(q["interval"]),
                             None if q["rendition"] is None else RenditionId(q["rendition"]))
    ctx = value["context"]
    if ctx is not None:
        _fields(ctx, "camera intervals")
        if type(ctx["intervals"]) is not list:
            raise ValueError("context intervals must be an array")
        ctx = VerificationContext(CameraId(ctx["camera"]), tuple(_interval(i) for i in ctx["intervals"]))
    return HistoricalResult(request, ctx, VerificationResult(value["verification"]),
                            MetadataCoverage(value["metadata_coverage"]),
                            RequestedCoverage(value["requested_coverage"]),
                            value["reason"], value["detail"])


def _artifact_path(value):
    # Portable artifact namespace: avoid POSIX/Windows traversal and alternate separators.
    if type(value) is not str or len(value) > 240:
        raise ValueError("invalid artifact path")
    parts = value.split("/")
    if (len(parts) < 2 or parts[0] != "artifacts"
            or any(p in ("", ".", "..") or not re.fullmatch(r"[A-Za-z0-9_.-]+", p) for p in parts)):
        raise ValueError("unsafe artifact path")
    return PurePosixPath(value)


def _validate_manifest(value):
    _fields(value, "schema version created_at clock_basis result provenance artifacts")
    if value["schema"] != SCHEMA or type(value["version"]) is not int or value["version"] != VERSION:
        raise ValueError("unsupported schema version")
    if value["clock_basis"] != "unix_seconds_utc":
        raise ValueError("unsupported clock basis")
    utc_timestamp(value["created_at"])
    result = _result(value["result"])
    provenance = _provenance(value["provenance"])
    if provenance.profile != PROFILE:
        raise ValueError("unsupported provenance profile")
    if result.verification != VerificationResult.UNKNOWN and (
            provenance.backend is None or provenance.trust_config_id is None):
        raise ValueError("verification requires backend and trust configuration identity")
    artifacts = value["artifacts"]
    if type(artifacts) is not list or not 1 <= len(artifacts) <= MAX_ARTIFACTS:
        raise ValueError("expected 1..128 referenced artifacts")
    seen = set()
    total = 0
    for item in artifacts:
        _fields(item, "path size sha256 role")
        _artifact_path(item["path"])
        if item["path"] in seen:
            raise ValueError("duplicate artifact path")
        seen.add(item["path"])
        if type(item["size"]) is not int or not 0 <= item["size"] <= MAX_ARTIFACT_BYTES:
            raise ValueError("artifact size outside bounds")
        total += item["size"]
        _hash(item["sha256"])
        identifier(item["role"])
    if total > MAX_TOTAL_BYTES:
        raise ValueError("bundle size outside bounds")
    return result, provenance


def _result_document(result):
    def interval(span):
        return {"start": span.start, "end": span.end}
    request = result.request
    return {
        "request": {"request_id": request.request_id, "camera": request.camera.value,
                    "rendition": None if request.rendition is None else request.rendition.value,
                    "interval": interval(request.interval)},
        "context": None if result.context is None else {
            "camera": result.context.camera.value,
            "intervals": [interval(i) for i in result.context.intervals]},
        "verification": result.verification.value, "metadata_coverage": result.metadata_coverage.value,
        "requested_coverage": result.requested_coverage.value, "reason": result.reason, "detail": result.detail,
    }


def issue_bundle(destination: Path, result: HistoricalResult, provenance: Provenance,
                 created_at: str, artifacts: dict[str, tuple[str, bytes]]) -> EvidenceReference:
    """Write a new bundle exclusively; never overwrite or delete an existing destination.

    Artifacts map bundle-relative paths to (logical role, bytes). A failed write may
    leave an incomplete new directory; only a completed receipt is recognizable.
    """
    def component(c):
        return {"name": c.name, "version": c.version}
    records = []
    if type(artifacts) is not dict or not 1 <= len(artifacts) <= MAX_ARTIFACTS:
        raise ValueError("expected 1..128 artifacts")
    for path, (role, data) in artifacts.items():
        _artifact_path(path)
        if type(data) is not bytes or len(data) > MAX_ARTIFACT_BYTES:
            raise ValueError("bounded artifact bytes required")
        records.append({"path": path, "role": role, "size": len(data), "sha256": _digest(data)})
    manifest = {
        "schema": SCHEMA, "version": VERSION, "created_at": utc_timestamp(created_at),
        "clock_basis": "unix_seconds_utc", "result": _result_document(result),
        "provenance": {"issuer": component(provenance.issuer), "profile": provenance.profile,
                       "backend": None if provenance.backend is None else component(provenance.backend),
                       "policy_id": provenance.policy_id, "trust_config_id": provenance.trust_config_id},
        "artifacts": sorted(records, key=lambda record: record["path"]),
    }
    _validate_manifest(manifest)
    reference = EvidenceReference(_digest(canonical_json(manifest)))
    encoded = canonical_json({"manifest": manifest, "manifest_sha256": reference.manifest_sha256})
    if len(encoded) > MAX_MANIFEST_BYTES:
        raise ValueError("manifest outside bounds")
    destination = Path(destination)
    destination.mkdir()  # Existing destinations, including symlinks, are refused.
    for path, (_, data) in artifacts.items():
        target = destination.joinpath(*PurePosixPath(path).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as stream:
            stream.write(data)
    with (destination / "receipt.json").open("xb") as stream:
        stream.write(encoded)
    return reference


class RecognitionStatus(str, Enum):
    SUPPORTED = "SUPPORTED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class Recognition:
    status: RecognitionStatus
    reason: str
    reference: EvidenceReference | None = None
    result: HistoricalResult | None = None
    provenance: Provenance | None = None


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _regular_file(root: Path, relative: str) -> Path:
    target = root
    for part in PurePosixPath(relative).parts:
        target = target / part
        if target.is_symlink():
            raise ValueError("symlink in bundle reference")
    if not target.is_file():
        raise ValueError("missing or nonregular bundle file")
    return target


def recognize_bundle(directory: Path, expected: EvidenceReference | None = None) -> Recognition:
    """Read/hash bounded local files only. SUPPORTED is not issuer authentication.

    Caller owns a stable bundle snapshot for the call. Concurrent hostile filesystem
    mutation is outside this offline API's boundary; this is not an upload service.
    """
    try:
        root = Path(directory)
        if root.is_symlink() or not root.is_dir():
            raise ValueError("missing or symlink bundle root")
        receipt = _regular_file(root, "receipt.json")
        with receipt.open("rb") as stream:
            data = stream.read(MAX_MANIFEST_BYTES + 1)
        if len(data) > MAX_MANIFEST_BYTES:
            raise ValueError("manifest outside bounds")
        document = json.loads(data, object_pairs_hook=_object)
        _fields(document, "manifest manifest_sha256")
        manifest = document["manifest"]
        result, provenance = _validate_manifest(manifest)
        reference = EvidenceReference(_digest(canonical_json(manifest)))
        if _hash(document["manifest_sha256"]) != reference.manifest_sha256:
            raise ValueError("manifest digest mismatch")
        if expected is not None and expected != reference:
            raise ValueError("evidence reference mismatch")
        for artifact in manifest["artifacts"]:
            target = _regular_file(root, artifact["path"])
            h = hashlib.sha256()
            size = 0
            with target.open("rb") as stream:
                while chunk := stream.read(65536):
                    size += len(chunk)
                    if size > artifact["size"]:
                        raise ValueError("artifact size mismatch")
                    h.update(chunk)
            if size != artifact["size"] or h.hexdigest() != artifact["sha256"]:
                raise ValueError("artifact digest or size mismatch")
        return Recognition(RecognitionStatus.SUPPORTED, "supported_recorded_result", reference, result, provenance)
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        # Malformed/unavailable evidence is not a negative cryptographic verdict.
        # No detail from untrusted paths or file content is echoed to callers.
        return Recognition(RecognitionStatus.UNKNOWN, "unavailable_or_unsupported_evidence")
