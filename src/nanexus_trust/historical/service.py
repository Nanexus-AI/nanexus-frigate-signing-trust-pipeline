"""Thin application API: new verification and completed evidence are separate paths."""

from datetime import datetime, timezone
from json import loads
from pathlib import Path
import uuid

from ..contracts import (
    CameraId, EvidenceReference, HistoricalResult, Interval, MetadataCoverage, RequestedCoverage,
    RequestedRange, VerificationContext, VerificationResult, covers, utc_timestamp,
)
from ..evidence import (Component, PROFILE, Provenance, canonical_json, issue_bundle, recognize_bundle,
                        MAX_ARTIFACTS, MAX_TOTAL_BYTES, Recognition, RecognitionStatus)
from .models import Availability, Candidate, HistoricalError, HistoricalOperation, Limits


class HistoricalService:
    def __init__(self, source, executor, backend_component, evidence_root: Path, trust_config_id: str,
                 *, limits=Limits(), policy_id="bounded-forward-h264-v1"):
        self.source = source
        self.executor = executor
        self.limits = limits
        self.evidence_root = Path(evidence_root)
        self.provenance = Provenance(Component("nanexus-historical", "0.1.0"), PROFILE,
                                     backend_component, policy_id, trust_config_id)
        if backend_component is None or trust_config_id is None:
            raise ValueError("backend and trust configuration identities required")

    def verify(self, camera: str, start: str, end: str, *, created_at=None):
        # Invalid caller shape is a validation error; operational failures below return UNKNOWN.
        request = RequestedRange(uuid.uuid4().hex, CameraId(camera), Interval(start, end))
        stamp = utc_timestamp(created_at or datetime.now(timezone.utc).isoformat())
        metadata = MetadataCoverage.INDETERMINATE
        context = None
        progression, artifacts = [], {}
        verdict, reason = VerificationResult.UNKNOWN, "METADATA_UNAVAILABLE"
        try:
            try:
                inventory = self.source.load(request.camera, self.limits)
            except OSError as exc:
                raise HistoricalError("METADATA_UNAVAILABLE") from exc
            all_rows = inventory.descriptors
            if inventory.metadata_complete:
                metadata = MetadataCoverage.FULL if covers(request.interval, tuple(d.interval for d in all_rows)) else MetadataCoverage.NOT_FULL
            initial = [i for i, d in enumerate(all_rows) if d.interval.overlaps(request.interval)]
            if not initial:
                raise HistoricalError("NO_MEDIA")
            selected = list(all_rows[initial[0]:initial[-1] + 1])
            next_index = initial[-1] + 1
            expansions = 0
            while True:
                candidate = Candidate(tuple(selected))
                if (len(selected) > self.limits.max_files or candidate.byte_count > self.limits.max_bytes
                        or expansions > self.limits.max_expansions):
                    raise HistoricalError("RESOURCE_LIMIT")
                if any(d.availability is Availability.MISSING for d in selected):
                    raise HistoricalError("MEDIA_UNAVAILABLE")
                if any(d.availability is not Availability.READY for d in selected):
                    raise HistoricalError("MEDIA_NOT_READY")
                context = VerificationContext(request.camera, tuple(d.interval for d in selected))
                ordinal = len(progression) + 1
                record = {"ordinal": ordinal, "candidate_id": candidate.identity,
                          "recording_ids": [d.recording_id for d in selected], "input_bytes": candidate.byte_count,
                          "verification": "UNKNOWN", "reason": "EXECUTION_INCOMPLETE"}
                progression.append(record)
                try:
                    observation = self.executor.execute(candidate, self.source, self.limits)
                except HistoricalError as exc:
                    record["reason"] = exc.reason
                    raise
                except OSError as exc:
                    record["reason"] = "BACKEND_UNAVAILABLE"
                    raise HistoricalError("BACKEND_UNAVAILABLE") from exc
                if type(observation.verification) is not VerificationResult:
                    raise HistoricalError("BACKEND_UNSUPPORTED")
                verdict = observation.verification
                if verdict is VerificationResult.PARTIAL and not observation.expandable:
                    raise HistoricalError("BACKEND_UNSUPPORTED")
                record.update({"verification": verdict.value, "reason": "BACKEND_RESULT",
                          "pending_frames": observation.pending_frames,
                          "pending_nals": observation.pending_nals, "integrity": observation.integrity,
                          "continuity": observation.continuity, "completeness": observation.completeness})
                if (len(artifacts) + len(observation.artifacts) + 1 > MAX_ARTIFACTS
                        or sum(len(data) for _, data in artifacts.values())
                        + sum(len(data) for _, data in observation.artifacts.values())
                        + len(canonical_json(progression)) > MAX_TOTAL_BYTES):
                    raise HistoricalError("RESOURCE_LIMIT")
                for name, artifact in observation.artifacts.items():
                    artifacts[f"artifacts/candidate-{ordinal}/{name}"] = artifact
                if verdict is VerificationResult.VALID:
                    reason = "VALID_CONTEXT"; break
                if verdict is VerificationResult.INVALID:
                    reason = "INVALID_CONTEXT"; break
                if verdict is VerificationResult.UNKNOWN:
                    reason = "BACKEND_UNKNOWN"; break
                if next_index >= len(all_rows):
                    reason = "MEDIA_EXHAUSTED"; break
                selected.append(all_rows[next_index]); next_index += 1; expansions += 1
        except HistoricalError as exc:
            if progression and progression[-1]["reason"] == "EXECUTION_INCOMPLETE":
                progression[-1]["reason"] = exc.reason
            verdict, reason = VerificationResult.UNKNOWN, exc.reason
        except OSError:
            verdict, reason = VerificationResult.UNKNOWN, "BACKEND_UNAVAILABLE"
        requested = RequestedCoverage.INDETERMINATE
        if (verdict is VerificationResult.VALID and metadata is MetadataCoverage.FULL
                and context is not None and covers(request.interval, context.intervals)):
            requested = RequestedCoverage.FULL
        result = HistoricalResult(request, context, verdict, metadata, requested, reason)
        artifacts["artifacts/progression.json"] = ("candidate-progression", canonical_json(progression))
        reference, evidence_reason = None, "ISSUED"
        try:
            self.evidence_root.mkdir(parents=True, exist_ok=True)
            reference = issue_bundle(self.evidence_root / request.request_id, result, self.provenance, stamp, artifacts)
        except (OSError, ValueError):
            evidence_reason = "EVIDENCE_UNAVAILABLE"
        return HistoricalOperation(result, tuple(progression), reference, evidence_reason)

    def recognize(self, reference: EvidenceReference):
        # Opaque reference lookup scans only receipts in this bounded application store.
        # The same API recognizes relocated bundles when pointed at the new store root.
        if not isinstance(reference, EvidenceReference):
            return Recognition(RecognitionStatus.UNKNOWN, "unavailable_or_unsupported_evidence")
        try:
            matches = []
            if self.evidence_root.is_symlink():
                raise OSError("invalid evidence root")
            for index, path in enumerate(self.evidence_root.iterdir()):
                if index >= self.limits.max_rows:
                    raise OSError("store lookup limit")
                if path.is_symlink() or not path.is_dir():
                    continue
                receipt = path / "receipt.json"
                if receipt.is_symlink() or not receipt.is_file():
                    continue
                # Do not read arbitrary-sized receipts while searching for the exact reference.
                if receipt.stat().st_size > 1024 * 1024:
                    continue
                try:
                    document = loads(receipt.read_bytes())
                    if document["manifest_sha256"] == reference.manifest_sha256:
                        matches.append(path)
                except (ValueError, TypeError, KeyError, RecursionError):
                    continue
            if len(matches) == 1:
                return recognize_bundle(matches[0], reference)
        except OSError:
            pass
        return Recognition(RecognitionStatus.UNKNOWN, "unavailable_or_unsupported_evidence")
