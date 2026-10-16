# Private Live transport and Frigate relay boundary

This boundary targets only Frigate **v0.18.0 / 0.18.0-77a66e7**. It prepares browser consumption of Live status; it does not implement or claim desktop badges, supported UI surfaces, Historical transport or release readiness.

The path is browser → same-origin Frigate API → existing Frigate authentication and camera authorization → fixed-upstream relay → private companion → existing D1 service/scheduler. Frigate owns user access decisions. The companion owns mapping, observation, sessions and signing-status policy. Browser credentials never cross into companion.

## Private companion

`nanexus_trust.companion.transport.LiveTransport` wraps an existing `CompanionService`. `LiveHTTPServer` supplies a bounded private HTTP server; the deployment owns binding, service construction, scheduler driving and shutdown. The server never accepts a URL, source path or caller identity.

Only these authenticated GET operations exist:

- `/v1/live/status?camera=…&stream=…&context=…`: exact Frigate playback key passed unchanged to D1.
- `/readyz`: transport/service readiness only, independent of signing state.

No mapping inventory, mutation, Historical, evidence, administration, WebSocket or SSE routes. Each logical input is1–128 portable identifier characters, with exactly one of each required parameter and no extra query fields. Results are at most4,096 bytes. HTTP connections have a two-second delivery deadline, one-second socket timeout and at most eight concurrent handlers. The service/source contract remains nonblocking and bounded; transport does not replace it.

Every request requires a deployment-generated high-entropy bearer credential. Generate at least32 random bytes outside Git and mount privately; never hard-code, log or give it to browser JavaScript. `token_from_file` accepts bounded URL-safe tokens. Keep the listener only on a private service network, without host-published ports; do not enable CORS or direct browser access. Readiness requires the same credential. Missing/wrong credentials return401; unsupported methods405; malformed input400; nonexistent routes404. Service failure/malformed results produce a sanitized UNKNOWN, never cached ACTIVE or raw errors.

The response contains only `status`, `reason`, `age_ms`, `observed_at`, `last_evidence_at`, `session_id`, `mapping_revision`, `logical_camera`, `rendition`. Allowed status values are ACTIVE/DEGRADED/UNKNOWN, preserving M1-C semantics. `mapping_revision` carries D1's effective mapping identity, including configured revision/source/session replacement effects. Age is a decimal millisecond string or null. Missing/ambiguous mappings yield UNKNOWN without borrowing a different rendition. These are observation results, not cryptographic verification or replay-freedom claims.

## Separate version-bound Frigate relay

A separately maintained thin patch supplies `/api/nanexus/live/status` on Frigate's same-origin API. It uses the pinned `allow_any_authenticated` and `require_camera_access` primitives before any upstream request. Unauthorized requests remain401/403, not UNKNOWN; camera access is not replaced by companion's deployment allowlist. Use the authenticated Frigate port; internal admin-equivalent access is not runtime auth acceptance.

Trusted deployment configuration supplies `NANEXUS_LIVE_UPSTREAM` and `NANEXUS_LIVE_TOKEN_FILE`. The upstream is a fixed HTTP service base with no credentials, path, query or fragment. The relay appends only the fixed status path and validated parameters. It forwards only its service bearer header, never browser cookies, browser Authorization or identity headers. It follows no redirects or environment proxies. Companion private-network isolation is required; do not expose this HTTP service across an untrusted link.

The relay bounds total upstream work to1.5 seconds and decoded application response to4,096 bytes, rejects malformed/oversized/compressed/unexpected responses, and returns sanitized UNKNOWN on transport failure. No signing-status cache is introduced. Both backend response and the exact nginx location disable caching (`no-store, private`, `proxy_cache off`); the normal Frigate auth subrequest remains in place. GET-only status does not mutate configuration, mappings or jobs. Existing Live video paths remain independent.

The boundary's focused tests and reference acceptance must be assessed separately from future UI validation. Signed replay acceptance does not establish fresh camera/Gateway acquisition, camera identity, Historical validity or production reliability.
