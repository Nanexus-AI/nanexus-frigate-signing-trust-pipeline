# Source licensing and distribution boundaries

Nanexus-controlled later contributions use **Apache-2.0**, with earlier **MIT** permissions and distinct upstream terms retained. Root [LICENSE](../LICENSE) contains Apache-2.0. [NOTICE](../NOTICE) contains the contribution schedule, current source identities, retained Nanexus MIT permission and upstream attribution. The portion-specific terms below take precedence over any assumption of exclusive whole-file licensing.

Source permissions and trademark authorization are separate. Neither this source license map nor local build acceptance grants permission to redistribute upstream logos, generated frontend assets or third-party binaries beyond their applicable terms.

## Scope and accompanying records

|Source scope|Applicable record and treatment|
|---|---|
|Nanexus companion/product, tools, docs and tests|Root Apache-2.0 LICENSE and product NOTICE, subject to the portion-specific exceptions below; no general relicensing of third-party code|
|The 15 integration paths below|Apache-2.0 for the identified later project contributions; retained MIT for legacy portions and any valid earlier grants; Frigate MIT for verified external portions|
|Eight Toolkit-derived files under src/nanexus_trust/historical_v2/videotrust/|Existing Apache terms and exact original paths/commit 678d8135f738a813831898388266842ede90c5e7 in NOTICE; no other Toolkit source bundled|
|third_party/media-signing-framework/|ONVIF MIT LICENSE, source headers and PROVENANCE at r25.12.6/cf7785ab993c18d921094e8e505c2c34a1350f28, including TLV/Meson modifications; not Nanexus Apache|
|Copied Frigate fixtures, schema and overlay excerpts|Full Frigate MIT copyright/permission/disclaimer in NOTICE; copied portions remain external regardless of the integration's own-contribution license|

## Exact integration contribution schedule

The following 15-path schedule identifies contribution boundaries and source hashes. It is not an assignment of exclusive whole-file ownership. The complete contribution schedule also accompanies locally built output in NOTICE.

|ID / exact path|Current SHA256|Treatment / contribution scope|
|---|---|---|
|F01 `src/nanexus_trust/frigate/api_nanexus.py`|`d62bf6f96bfd84d24957082e47f716c8dcde0b1cc78aade8a9b2daaffdd0f79e`|MIT retained relay; Apache-2.0 version edit. M1: retained old relay text; A1: version literal change at current line 65. Retained text inside that line remains M1.|
|F02 `src/nanexus_trust/frigate/api_overlay.py`|`2ece9b3665852d32d324f124fd2c67f33045f27fc405b9cb47931c704a04f897`|Apache-2.0 project wrapper; Frigate MIT excerpts. A2: project hash/render/write logic and Nanexus insertions; exclude U1–U3 and retained repetitions in AUTH_INSERT, IMPORT_INSERT and ROUTER_INSERT.|
|F03 `src/nanexus_trust/frigate/live_frontend_overlay.py`|`eeb3f606288b6596fff71fe99faa0f679e39f28bc7badf41372b2afae033dc7f`|Apache-2.0 project wrapper; Frigate MIT excerpt. A3: project entry-patch/hash/write logic and Nanexus entry import/call; exclude U4 and retained context in ENTRY_INSERT.|
|F04 `src/nanexus_trust/frigate/navigation_overlay.py`|`d78053a3559864d0172a8696380c16c413ce6de596f7c08676777e1ca821becd`|Apache-2.0 project wrapper; Frigate MIT excerpts. A4: project nginx-patch/hash/asset logic and Nanexus markup; exclude U5–U7 and retained context in MAP_INSERT, BODY_INSERT and location replacement strings.|
|F05 `src/nanexus_trust/frigate/live_frontend/web/src/api/live-signing.ts`|`3ac5a35e8ea9d2fc67467550e0f2af00c622caab103a143626682eb396a2620a`|MIT retained unchanged client. M2: whole unchanged signing client. No later code delta is assigned Apache-2.0 by this schedule.|
|F06 `src/nanexus_trust/frigate/live_frontend/web/src/components/signing/SigningMonitor.tsx`|`4543cb7b90057df2d44806c11eb0748df283f8e0bb3f64079136caa63fb9b7ae`|Apache-2.0 project component; prior MIT preserved. A6: project still/status monitor component; any valid earlier MIT permission remains available.|
|F07 `src/nanexus_trust/frigate/live_frontend/web/src/components/signing/SigningStatus.tsx`|`3dbc64fac7238ba6d0f45fc0cc4e578dff833d7a1afa7134d0f705ed0c59bc1c`|MIT retained badge; Apache-2.0 client edits. M3: retained old label/subscription/desktop material; A7: client-injection edits at current lines 3–8,37–42,44–45,48–49,56–60. Retained substrings within edited lines remain M3.|
|F08 `src/nanexus_trust/frigate/live_frontend/web/src/components/signing/monitor-layout.ts`|`a2af261d093442b08299980139cb8163b7e79b15b2d87c4564d9fa9c2872be9a`|Apache-2.0 project layout; prior MIT preserved. A8: project authorized-camera selection, geometry and pagination; any valid earlier MIT permission remains available.|
|F09 `src/nanexus_trust/frigate/live_frontend/web/src/components/signing/monitor-snapshot.ts`|`b25c82e8ec28f3c4c8b515f374dada758355ba0ff3a5dd5f20252eb3c46c73ea`|Apache-2.0 project still loader; prior MIT preserved. A9: project per-mount snapshot session/loader; any valid earlier MIT permission remains available.|
|F10 `src/nanexus_trust/frigate/live_frontend/web/src/components/signing/nanexus-live-entry.tsx`|`1a5acc2ff744216bd869fa3880bfccc7df3c53f5e20eaffe59482e907479e523`|Apache-2.0 project entry; prior MIT preserved. A10: project camera/profile mapping and Live mount adapter; imported native implementations are not claimed as Nanexus code. Any valid earlier MIT permission remains available.|
|F11 `src/nanexus_trust/frigate/navigation_locations.nginx`|`3f370fd5a3f54e85e1ca21e7e031af89110b17eafffd704877a6d0fee1939282`|Apache-2.0 project configuration. A11: project Nanexus proxy-route configuration arrangement; no ownership of nginx software, standard directives or included external files is asserted.|
|F12 `ui/nanexus/historical.js`|`3f7182d7f09c11948ba695b3ef9e60f0aa897937f38dff1eb1c3cf3ca69c8bf4`|Apache-2.0 project Historical view. A12: project Historical DOM/calendar/query/progress/timeline and fallback-grid implementation, including project predecessor UI contributions; no grant over any external implementation is implied.|
|F13 `ui/nanexus/navigation-live.css`|`c24c246f23ee999d6bad22306833a14a4b086c3ec287c43660f1c2f47b6301e2`|Apache-2.0 project navigation CSS; prior MIT preserved. A13: project flyout/Live/theme CSS and source-defined geometry; any valid earlier MIT permission remains available.|
|F14 `ui/nanexus/navigation.css`|`4627d45367e28d4b0b471ebc2561260ade38541aca15fbb207d0a44c1a6151e4`|Apache-2.0 project UI CSS. A14: project navigation/Historical layout and finding styles; system-font references/common syntax are not an exclusive Nanexus property claim.|
|F15 `ui/nanexus/navigation.js`|`fd6ecdc738bb72fc4d6029361ac6424f7f3b3ad479fda2a24ff080a1c11451d9`|Apache-2.0 project navigation; prior MIT preserved. A15: project DOM flyout/mount/lifecycle integration; any valid earlier MIT permission remains available.|

M1–M3 are the retained Nanexus MIT portions. F01 changes only the version literal at current line65; unchanged text in the edited line remains legacy material. F07's client edits are located at current lines3–8,37–42,44–45,48–49,56–60; the old equal blocks and retained substrings inside edited lines keep their applicable MIT terms. F05 is unchanged and remains covered by the retained MIT permission; no nonexistent later delta is invented to assign Apache to it. The old source hashes are recorded in NOTICE.

Any valid earlier MIT or Apache permission remains applicable. The later Apache-2.0 contribution treatment does not extend MIT to all later code or withdraw earlier grants. Earlier source hashes and the retained permission text are recorded in NOTICE.

U1–U7 identify Frigate text at pinned commit `cfa08c56b23d23280c3d0a218e5a43182c97445b`: F02 AUTH_ANCHOR/IMPORT_ANCHOR/ROUTER_ANCHOR from API auth/application source; F03 ENTRY_ANCHOR from web/src/main.tsx; F04 MAP_ANCHOR/LOCATION_ANCHOR/BODY_ANCHOR from native nginx.conf. Retained upstream context in their INSERT/replacement strings remains Frigate MIT even where a literal also contains a Nanexus insertion. No license over Frigate-owned code is issued by Nanexus. Imports, framework conventions, system-font references and standard nginx syntax are not claims of upstream ownership or copied dependency implementation.

## Delivered source versus local outputs

The source checkpoint contains source, vendored source with its licenses, approved tools/templates/tests and documentation. It does not ship compiled Worker/OMS ELF, frontend/Monaco bundles, fonts, upstream logos/icons, official-image layers, archives, operator CA/keys/credentials, recordings/databases or private evidence. A binary package is not source-only because its source exists elsewhere. Operators acquire the pinned official Frigate source/image and dependencies from their origins and build their own outputs.

Fetched and generated runtime outputs retain their own dependency, font, icon, model, binary, copyright and trademark obligations. Before redistributing such outputs, inventory their contents and supply required source, licenses and notices or obtain permissions as applicable. Source permissions alone do not clear compiled-output redistribution. Native Frigate branding remains in the supported local runtime; Nanexus is independent and does not claim Frigate or ONVIF endorsement.

## Source tooling and contribution boundaries

The current Builder imports constants/helpers from `tools/frigate_private_install.py` and `tools/build_historical_worker.py`. Those names do not make the superseded private-install procedure a supported alternative; the source helpers remain required build inputs. Use only the [current source-build guide](frigate-source-build.md).

A local output manifest records its source identity and license records. Replacing a source or notice file requires a newly reviewed output; never relabel another build's acceptance. A documentation-only checkpoint does not independently inherit the accepted technical source's G8 identity.

Private operational configuration, signing keys, credentials, CA material, recordings, databases and build outputs are excluded from this source distribution. Ignore rules do not remove already tracked files or reachable history; contributors must review the actual staged content before sharing it.

## Documentation illustrations

The two README illustrations were generated through ChatGPT at the owner's direction, using actual Frigate + Nanexus interface screenshots as references. Their scenes and verification data are illustrative, not deployment or verification evidence. The distributed copies contain no upper-left Frigate bird logo. This provenance statement does not claim that AI generation clears third-party rights or grants rights to upstream marks. Ordinary interface layout is shown to explain this independent integration; no Frigate or ONVIF endorsement is implied.

The source release does not include upstream runtime artwork/font bundles. The illustration use is distinct from redistribution of locally generated Frigate frontend outputs, whose applicable rights and notices must be assessed separately.
