# M1 support matrix

M1 is a source-integrated functional prototype for native **Linux amd64**, with an **Ubuntu 24.04** build profile, runtime Host Entry UID/GID **1000**, and **Frigate 0.18.2-cfa08c5** only.

Upstream source: `cfa08c56b23d23280c3d0a218e5a43182c97445b`.

Official image: `ghcr.io/blakeblackshear/frigate@sha256:fd60f99ffd4eb6c42360ed4e14953e270586f239309ab3983e55b2e97ffac09a`.

The [source-build guide](frigate-source-build.md) is the only current installation procedure. Operators build and explicitly admit their own frontend and Worker/OMS pair, then mount assets into the official image. No derivative image or prebuilt Nanexus package is supplied.

The [installation procedure](frigate-source-build.md#2-acquire-reviewed-sources-and-official-upstream-inputs) defaults to immutable technical source `e84a7b7cfdbae960558a0582f4bd3cbf7dfee5e5`. This identifies build inputs, not a distributed binary; every new local output requires its own manifest review and acceptance. A later documentation commit is not a newly G8-qualified technical build.

## Supported behavior

| Surface | Supported behavior and limits |
|---|---|
| Build | Pinned upstream/archive/image, Node 20 compatibility profile, fixed compiler checks, complete frontend/Monaco handling, paired Worker/OMS and explicit manifest acceptance. Node 20 is end-of-life; use the isolated build precautions in the guide. |
| Native integration | Native `/init`, s6, authentication/TLS, individual API overlays, complete writable operational nginx copy and non-root Host Entry. Twenty mounts separate immutable assets from operator-owned writable storage. |
| Live Monitor | Authorized enabled non-replay cameras, count-based desktop grid/pagination and distinct camera/stream/context keys. Membership is read on monitor mount; dynamic membership within one mounted view is not claimed. |
| Live images | One `latest.jpg?store=0` GET per displayed camera appearance in a mounted session; contained still images, no polling, retry or video playback in tiles. Status polling is separate. |
| Live status | ACTIVE: recent supported structural evidence under the configured policy. DEGRADED: overdue evidence while media progresses within policy limits. UNKNOWN: no current supported determination. No real-time signature verification, trust-anchor evaluation, replay freedom or camera-health conclusion. |
| Historical backend | Native Catalog selects actual retained originals through a read-only view. The selected accepted Worker loads its paired OMS and uses one operator public CA. |
| Historical UI | One authorized camera and display-timezone interval up to 30 actual minutes; proportional Catalog intervals, legend and hover recording ranges. A labelled ordinal grid is used only when optional query coverage is absent. No block navigation. |
| Historical meaning | Per-file colors reflect backend findings, separately from execution state and Catalog metadata. Fixed conservative window fields are not independent origin/continuity/coverage verdicts. See the [Historical reference](historical.md#current-frigate-0182-window). |
| Results and lifecycle | Current window results are volatile. No durable archive, resume, API-job adoption, Review/Export association or evidence ZIP download is promised by this UI. Native-only recovery retains external data, subject to native migrations and retention. |
| Trust and access | One operator public CA, read-only at `/nanexus/trust/ca.pem`. Native authentication/TLS and loopback access remain enabled. Host Entry disables TLS hostname checking for the trusted local certificate; see the [transport exception](frigate-source-build.md#transport-tls-and-hostname-checking). |

## Input and operating limits

Inputs must fit the supported H.264 / ONVIF Media Signing evidence subset. An optional Nanexus Gateway or compatible signer can supply media; not every ONVIF device/profile is qualified. Host Entry rejects credential-bearing RTSP URLs. Original retained recordings, not exported MP4 artifacts, are the verification input.

Catalog timestamps and proportional widths are not authenticated signing time or cryptographic signing coverage. GREEN is positive only within its reported scope, AMBER is partial/indeterminate, and RED is integrity-negative. Missing media, gaps, resource bounds and infrastructure failures remain distinct. Completion is not an all-VALID result.

The prototype has a fixed architecture/version/compiler/UID profile, loopback HTTPS and manual certificate renewal. Mobile support, HA, enterprise management, automatic upgrades, multi-version support, multi-CA administration and migration of existing deployments are not qualified. Independent ordinary-user source installation, recreation and recovery have not been qualified as a complete end-user workflow. No unattended 24/7 reliability claim is made.

## Recorded validation observations

| Observation | Evidence scope |
|---|---|
| Three-camera real-source E2E (D5), source `df385a768656a4cd245913ebd724ae31c5e1e2c8` | Three physical cameras through an existing signing Gateway; actual Live stills and ACTIVE/recent_signing_evidence states with distinct mapping/session identities; native retained recordings and playback samples; Windows 11 human review of Live. This is functional evidence, not an endurance or all-camera detection qualification. |
| Recorded 13-file Historical UI request in that session | 13/13 files completed, all AMBER; 3290 VERIFIED_SIGNING, 329 INDETERMINATE, 11 unresolved, 0 unprocessed. The response also carried fixed `origin=HOLD`, `continuity=UNKNOWN`, `coverage=UNKNOWN` fields; these are not additional computed trust results. |
| Separate owner-observed Historical request | 74/74 files, 72 GREEN and 2 AMBER. Counts are the owner's direct observation, not machine-captured terminal counts in the frozen session report. No job ID, time range, per-observation counters or trust-axis result is inferred. Non-root Worker loading its selected OMS was observed separately during the human session. |
| Later timeline restoration, source `e84a7b7cfdbae960558a0582f4bd3cbf7dfee5e5` | Focused fixture/browser/navigation checks, human visual approval and independent clean build/local G1–G8 acceptance. This is the accepted technical source. The full three-camera E2E was not repeated on the restored frontend. |
| Lifecycle precedent | Earlier private prototype lifecycle checks and the three-camera session's normal data-preserving shutdown provide bounded precedent, not independent ordinary-user source-handoff lifecycle acceptance. |

These are separate observations, not one aggregate test. Illustrations in the README are not validation evidence. Local artifact acceptance records build identity and operator review, not publisher attestation.

## Reliability observations

A prior native-recording isolation experiment observed a **43.175-second recording gap** with an upstream go2rtc EOF and native ffmpeg restart. A shorter five-minute no-fault control was clean. Gateway evidence narrowed a source/publication failure correlate without establishing the trigger or showing that Host Entry shutdown caused it. Neither experiment establishes production resilience or complete failure isolation. Native startup/shutdown diagnostics also remain outside a general reliability qualification.

Use the [operator acceptance procedure](frigate-e2e-acceptance.md) for a separate controlled evaluation. See [licensing boundaries](source-license-boundaries.md) for source and generated-output obligations, and [test portability](frigate-source-build.md#developer-checks-and-test-portability) before running developer checks.
