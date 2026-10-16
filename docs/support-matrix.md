# Live Signing Status support matrix

Validated profile: **Frigate v0.18.0 / 0.18.0-77a66e7**, source commit
`77a66e75c61862b048a07c1295877f4b31343504`, with the separate Nanexus
read-only relay and desktop UI patches. This is a version-specific development
integration, not an installation package or a claim of support for other Frigate
versions.

| Desktop Live surface | Validated behavior |
| --- | --- |
| Default multi-camera grid | One status per tile, using its actual camera and selected stream |
| Draggable/custom grid | Same exact playback identity and shared status delivery |
| Single-camera view | Persisted selected stream; stream changes begin UNKNOWN |
| Digital zoom/pan | Status stays outside the transformed media layer |
| DOM/container fullscreen | Status stays inside the fullscreen ancestor |

The visible label is **Signing Status: ACTIVE**, **Signing Status: DEGRADED**,
or **Signing Status: UNKNOWN**. ACTIVE means recent supported signing evidence
was observed. DEGRADED means signing evidence is overdue while media continues.
UNKNOWN means status is unavailable or evidence is insufficient. These are Live
observation states; they do not assert Historical cryptographic validity.

The browser uses Frigate's same-origin authenticated, camera-authorized endpoint.
Normal password login and native browser-cookie refresh were exercised in the
local desktop Chromium reference. The companion remains private. Transport
failure displays UNKNOWN without interrupting playback.

The shared browser client keys entries by camera, selected stream and playback
context (`grid` for both grid layouts; `single` for a single camera). It polls no
more often than once per second after a request completes, with two concurrent
requests and at most sixteen active identities. This follows the existing Live
observation timescale without observing media in the browser. A two-second
request timeout and 2.5-second delivery expiry bound stale display independently
of backend signing freshness. Excess identities remain UNKNOWN; unused entries
are removed when their last consumer unsubscribes.

Current runtime acceptance used **preserved real signed C210/Gateway H.264
replay**, presented through two local camera tiles and two stream aliases.
Stream switching exercises distinct configured rendition mappings of that replay;
it does not qualify separate hardware streams. Fresh hardware-chain
qualification and deployment/release acceptance are separate work.

Deferred and unsupported: Mobile-specific UI, Birdseye, native Picture-in-Picture,
native video fullscreen (including iOS native fullscreen/overlay), debug/settings
previews, and replay/playground. This desktop Live profile makes no claims for
Historical UI, Review verification, EventView, Motion Search, Explore, snapshots,
batch workflows or exported-file verification.

## Historical Verification — desktop Review/Export

The same **Frigate v0.18.0 / validated profile `0.18.0-77a66e7`** supports:

- Desktop `/review`, one selected recording camera, and the existing Export dialog.
- **Custom** selection of explicit start/end times, with the same rounded Unix-second bounds submitted by native Export. Requests are half-open `[start, end)`.
- An optional **Verify retained signed recording** action, independent of native Export submission and success.
- Durable companion verification jobs, restoration of the latest job for a selected range through Frigate's per-user browser storage, and evidence summary/download through camera-authorized routes.

Historical Verification applies to the **original retained signed recording material corresponding to the selected range**. It does **not** verify the exported MP4, assert byte identity with it, or label that file authentic. Exported-file verification needs a separate future artifact-binding contract.

Job lifecycle is separate from Verification Result (`VALID`, `PARTIAL`, `INVALID`, `UNKNOWN`). Metadata Coverage (`FULL`, `NOT_FULL`, `INDETERMINATE`), Requested-Range Coverage (`FULL`, `INDETERMINATE`), actual verification context, and evidence are displayed separately. FULL metadata does not establish complete verification context. Infrastructure failures do not become media INVALID; queued/running jobs have no media verdict.

The companion retains jobs and evidence. Existing Frigate per-user IndexedDB stores the latest association for each camera/range, bounded to sixteen associations. Clearing that browser storage removes its restoration references. An explicit new verification replaces that range's displayed job with a new execution; refresh/reopen only queries the saved job. Terminal jobs stop periodic polling. Native export status is queried separately, including its durable completed Export record when the queued-job record is unavailable.

Reference acceptance used preserved real signed C210/Gateway H.264 replay retained by Frigate. It exercised an actual completed INVALID result with FULL metadata and INDETERMINATE requested-range coverage, plus a recording-gap UNKNOWN result with NOT_FULL metadata. Neither outcome was manufactured by altering media. All four verdict displays are covered by focused tests. Normal password authentication, independent native export, evidence view/download, dialog reopen, page refresh/window reopen, companion outage and completed-job restoration after companion restart were exercised. Fresh hardware qualification, a full browser-process restart, and other browser engines were not qualified by this run.

Historical workflows deferred/unsupported: all Mobile Export; EventView and ReviewCard quick export; batch and multi-camera export; Motion Search; Explore clip/download; retrospective verification of existing exports; snapshots; continuous/background verification; automatic per-segment annotations; exported-file verification; standalone Historical dashboards. No Historical feature is enabled at those entry points.

See the [bounded Historical transport](historical-transport.md) for the private deployment boundary and response limits.
