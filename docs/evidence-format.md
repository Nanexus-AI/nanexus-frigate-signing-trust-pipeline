# Recorded evidence contract, version 1

## Domain and timing

`nanexus_trust.contracts` defines distinct enums:

| Axis | Values |
|---|---|
| Historical verification | `VALID`, `PARTIAL`, `INVALID`, `UNKNOWN` |
| Metadata coverage | `FULL`, `NOT_FULL`, `INDETERMINATE` |
| Requested-range coverage | `FULL`, `INDETERMINATE` |
| Live signing observation | `ACTIVE`, `DEGRADED`, `UNKNOWN` |

The last axis is only a vocabulary here; no Live observer is implemented. Runtime health is outside these verdicts. Enum types cannot substitute for each other even when their string values match.

`CameraId` and `RenditionId` are logical identities, not endpoints, paths or signer vendor identities. IDs are 1–128 ASCII letters/digits/`_.:-`, beginning with an alphanumeric character. A `RequestedRange` carries a caller-assigned request ID, camera, optional rendition and nonempty half-open interval. The request ID is a correlation key, not an authenticated identity or content digest.

Interval endpoints are decimal Unix UTC second strings, at most 20 integer digits and 9 fractional digits, with no exponent or floating-point coercion. `VerificationContext` carries the actual ordered interval list for the same camera, not merely an envelope. At least one interval must intersect the business request. A wider or partially covering context is permitted. No storage-file-to-interval one-to-one relationship is implied.

`covers` computes the interval union, retaining gaps. `HistoricalResult` accepts caller-supplied metadata coverage independently: this is a recorded claim, not fresh metadata discovery. Requested `FULL` requires `VALID`, metadata `FULL` and full actual context-union coverage. The converse is not forced. `VALID` with requested `INDETERMINATE`, and `PARTIAL` with metadata `FULL`, are legitimate. Non-UNKNOWN verdicts require context; UNKNOWN may have none. Recognition checks these relationships but cannot authenticate the truth of supplied observations.

`ObservationTime` separates a timezone-aware wall-clock timestamp from nonnegative monotonic seconds and session ID. Elapsed time uses only same-session, nondecreasing monotonic values. A backwards wall clock cannot change elapsed time. Runtime monotonic readings are not stored in Historical bundles or compared across processes. Stored wall-clock timestamps identify recorded creation time, not authenticated UTC. The writer normalizes them to UTC.

## API example

With `src` on the Python import path:

```python
from pathlib import Path
from nanexus_trust.contracts import (
    CameraId, HistoricalResult, Interval, MetadataCoverage, RequestedCoverage,
    RequestedRange, VerificationContext, VerificationResult,
)
from nanexus_trust.evidence import Component, PROFILE, Provenance, issue_bundle, recognize_bundle

request = RequestedRange("request-1", CameraId("front"), Interval("100", "105"))
result = HistoricalResult(
    request, VerificationContext(request.camera, (Interval("98", "110"),)),
    VerificationResult.PARTIAL, MetadataCoverage.FULL, RequestedCoverage.INDETERMINATE,
    "context_incomplete",
)
provenance = Provenance(
    Component("companion", "0.1.0"), PROFILE, Component("verification-backend", "1.0.0"),
    "bounded-h264-v1", "evaluation-trust-revision-1",
)
reference = issue_bundle(
    Path("new-evidence"), result, provenance, "2026-01-01T00:00:00Z",
    {"artifacts/report.json": ("verification-report", b'{"example":true}')},
)
recognized = recognize_bundle(Path("new-evidence"), reference)
assert recognized.result == result
```

The report above is illustrative recorded data, not proof that verification occurred. Production callers must supply real backend artifacts and meaningful trust/policy identities. This API does not synthesize, run or interpret a verification tool.

`issue_bundle` accepts artifact bytes, validates before creating a new destination, writes artifacts exclusively, then writes the receipt last. The destination parent must exist; existing destinations are refused. It never removes user data. An interrupted/failed write can leave an incomplete directory for the caller to inspect; the API does not silently repair it. This is application-level exclusive creation, not transactional durability or protection against hostile concurrent writers.

## Receipt structure

`receipt.json` has exactly two fields:

- `manifest`: the object described below.
- `manifest_sha256`: lowercase SHA-256 of canonical manifest bytes.

The manifest has exactly these fields:

| Field | Contract |
|---|---|
| `schema`, `version` | `nanexus-recorded-evidence`, integer `1` |
| `created_at` | Timezone-aware ISO timestamp; writer emits UTC |
| `clock_basis` | `unix_seconds_utc` |
| `result` | `request`, nullable `context`, `verification`, `metadata_coverage`, `requested_coverage`, `reason`, `detail` |
| `provenance` | `issuer`, `profile`, nullable `backend`, `policy_id`, nullable `trust_config_id` |
| `artifacts` | Array of `path`, `size`, `sha256`, `role` records |

Request fields: `request_id`, `camera`, nullable `rendition`, `interval` (`start`, `end`). Context fields: `camera`, `intervals` (array of `start`, `end` objects). A missing context is JSON `null`, not an invented empty interval. `reason` is a logical identifier; optional explanatory `detail` is a string of at most 2,048 characters. Callers must keep secrets and machine-specific private data out of details/artifacts; no automatic redaction is promised.

Component records have `name` and `version` logical identifiers. The supported provenance profile is `recorded-historical-result-v1`. Issuer/backend versions are audit information, not pins to source hashes or particular vendor tools. The profile means supported recorded-result structure and invariant checks, not validation of arbitrary backend report schemas. A non-UNKNOWN verdict requires both backend and trust configuration identities. Policy/trust identities describe the configuration used at issuance; changing current trust does not rewrite old bundles. Native signers and compatibility layers use the same contract; no Gateway-specific identity is required.

Version 1 rejects missing fields, unknown fields, duplicate JSON keys, unknown schema versions and unknown provenance profiles. There is no implicit migration or fallback. Later versions/profiles can explicitly define new interpretation rules or authenticated wrappers around the content reference without changing the basic verdict meanings. No extension field or evidence-signing PKI is implemented now.

## Portability, integrity and limits

Artifact paths must begin `artifacts/` and use nonempty ASCII components containing only letters/digits/`_.-`. Absolute paths, `.`/`..`, backslashes, colons and empty components are rejected. Referenced files and their path components cannot be symlinks. The bundle root itself cannot be a symlink. Callers choose a local bundle directory; no untrusted arbitrary-path network endpoint is provided. Only manifest-listed artifacts are interpreted; extra files are not evidence.

References are `sha256:<manifest digest>`, represented by `EvidenceReference`. They contain no physical location. The caller/store resolves references to directories. An optional expected reference checks that a located bundle is the expected content, including after relocation.

Canonical JSON is UTF-8 with sorted object keys, compact separators, literal Unicode and no NaN/Infinity output. Version 1 defines only strings, integers, booleans where applicable, arrays, objects and null; no float fields. JSON document key order/whitespace do not affect identity; string spelling and array order do. Writers sort artifact records by path. Artifact digests cover exact bytes. No absolute paths, source-code hashes, runtime layout or build location are required for interpretation.

Limits: receipt 1 MiB; 1–128 artifacts; at most 32 MiB each and 128 MiB total; context 1–128 intervals. Recognition reads artifacts in 64 KiB chunks and checks exact declared length and digest. Issuance takes caller-owned bytes in memory and is not a large-media streaming API. File operations assume a stable caller-owned snapshot throughout the call. These checks do not constitute a hostile-upload sandbox or concurrent-filesystem defense.

## Recognition versus verification

Success returns `RecognitionStatus.SUPPORTED`, the content reference, provenance and unchanged `HistoricalResult`. A recognized INVALID remains a recorded INVALID; a recognized UNKNOWN remains a recorded UNKNOWN. Recognition does not establish source/camera authenticity, replay freedom, signer health or authenticated time.

Missing, malformed, unsupported, inconsistent or inaccessible evidence returns `RecognitionStatus.UNKNOWN`, reason `unavailable_or_unsupported_evidence`, and no result/reference/provenance. Callers must display uncertainty, not a negative media verdict. No untrusted paths/content are echoed through the failure reason. New verification requires a separate operation; there is no hidden fallback.

The implementation reads local receipt/artifact files only. It imports no media backend, subprocess, network or database library, performs no reconstruction, and writes nothing during recognition. Hash consistency detects accidental/tampered bytes relative to the manifest; an attacker who can replace the entire receipt can recompute hashes. A separately trusted expected reference detects substitution relative to that reference but does not authenticate the issuer. Future signatures/authentication need a distinct trust policy.
