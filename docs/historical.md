# Historical verification: current window and offline module reference

This reference distinguishes the integrated M1 product path from the independently usable offline service. Installation is documented only in the [source-build guide](frigate-source-build.md).

## Current Frigate 0.18.2 window

The supported window uses Host Entry, `companion/window_http.py`, the `historical_v2` coordinator and the selected portable Worker with its paired OMS library. It selects original retained files from the authenticated native recording Catalog. It does **not** use the older durable `HistoricalService`/JobStore workflow described below and in the [historical transport design](historical-transport.md).

| Response or UI element | Meaning and limits |
|---|---|
| Per-file `finding` / `state` | Actual Worker/OMS-derived GREEN, AMBER or RED for completed files; PENDING is execution progress, not a verdict. Partial/indeterminate AMBER is not automatically cryptographic failure. |
| Top-level `state`, `reason`, completion counts | Request execution outcome; COMPLETE does not mean all files are VALID. Infrastructure errors and unavailable media remain distinct. |
| `metadata_interval`, Catalog span and gaps | Native recording selection metadata; not authenticated signing time or proof of continuous signed media. |
| Optional `query_coverage` | Catalog-based partition of the selected query, used for proportional display. It is not cryptographic signing coverage. Absent data uses a labelled ordinal grid; malformed data produces an error. |
| `origin=HOLD`, `continuity=UNKNOWN`, `coverage=UNKNOWN` | Fixed conservative fields returned by `observe()` for this window. They are not independently computed per-request assessments and the UI does not render them as separate verdict axes. They do not replace the actual per-file findings. |
| `summary`, `evidence_available`, `summary_only` | Backend progress/availability metadata, not a durable evidence archive or a current UI download feature. |

The UI offers one authorized camera and an increasing interval of at most 30 actual minutes in Frigate’s configured UI timezone, or the browser timezone when unset. It displays state, counts, per-file colors and Catalog intervals, with hover information and no block navigation. Results are volatile; terminal release/expiry affects Nanexus memory. No durable result management across restart, UI adoption of an API-created job, resume, Review/Export association or evidence ZIP download is promised by this window.

Source references: `src/nanexus_trust/companion/window_http.py` (`observe`, `_query_coverage`) and `ui/nanexus/historical.js` (`apply`, `drawCoverage`, `drawSegments`). Keep execution state, file findings and metadata coverage distinct when consuming the API.

## Standalone offline HistoricalService

The standalone `HistoricalService` application accepts a camera and half-open requested time range. Deployment configures a metadata source, finite executor, verification backend, evidence store, public trust configuration identity and limits. There is no HTTP listener, daemon, SQLite watcher, Live observer or dependency on a running Frigate instance.

## Responsibilities

| Component | Responsibility |
|---|---|
| `HistoricalService` | Validate a request, select candidates, bound forward expansion, calculate independent coverage, issue evidence and recognize completed evidence |
| `MetadataSource` protocol | Supply a coherent ordered retained inventory and read frozen bytes; operational failures use `HistoricalError` reason codes |
| `FrozenFrigateSource` | Translate caller-supplied Frigate rows, attest readiness, confine paths and detect missing/changed files |
| `FiniteExecutor` | Copy finite immutable inputs, inspect packet positions, preserve actual H.264 NAL payloads in order and delegate verification |
| `VerificationBackend` protocol | Return an M1 result enum, integrity/continuity/completeness, pending counters and optional supporting artifacts |
| `ToolkitBackend` | Interpret supported verify/inspect schema and exits at one boundary; preserve supported JSON reports without local command lines |

Within this standalone service, `HistoricalResult` is the verification/coverage model. `HistoricalOperation` wraps it with candidate progression, evidence reference and evidence-storage outcome; these are not alternative verdicts. The executor and backend have no Frigate schema, business-range planning or expansion logic. The metadata adapter does not classify signatures.

## Metadata and readiness contract

Supply an immutable tuple of row dictionaries with `id`, `camera`, `start_time`, `end_time`, `path` and boolean `closed`. Time endpoints are decimal Unix UTC second **strings**, preserving metadata precision without binary floating-point conversion. Adapters supplying JSON/SQLite data must normalize these types deliberately. Rows are copied on construction, filtered by exact camera and sorted by numeric start/end then recording ID. Duplicate identities/paths or malformed rows are rejected conservatively.

The configured container recording mount maps onto one retained-media root. This is configuration, not part of trust logic or portable evidence. Relative references are derived only from metadata paths; filesystem names never create rows. Traversal, another mount prefix, backslashes and symlinks in the retained path/root are rejected. Files must be regular, nonempty and explicitly closed. No symlink resolution fallback occurs.

Availability distinguishes `READY`, `NOT_READY`, `MISSING` and `UNAVAILABLE`. Every descriptor is indexed by construction; existence does not mean closed/eligible. READY records carry actual byte count and digest, checked during extraction and again before candidate acceptance. Oversized files cannot bypass resource limits. `coherent=False` refuses the snapshot; `complete=False` retains INDETERMINATE metadata coverage even if supplied intervals appear to cover the request.

The caller attests that rows are a coherent snapshot and that `closed` has been established independently. These assertions are suitable for frozen offline fixtures; they do not implement current file readiness or WAL-coherent discovery. A runtime adapter must supply the real closure/coherence mechanism; these caller attestations are not the current window's Catalog admission procedure. A stable caller-owned filesystem is required throughout adapter operations; this API is not a hostile concurrent-filesystem sandbox.

## Candidates, expansion and coverage

Initial candidate contains indexed objects intersecting the requested range, including any intervening ordered rows. It can span multiple files. Any missing/unready required object stops conservatively. Context intervals retain their actual gaps and overlaps, separately from the business request. Candidate content identity binds ordered recording IDs, camera, intervals, sizes and hashes; it excludes physical paths. Policy and backend identities are recorded separately in provenance.

Only a supported positive-integrity/intact/incomplete PARTIAL with positive pending frame and NAL counters permits expansion. Admit the next indexed neighbor only; an unavailable/unready neighbor is a barrier, not a reason to guess around it. Expansion has finite file/byte/step bounds. No unbounded search, backwards expansion or INVALID repair is implemented.

| Outcome | Result / reason | Coverage |
|---|---|---|
| Complete verified context | VALID / VALID_CONTEXT | Requested FULL only if metadata and actual context union cover the request |
| Incomplete context with no supplied forward object | PARTIAL / MEDIA_EXHAUSTED | Requested INDETERMINATE, even with metadata FULL |
| Supported cryptographic failure | INVALID / INVALID_CONTEXT | Stop immediately; no expansion |
| Unmapped backend evidence | UNKNOWN / BACKEND_UNSUPPORTED or BACKEND_UNKNOWN | Never a media INVALID |
| File/byte/step/output bound | UNKNOWN / RESOURCE_LIMIT | Earlier candidate observations remain in evidence |
| Missing/unready/changed media | UNKNOWN / MEDIA_UNAVAILABLE, MEDIA_NOT_READY or MEDIA_CHANGED | Metadata may independently remain FULL |
| Unreadable/untrusted metadata | UNKNOWN / METADATA_UNAVAILABLE | Metadata INDETERMINATE |
| No intersecting indexed object | UNKNOWN / NO_MEDIA | NOT_FULL for complete metadata, otherwise INDETERMINATE |
| Tool failure/timeout | UNKNOWN / BACKEND_UNAVAILABLE or BACKEND_TIMEOUT | No fabricated cryptographic verdict |

Metadata coverage uses the supplied inventory's interval union independently of file availability or verification. Requested FULL additionally requires VALID and actual submitted context-union coverage. Storage files, the requested business range and trust context are distinct objects. Metadata FULL cannot prove sufficient forward signing closure, authenticated UTC, per-frame trust or camera identity.

## Application wiring

With `src` on the import path, deployment code can construct:

```python
from pathlib import Path
from nanexus_trust.frigate.metadata import FrozenFrigateSource
from nanexus_trust.historical.backend import ToolkitBackend
from nanexus_trust.historical.executor import FiniteExecutor
from nanexus_trust.historical.models import Limits
from nanexus_trust.historical.service import HistoricalService

# rows are supplied from a coherent offline export; keys/types are described above.
source = FrozenFrigateSource(rows, Path("retained-recordings"))
backend = ToolkitBackend(Path("tools/video-trust"), Path("trust/ca.pem"), "configured-version")
service = HistoricalService(
    source, FiniteExecutor(backend, Path("tools/ffprobe")), backend.component,
    Path("evidence"), "trust-revision-1", limits=Limits(),
)
operation = service.verify("front", "100", "105")
result = operation.result
if operation.evidence_reference is not None:
    completed = service.recognize(operation.evidence_reference)
```

Configuration supplies actual executable paths/versions and public trust anchors; no paths or versions above are universal defaults or installation instructions. The verifier never needs signer private keys or Gateway identity. Malformed camera/time arguments or invalid configuration raise `ValueError` before a valid request exists. Operational metadata/media/backend failures return structured UNKNOWN. Custom adapters/backends must implement the documented protocol and raise `HistoricalError` for expected operational failures.

## Executor and tool boundary

The supported extraction profile is a single H.264 MP4 stream with length-prefixed NALs and ordered, nonoverlapping packet byte extents. Actual sample payloads become Annex-B with four-byte delimiters. Terminal SEI-only packets are retained. There is no decoder, transcoder, avcC injection, repair or synthesized metadata.

Configured ffprobe supplies packet positions for owned temporary copies. The Toolkit adapter calls verify and inspect, checks their agreement, supported schema/profile, trust findings, nonnegative consistent counters and exits. A supported INVALID profile is distinct from an unknown/unavailable tool. Unsupported certificates/schemas/findings are conservatively UNKNOWN; this does not promise exhaustive Toolkit or vendor interoperability.

Defaults: 3 files, 2 expansions, 16 MiB per candidate/reconstruction, 1,000 metadata rows, 64 MiB inventory, 15 seconds per subprocess and 8 MiB per output stream. Output is written to owned temporary files rather than unconsumed pipes; a POSIX process session is killed on time/output violation. Output monitoring can transiently overshoot the threshold between polls; this is not a hard filesystem quota. Configured executables must be trusted. Temporary inputs/streams are removed only by their owning invocation. Overall cost scales with explicitly configured bounds; these defaults are not throughput guarantees.

## Evidence and recognition

Each request attempts M1-A bundle issuance, including unknown and exhausted outcomes. Artifacts include candidate IDs, ordered recording IDs, byte accounting, pending counters, integrity/completeness and preserved backend reports when configured. Failed execution attempts record an UNKNOWN progression entry. Stop reason is in the final result. References/provenance contain no private local absolute paths. Supporting JSON is checked for configured/local absolute paths; raw command lines, stderr and ffprobe documents are not exported. Custom backends remain responsible for providing portable artifact data.

Evidence-storage failure is separate: `evidence_reference=None` and `evidence_reason=EVIDENCE_UNAVAILABLE`; it cannot rewrite a valid or partial verdict. Evidence success is `ISSUED`. Partial output directories are preserved without overwrite or automatic repair.

Completed lookup accepts only an `EvidenceReference` and reads a bounded local store of receipts. It never calls metadata discovery, the executor or backend. Missing, duplicate, malformed, tampered or unsupported evidence remains recognition UNKNOWN. Pointing the service at a relocated store preserves recognition. This small bounded directory lookup is not a production database/registry; exceeding its configured row bound returns uncertainty.

The public fixtures are synthetic tiny files and normalized complete/incomplete progression observations. They test orchestration, not real signatures. Tests require no media binaries, installed Toolkit or private evidence. Real signed-media qualification is separately recorded locally; private camera media is not distributed. Hash-consistent evidence is not issuer authenticity.

Historical resolves the timezone once when opened from authenticated Frigate `/api/config`: `ui.timezone`, otherwise the browser IANA timezone. If configuration cannot be read or its timezone is unsupported, a visible browser-fallback label identifies the limitation; reopen to retry. Nonexistent or repeated local times are rejected with an actionable message. Same-day selections are converted to absolute Unix seconds before duration checks and submission; backend timestamps and signing findings are unchanged.
