# Nanexus signing trust integration for Frigate

M1 is a **Frigate-first source-only technical prototype** for native Linux amd64 and **Frigate 0.18.2-cfa08c5**. Build Nanexus integration assets locally and mount them into the unchanged official Frigate image. No Nanexus container image or prebuilt frontend/Worker/OMS package is supplied.

Start with the [operator build and installation guide](docs/frigate-source-build.md). It covers prerequisites, pinned source/image acquisition, local build and explicit acceptance, private configuration, all 20 mounts, startup, use and data-preserving recovery. The [support contract](docs/support-matrix.md) separates observed capabilities from pending acceptance.

- **Live Monitor:** supported signing-evidence presence, receipt freshness and observation continuity. ACTIVE does not mean cryptographic validity or certificate trust. Desktop tiles load one still image per camera per mounted session; image refresh/retry and grid video playback are not provided.
- **Historical Verification:** choose an authorized camera and UTC interval, then verify the corresponding original retained signed recordings. Pending/completed recording blocks form one chronological, fixed-width wrapping grid. GREEN/AMBER/RED describe backend findings; execution completion is not an all-VALID result. The grid correction is source-only and awaits build/browser acceptance.
- **Signing trust:** provide one public CA from your signing environment at `nanexus-runtime/trust/ca.pem`, mounted read-only at `/nanexus/trust/ca.pem`. **A single trusted CA certificate is sufficient for the current supported setup.** No CA or signing private key is distributed. A compatible third-party signed source or an optional Nanexus Gateway can supply media; Gateway is not required.

Frigate native branding, authentication and TLS remain intact. Nanexus is an independent integration, not an official or endorsed Frigate product. See [license and source boundaries](docs/source-license-boundaries.md): existing Apache/MIT and third-party notices remain applicable; missing license-scope records and presentation clearance still gate publication. Do not redistribute locally generated branded bundles on the strength of this source guide.

The earlier clean-build/runtime evidence does not qualify this new source candidate. Independent build, actual Historical browser-result flow and clean-operator installation/recovery acceptance remain pending. No production, HA, mobile, other Frigate-version or exported-MP4 verification claim is made. Retained data and native metadata must not be rewritten to make verification succeed.

Use only the focused checks documented in the guide when separately authorized. Do not run broad test discovery or `tests/test_frigate_host_entry.py`; that laboratory test can contact protected services. Older offline, 0.18.0 Review/Export, private-install and private-image documents are historical contracts, not this release's installation path.
