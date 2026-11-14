# Live signing-status core reference

This is the standalone observer/service contract for developers. The current Frigate 0.18.2 integration supplies transport ownership through Host Entry and its scheduler; follow the [source-build guide](frigate-source-build.md) for deployment. Statements below about what the core does not own are module boundaries, not missing integrated product features.

Live observes supported compressed H.264 signing structures at actual receipt times under an explicit policy. It does not verify signatures, call Historical verification, produce Historical receipts or establish retained-media coverage. Media progress and signing evidence are separate observations.

## States and reasons

| State | Meaning |
|---|---|
| `ACTIVE` | Supported signing evidence is within the configured freshness limit, media is still progressing and the current observation remains interpretable |
| `DEGRADED` | Previously observed supported signing evidence is overdue, while media is progressing and evidence remains within the configured observation horizon |
| `UNKNOWN` | A current supported determination is unavailable: startup/reset, no supported evidence yet, missing policy, media silence, evidence beyond the horizon, unsupported structure, mapping ambiguity or transport failure |

ACTIVE does not mean Historical VALID, authenticated source identity, healthy signer/camera, replay freedom or complete retention. DEGRADED does not identify tampering or a failed signer. Media without any supported signing evidence remains UNKNOWN; the service does not strengthen uncertainty into DEGRADED merely because media exists.

Known core reasons include `recent_signing_evidence`, `signing_evidence_overdue`, `no_signing_evidence_in_window`, `freshness_unconfigured`, `evidence_stale`, `parser_uncertainty`, `unsupported_signing_structure`, `observation_discontinuity` and `observer_unavailable`. Mapping/adapter failures use conservative logical reasons and never echo endpoints or exception text.

## Explicit policy and clocks

`LiveSigningPolicy` requires a logical policy ID plus `evaluation_interval`, `active_freshness_limit`, `observation_horizon` and `media_silence_limit`. Durations must be finite/positive; horizon must exceed freshness and be at least the media-silence limit. There are no universal timing defaults or automatic calibration. Deployment must select policy for its measured rendition and buffering behavior.

Evidence age equal to the freshness limit is ACTIVE; greater age is DEGRADED only if media is recent and age is at most the horizon. Age beyond the horizon is UNKNOWN. Media silence greater than its limit overrides freshness with UNKNOWN. Equality at the media-silence/horizon limits remains eligible. Structural/transport uncertainty overrides all freshness calculations.

The common `ObservationTime` carries a logical session ID, decimal monotonic seconds and timezone-aware wall time. Elapsed time uses monotonic time, never UTC subtraction. All events and queries for an observer share one nondecreasing monotonic domain, including reconnects. Wall time is display correlation, not authenticated signing time. Events must retain actual receipt timestamps; do not retimestamp an old queue/file as current.

The observer evaluates on calls. The policy evaluation interval is a deployment cadence obligation; this package starts no clock thread, scheduler or continuous ingest loop. A query always evaluates age at its current clock, rather than returning a cached ACTIVE result.

Use serialized owner calls. Concurrent transport scheduling and thread-safe service ownership are not implemented here.

## Logical facade and source contract

```python
from nanexus_trust.contracts import CameraId, RenditionId
from nanexus_trust.live import Binding, LiveService, LiveSigningPolicy

# Illustrative values only: choose and validate a policy for the actual deployment.
policy = LiveSigningPolicy("deployment-policy-v1", 0.1, 1.0, 4.0, 1.0)
binding = Binding(CameraId("front"), RenditionId("record"), "compressed-source", "config-revision-1", policy)
service = LiveService((binding,), source_factory)
result = service.get_status("front", "record")
response = result.to_dict()
service.close()
```

`source_factory(binding)` is deployment-supplied. A source implements prompt, nonblocking `next_event(now)` and `close()`. `now` is decimal monotonic seconds; return one due `ObservationEvent` or `None`. Close must be prompt, idempotent and release the connection without raising expected operational failures. Factory/poll availability failures may raise `OSError`; those produce UNKNOWN. Other adapter programming errors are not silently hidden.

An event has `kind`, explicit `ObservationTime`, and immutable `bytes` data:

- `begin`: establish a unique session before any bytes.
- `bytes`: complete or fragmented Annex-B data with its receipt timestamp.
- `disconnect`: immediately disable eligibility and clear pending parser bytes.
- `eof`: end observation without interpreting an incomplete final NAL as clean cryptographic closure.

Use a new session ID for reconnect/replacement; never reuse session identities. A new begin clears all old evidence/media timestamps and counters. Old-session or backdated events cannot qualify the new session. Sources own their bounded buffering and must report transport uncertainty explicitly. A connection joining mid-NAL must align to an Annex-B start code; the observer is not a transport repair mechanism.

Bindings contain only typed logical camera/rendition, logical source ID, configuration revision and policy. Exact resolution requires one match; absent/duplicate mappings yield UNKNOWN. `configure()` releases invalidated sources. Source, rendition, revision or policy changes discard the old observer so old freshness cannot survive a remap. No camera-only or default-stream fallback exists.

`stop(camera, rendition)` disables that observation; reads do not implicitly reopen it. `close()` releases all sources and prevents new opens. A stopped/factory-failed mapping can be reopened by deployment configuration replacement with a new revision. Ordinary reconnects within an available source use explicit disconnect/begin events. No retry daemon is supplied.

The core is also usable independently as `LiveObserver(policy)` with `begin`, `feed`, `end` and `snapshot`. Malformed explicit clocks/configuration raise validation errors. The facade converts validly timestamped adapter/session failures into conservative UNKNOWN and closes the observation. It does not own RTSP, go2rtc, SSH, FIFOs or a running Frigate configuration.

## Result envelope and security

`LiveResult` contains camera/rendition, status/reason, observation timestamp, evidence age, last evidence timestamp, session reference, opaque mapping identity, policy ID and separate media/evidence observation booleans. `to_dict()` emits status and decimal milliseconds as strings for JSON transport. No raw source ID/URL, credentials, parser state, timing thresholds, private keys or Historical verdict appear in caller responses.

Identifiers are logical names, never secret values. Policy/source IDs reject endpoint/path syntax. Transport configuration belongs to the deployment adapter, not this package's mapping/result contract. The current Host Entry accepts credential-free RTSP URLs; service bearer credentials remain private. No observation log or artifact is persisted. Ended-session timestamps may remain factual diagnostics while status is UNKNOWN; they never confer current eligibility and are cleared on replacement. Display booleans describe observed presence within the horizon, not health or authenticity.

## Supported structure and resource bounds

The parser recognizes the measured H.264 Annex-B ONVIF general-v2 SHA256 layout with signature-v1 structural encoding. Optional metadata TLVs remain opaque. Structural signature bytes are not authenticated. H.265, arbitrary vendor variants and universal native-camera interoperability are not supported claims.

Default parser ceilings are 1 MiB per NAL, 64 KiB per input chunk, 64 SEI messages per NAL and seven supported unique TLV tags. Limits can be lowered explicitly. The parser retains at most one pending NAL plus delimiter/chunk allowance; overflow or unsupported structure latches UNKNOWN until a new session. A complete NAL requires a following start code; EOF/reconnect discard incomplete edges without inventing signing evidence.

Each observer retains one session, two observation timestamps and saturating counters, with no session/event history list. The horizon limits status eligibility; at most two old timestamp scalars may remain factual diagnostics until replaced. The facade supports at most 16 bindings/entries and drains at most 256 events per query. Exhausting the query budget disables observation as UNKNOWN/adapter_backlog rather than holding an unbounded queue. The source implementation must enforce its own queue bound. Scalar parser diagnostics are for deployment/tests and are excluded from caller results.

Public tests use tiny synthetic structural payloads with deliberately non-cryptographic signature placeholders. They test parser/status/lifecycle semantics, not real signature verification. These core tests do not establish current transport behavior. The integrated Host Entry owns current readers and scheduling; bounded three-camera observations are documented separately in the [support matrix](support-matrix.md).
