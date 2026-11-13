# One integrated human-operated acceptance

This is a **future authorized test**, not an experiment performed by these instructions. Use the [operator guide](frigate-source-build.md) and [support contract](support-matrix.md). The frozen technical reference is source `8b8644a3b9e627a392166c398ab5ad1fca8d2de1`, Frigate `0.18.2-cfa08c5` and the exact official image pinned in that guide. Local G8 and synthetic browser evidence do not replace this real-source acceptance. Publication remains withheld.

## Approve inputs once

The master/operator must supply the reviewed full-history source origin/ref, build host/resources, three actual authorized signed camera outputs, matching trusted public CA, Windows11 access path, independent absolute paths/name/port, and permission for the real Historical UI request and scheduled lifecycle operations below. No GitHub URL, current camera availability or credential is invented here. This documentation revision needs a reviewed handoff checkpoint or approved accompanying instructions before it becomes an operator input.

Prefer a separate Ubuntu24.04 amd64 build/runtime environment with UID/GID1000 and the exact compiler/Node prerequisites. Do not change existing HA/Frigate services to make space. Reserve resources from measured availability; runtime uses4CPU/4GiB/256MiBshm. Build peak RAM and minimum total disk are not qualified. Assess N150 separately before using it; its existing services are protected. Build and runtime run sequentially, not concurrently.

Use a **fresh output and fresh runtime**, never an old accepted directory or developer container. Do not mount original recordings/databases writable. Do not use private `.local` scripts, prebuilt Nanexus artifacts, test CA or signing keys. An optional existing Nanexus Gateway or another supported signer supplies current H.264 signed RTSP; do not restart/reconfigure it or cameras. Verify the operator CA belongs to the actual current signing environment, not merely an old test recording. If the three sources require unsupported trust arrangements, stop for review rather than constructing a new bundle or PKI scheme.

## 1. Build and admit the source

Follow guide sections1–4: administrator prerequisites; full clone/check exact ref; independent pinned Frigate archive/image; one fresh build; verify; review and explicitly accept that exact local manifest once. Record source/tool/image/lock/pair/frontend identities privately. A fresh build can have different manifest/OMS bytes and requires its **own** acceptance; never copy the frozen reference's acceptance or overwrite it.

Proposed build deadline30minutes, no automatic retry or dependency substitution. Unsupported toolchain/network/identity is a stop, not permission to repair production hosts or weaken admission. No bit-identical reproducibility claim is required.

## 2. Prepare all three real bindings

Follow guide sections5–7 for private directories, one public CA, new private transport identity/token, explicit pair selection/container path translation, canonical nginx copy and the unchanged20 mounts. Use the following mapping consistently in the **private copies** of both templates:

|Native camera|Native go2rtc/live stream|Host context/rendition|Host source identity|Authorized URL|
|---|---|---|---|---|
|cam1|signed_cam1|grid / main|operator-cam1 / operator-1|CAM1_SIGNED_RTSP_URL|
|cam2|signed_cam2|grid / main|operator-cam2 / operator-1|CAM2_SIGNED_RTSP_URL|
|cam3|signed_cam3|grid / main|operator-cam3 / operator-1|CAM3_SIGNED_RTSP_URL|

In native YAML, replace the one `source_camera` entry with three copies named cam1/2/3. For each, set its ffmpeg input to `rtsp://127.0.0.1:8554/signed_camN`, roles `[record, detect]`, and its only `live.streams` value to `signed_camN`. Map go2rtc streams signed_cam1/2/3 to the actual authorized URLs. Keep recording/detection enabled, adjust detection dimensions only to actual input, and retain authentication/TLS and internal1984/8554 listeners.

In private Host JSON replace the one cameras entry with three entries having those camera/stream/context/rendition/source_id/revision values and the **same corresponding direct signed RTSP URL**. Other fields retain the template/selected pair and existing policy. No credentials in RTSP URLs, duplicate/ambiguous bindings, protected restreams, replay or threshold tuning. Have the operator review all three edits before startup; no new configuration helper or product code is needed.

Approve source load: native restream plus Host Entry readers create independent clients. Source availability and the ability to serve that load are prerequisites, not established by old evidence. Observe actual EOF/restart/recording gaps; do not repair or attribute them automatically to Nanexus.

## 3. Establish Windows11 access before handoff

Choose one unused host port and use it consistently for Docker loopback binding, Host browser_origin and Windows browser URL. Follow the guide's approved client-local SSH forward using an existing authorized K11/test-host identity. No public exposure, reverse proxy, global certificate-ignore, origin change or new firewall rule. A Codex remote project alone does not provide a browser forward.

Verify transport certificate validity/fingerprint and leave native authentication enabled. Retrieve initial admin credentials privately, then the **human** opens the Windows11 browser, acknowledges the intended private transport identity, logs in and discovers N→Live Monitor/Historical Verification. Do not place passwords, cookies, CA content or private keys in screenshots/reports. If access/TLS is not usable, stop before calling a session ready.

## 4. One real-source product observation

Start only the fresh target, with180s startup deadline. Check native/init, pinned version/image,nginx configuration, health,UID1000 Host Entry and authenticated Nanexus APIs; anonymous protected data must stay unauthorized. Use the guide's normal stop on startup failure, no repair/restart loop. Native health alone does not prove recordings, detector work or signing evidence.

Observe up to five minutes of current three-channel input, including finalization. Record source/mapping/session identity, receipt-time freshness, continuity uncertainty, native ffmpeg/source errors and actual Live reasons. The human inspects three camera tiles, actual still rendering, typography, status independence, theme/navigation and normal controls. In browser network tools count exactly one `latest.jpg?store=0` per tile appearance in a mounted monitor session; status polling is separate. No still refresh/retry loop or grid video. Record actual states; do not require all ACTIVE or identical outcomes.

Confirm finalized playable recordings/catalog entries and genuine completed detection/inference evidence. Retain hashes/timing of selected **new test recordings**, outside public evidence. Merely starting a detector or seeing an inference-speed field is insufficient. Unexpected source/network gaps remain evidence, not a reason to change transport/buffer/policy.

## 5. Human-operated real Historical request

Once eligible native recordings are finalized, the human selects one actual camera and a representable UTC minute interval≤30minutes and clicks Verify once. Match native catalog ranges to original retained files; do not rewrite timestamps, metadata or media. This is a real backend request, not a fixture or an API-created job adopted by the UI. Initial authorization should bound one request; if more camera requests are desired, approve the exact count beforehand, never repeat merely to get a favorable result.

Observe actual API progress, selected Worker loading its own OMS and a genuine terminal browser result. Proposed observation deadline120s; on timeout use the existing cancellation/termination protocol and record failure/incomplete outcome, not a blind resubmit. No expected verdict/color sequence. Record signing context, origin/coverage/continuity uncertainty and infrastructure errors independently.

The human checks chronological time-proportional Catalog intervals, colors/legend and hover recording start/end times with UTC/raw-time provenance. Display duration is recording metadata, not authenticated signing time or proof of signing coverage. If optional coverage is absent, inspect the explicitly labelled ordinal fallback; wrapping depends on its actual segment count. Do not fabricate gaps, padding or segments, add block navigation, or corrupt data to force error states. Record unexercised display cases rather than manufacturing them. Preserve result observations before volatile release/expiry; durable export/resume is not promised.

## 6. Planned lifecycle, membership and native-only recovery

Use guide section9 for one normal stop/start and one recreation with the same external files, then native-only disable/recovery with config/media/TLS and the same4CPU/4GiB bounds. These are planned acceptance transitions, not retries after failure. Capture finalized media hashes/catalog/playback and credential/config preservation across each; native DB bytes can legitimately change. Stop timeout/forced kill is not graceful success. Do not delete or automatically restore retained data.

Camera membership is configuration-driven and loaded on monitor mount. The human can leave/reopen Live and observe subscription cleanup/new appearances. If a configuration membership change is included, approve its exact private edit and scheduled restart in advance; do not claim dynamic within-one-mount updates or alter Gateway/camera configuration. Keep all source media and signing findings authentic.

Proposed total runtime/lifecycle cap30minutes, including startup/restart/recovery margins, generally much shorter. Each startup≤180s, each normal stop30s plus command margin. No endurance/fault campaign. The final master-approved schedule must identify the target IDs, failure cleanup and whether the final human handoff remains running until separately authorized shutdown. Never apply a generic auto-stop to unrelated/protected services.

## Acceptance and evidence

The human personally records layout, light/dark behavior where available, navigation, confusing text/branding, actual Live states and Historical result, then accepts/rejects the experience. Automated checks may support but cannot substitute for this decision. Keep one private report with command outcomes/source/asset identities, redacted API facts, actual screenshot viewports and native media/catalog provenance; keep raw credentials/DB/media out of reports.

PASS means the approved source-only instructions and explicit operator inputs suffice without private development knowledge; isolation/authentication/native recording/detection work; the real product path produces an honestly reported result; lifecycle/recovery preserves new test data; and the human accepts the observed experience. A cryptographic negative/indeterminate result is not automatically a product failure, while unsupported input or execution failure cannot be called complete verification. Missing dimensions remain explicit. Stop on substantive failure or need for source/architecture/rights change—no repair-and-retry in this session.

Preserve the accepted V1/D2 outputs,193 original MP4s, historical claims/CA/evidence and existing stopped containers. Stop only the new target as approved, retain its data and report. No commit, push, release, image publication or automatic next experiment. File-level rights, Frigate presentation and final source/history audit remain separate publication gates.
