# Source licensing and distribution boundaries

This factual schedule does not issue a new license, change an existing grant or establish ownership by file location. Keep root [LICENSE](../LICENSE), [NOTICE](../NOTICE) and applicable upstream records. The source-only technical prototype is not yet cleared for publication. `LICENSE.nanexus` is absent; no substitute grant with an assumed scope is created here.

| Source scope | Existing declaration/provenance | Treatment and remaining action |
|---|---|---|
| Nanexus companion/product, tools, docs and tests | Root Apache-2.0 LICENSE and product statement in NOTICE | Apache applies to authorized Nanexus-owned work, subject to path exceptions and third-party portions. Do not infer ownership from a header-free file; final owner-approved scope remains required |
| Integration paths listed below | NOTICE separately expresses MIT for integration additions outside its product Apache declaration | Preserve the existing MIT statement and relevant grants. This candidate path list is a conservative review schedule, not verified ownership or permission to relicense. Owner must confirm exact scope and complete the missing record before publication |
| Eight Toolkit-derived files under src/nanexus_trust/historical_v2/videotrust/ | NOTICE lists original paths and commit678d8135f738a813831898388266842ede90c5e7; builder pins source hashes | Retain Apache and Toolkit attribution. No other Toolkit source is bundled |
| third_party/media-signing-framework/ | ONVIF MIT LICENSE and PROVENANCE, r25.12.6/cf7785ab993c18d921094e8e505c2c34a1350f28; modified TLV/Meson scope recorded | Preserve MIT and modification records; do not call this Nanexus Apache or claim ONVIF endorsement |
| tests/fixtures/frigate-0.18.2-cfa08c5.nginx.conf and frigate-0.18.2-cfa08c5.web-src-main.tsx | Copied Frigate source/configuration; full Frigate MIT text exists in NOTICE | Retain upstream attribution. NOTICE's schema-only applicability wording needs an approved factual extension to these fixtures and copied portions |
| Frigate anchors/schema portions in src/nanexus_trust/frigate/{api_overlay,navigation_overlay,live_frontend_overlay,retained,historical_v2}.py and schema-bearing tests | Upstream copied portions mixed with integration logic | Preserve applicable Frigate MIT; this does not assign the entire mixed file to a new license or relicense upstream |
| New source-build handoff: tools/frigate_source_build.py, tests/test_frigate_source_build.py, docs/frigate-source-build.md and docs/examples/frigate-source/{host.json,frigate.yml} | Existing product/root declaration; no separate file-specific grant established | Include in final owner path schedule. Templates carry no private inputs or test CA |

Integration-addition review paths:

- `src/nanexus_trust/frigate/api_nanexus.py`
- `src/nanexus_trust/frigate/api_overlay.py`
- `src/nanexus_trust/frigate/live_frontend/web/src/api/live-signing.ts`
- `src/nanexus_trust/frigate/live_frontend/web/src/components/signing/SigningMonitor.tsx`
- `src/nanexus_trust/frigate/live_frontend/web/src/components/signing/SigningStatus.tsx`
- `src/nanexus_trust/frigate/live_frontend/web/src/components/signing/monitor-layout.ts`
- `src/nanexus_trust/frigate/live_frontend/web/src/components/signing/monitor-snapshot.ts`
- `src/nanexus_trust/frigate/live_frontend/web/src/components/signing/nanexus-live-entry.tsx`
- `src/nanexus_trust/frigate/live_frontend_overlay.py`
- `src/nanexus_trust/frigate/navigation_locations.nginx`
- `src/nanexus_trust/frigate/navigation_overlay.py`
- `ui/nanexus/historical.js`
- `ui/nanexus/navigation-live.css`
- `ui/nanexus/navigation.css`
- `ui/nanexus/navigation.js`

The current schedule preserves the mixed-license decision. No NOTICE text, root license, ONVIF header or prior MIT grant was removed or replaced. Completing LICENSE.nanexus and the precise owner grant/path map requires separate authorization with established rights; do not fill the gap by declaring every integration file Apache.

## Delivered source versus local outputs

The source candidate contains original/integration source, vendored source with its licenses, approved scripts/templates/tests and documentation. It does not ship locally compiled Worker/OMS ELF, compiled frontend/Monaco bundles, fonts, upstream logos/icons, official-image layers, source archives, operator CA, keys, credentials, recordings, databases or private evidence. A package containing binaries is not source-only merely because their source is elsewhere. The builder's local manifest/acceptance identifies operator-owned bytes, not publisher or legal attestation.

Operators acquire the pinned official Frigate source/image and dependencies from their documented origins. Their local outputs retain applicable Frigate MIT, ONVIF MIT and other code licenses, font/icon attribution and separate trademark conditions. Root Apache does not replace those terms. The existing output license directory is not a complete clearance of every dependency/font/icon or inherited image component. Later output redistribution needs its own exact inventory and permissions; it is not authorized by this guide.

Frigate's native UI branding remains untouched. Nanexus additions use their own N entry and names as an independent integration, with no official endorsement claim. No restricted Frigate graphic is bundled into this source candidate. Pinned upstream policy permits local interface display and compatibility references but separately restricts modified-fork branding; presentation clearance for any eventual public release must be recorded. No replacement logo/font, branded compiled bundle or modified Frigate image is distributed by this work.

## Public-tree review

Ignore rules protect future untracked local evidence/configuration, generated outputs, keys/certificates and runtime data; they do not remove tracked files or reachable history. Review an explicit source/history allowlist before any push. Existing laboratory-only tests, including tests/test_frigate_host_entry.py, must not be run as public default acceptance or supplied with private evidence to make them work. Historical private-image instructions are not the source installation path. No GitHub repository, commit or publication is authorized by these records.
