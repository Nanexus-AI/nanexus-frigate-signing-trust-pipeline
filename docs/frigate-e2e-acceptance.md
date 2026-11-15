# Isolated operator acceptance procedure

This runbook helps an operator evaluate a new source-built Frigate **0.18.2-cfa08c5** integration. It is not an installation guide, an automatic test runner or a production reliability qualification. Use the [source-build guide](frigate-source-build.md) first and the [support matrix](support-matrix.md#recorded-validation-observations) for prior observations.

Prior real three-camera E2E used source `df385a768656a4cd245913ebd724ae31c5e1e2c8`. The later restored timeline at `e84a7b7cfdbae960558a0582f4bd3cbf7dfee5e5` received separate focused UI and clean-build acceptance without repeating that E2E. The distinct 13-file and owner-observed 74-file Historical runs are recorded in the support matrix. Do not treat this reusable procedure as evidence that a new installation passed.

## Prerequisites and isolation

Choose a fresh external build output, runtime directory, target name and unused loopback port. Obtain an immutable reviewed source commit and the exact pinned upstream/archive/image from the guide. Preserve any existing deployments; never mount their configuration, database or original recordings into a writable test directory.

Use the guide's native Linux amd64 compiler/Node profile and UID/GID 1000. Assess actual free RAM, disk and source-client capacity. The documented runtime uses 4 CPUs, 4 GiB memory and 256 MiB shared memory; build peak RAM and minimum total disk are not qualified. Run build and runtime sequentially if capacity requires it.

Supply current authorized signed H.264 sources, their matching public CA, private transport credentials and a safe browser access path. For a three-camera evaluation, native ingest and Host Entry create two independent readers per source. Verify source capacity without reconfiguring cameras or signing services. Do not replay captures as fresh Live, tune policy to force a state, or fabricate recording metadata. Keep private inputs and reports outside source control.

Decide the observation/request/lifecycle bounds and cleanup target before starting. Suggested limits are 180 seconds per startup, five minutes of live observation, 120 seconds for one Historical request, 30 seconds for normal stop and a 30-minute total runtime/lifecycle budget. An unavailable prerequisite or timeout is a reported limitation, not an automatic repair/retry instruction. This document does not grant access to another operator's devices or services.

## 1. Build and admit local artifacts

Follow source-build guide sections 1–4 using a full clone and exact reviewed SHA. Verify the output, review its manifest and explicitly accept the exact manifest digest. Preserve source, tools, image, lockfile, frontend and pair identities privately. Never reuse another build's acceptance or overwrite frozen artifacts. A build failure requires diagnosis before deliberately choosing a new output root; no automatic dependency substitution is part of this procedure.

## 2. Prepare canonical bindings

Follow guide sections 5–7 for private configuration, one public CA, a separate transport identity, explicit Worker/OMS selection, container path translation, complete operational nginx copy and the unchanged 20 mounts.

For a three-camera session, expand both private templates consistently:

| Native camera | Native live stream | Host context / rendition | Logical source / revision |
|---|---|---|---|
| cam1 | signed_cam1 | grid / main | operator-cam1 / operator-1 |
| cam2 | signed_cam2 | grid / main | operator-cam2 / operator-1 |
| cam3 | signed_cam3 | grid / main | operator-cam3 / operator-1 |

In native YAML, create those camera and go2rtc stream entries. For each camera use `rtsp://127.0.0.1:8554/signed_camN` with roles `[record, detect]` and one `live.streams` value `signed_camN`. Set each go2rtc stream to its actual authorized signed URL. Keep native auth/TLS and internal go2rtc ports 1984/8554. Set detection dimensions to the actual input.

In private Host JSON create one entry per matching camera/stream/context/rendition with the corresponding direct signed URL and distinct source identity. Preserve the selected pair and policy. Host Entry does not accept URL credentials. Review all mappings before startup; no camera-only fallback, duplicated bindings or protected restreams.

## 3. Establish native readiness and browser access

Start only the new target. Check pinned image/version, native `/init`, nginx syntax, container health, non-root Host Entry and readiness marker. Inspect native generated admin credentials privately; keep passwords, cookies and keys out of reports. Native health does not prove recording, detection or signing evidence.

For a remote browser, use the guide's loopback SSH forward with an existing authorized host identity. Check the intended certificate/fingerprint deliberately and keep authentication enabled. Confirm login, N-menu discovery, Live Monitor and Historical Verification. Protected anonymous data requests must remain unauthorized; anonymous static assets are normal. Check native API/UI and Monaco loading alongside Nanexus endpoints.

## 4. Observe genuine Live and native recording

Record each source's actual status/reason, session/mapping identity, receipt-time freshness and continuity uncertainty. Record EOF, timestamp and ffmpeg restart diagnostics separately from signing status. Do not require all streams to be ACTIVE or identical.

Inspect actual still images and independent status updates. In browser network tools expect one `latest.jpg?store=0` GET per camera appearance in a mounted monitor session; image refresh/retry and video playback are not supplied by these tiles. Status polling is separate. Check layout, navigation, theme where available and subscription cleanup when leaving/reopening the view. Membership is loaded on mount; dynamic within-one-mount updates are not promised.

Reconcile finalized Catalog rows with actual new retained files, their time coverage and playable samples, allowing delayed finalization. Do not infer continuous recording from health or stats alone. If detection is in scope, require actual completed inference evidence; a live detector process or initialized inference-speed field is insufficient. Keep hashes and timing of new test media private.

## 5. Submit one real Historical request

Select one authorized camera and a real Catalog-backed minute interval in the displayed timezone of at most 30 actual minutes. Click Verify once after recordings have finalized. The API supports fractional seconds; minute controls are not an exact fractional-segment selector. Never edit media or Catalog rows to fit the query. A lost POST response may still have created work, so do not blindly resubmit.

Observe actual API progress, selected Worker loading its own OMS and a terminal UI outcome. On timeout, use the existing cancellation/termination controls and report the incomplete result. No particular finding/color is required.

Keep four things separate: execution state/counts, actual per-file Worker/OMS findings, Catalog metadata and fixed conservative response fields. `origin=HOLD`, `continuity=UNKNOWN`, `coverage=UNKNOWN` are not computed trust assessments. Optional `query_coverage` drives a Catalog-based proportional timeline, not cryptographic signing coverage. Check hover ranges and the legend; absent optional coverage uses the labelled ordinal grid. Record unexercised display cases without manufacturing data. No block navigation, durable result archive, resume or evidence ZIP download is promised by this window.

Preserve actual observations before volatile release/expiry. A negative or indeterminate finding is not by itself a product failure; execution failure is not successful verification.

## 6. Planned lifecycle and native-only recovery

If lifecycle acceptance is included, use guide section 9 for one normal stop/start, one container recreation using the same external files, and native-only recovery. These are planned transitions, not retries intended to improve a failed result. Check credentials/configuration, finalized recordings, Catalog visibility and playable samples across transitions. Native database bytes can legitimately change under native behavior.

Host Entry/readers/Worker must stop with native s6 and start normally on recreation. A stop timeout or forced kill is not graceful success. Keep original evidence outside writable native mounts and never restore or delete a database merely to match old bytes. Native migrations and retention remain active. Stop only the named disposable target and preserve its external data and needed container-local logs.

## Evaluation record

Record immutable source/build identities, exact target boundaries, redacted command/API outcomes, native recording/inference facts, actual screenshots and the human's visual assessment. Human review should identify layout or wording concerns separately from functional verification outcomes. Do not expose credentials, raw databases or camera media in public issues.

A successful evaluation requires honest results and completion of its declared scope: source-based installation, authentication/isolation, real product paths, native recording/detection where included, and planned data-preserving lifecycle checks. Missing dimensions stay explicit. Independent ordinary-user acceptance and production reliability must not be inferred from an earlier laboratory result. A substantive failure calls for diagnosis separately from this runbook, not an automatic repair-and-repeat cycle.
