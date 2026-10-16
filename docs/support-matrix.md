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
