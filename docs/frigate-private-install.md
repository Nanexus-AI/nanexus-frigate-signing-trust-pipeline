# Private Frigate-first prototype

This procedure is only for Pipeline `ac183e6fda6cc6bb9e0211d53c5b8e1202edbac5`, Frigate `0.18.2-cfa08c5`, and image `ghcr.io/blakeblackshear/frigate@sha256:fd60f99ffd4eb6c42360ed4e14953e270586f239309ab3983e55b2e97ffac09a`. It prepares a new private directory and a new disposable Docker target. It does not install into an existing Frigate deployment. Keep that directory, configuration, credentials, binaries and upstream frontend private. Do not use the untracked legacy packaging service.

Prerequisites: Linux, Docker access, operator UID/GID 1000, Python 3.11+ with tarfile's data extraction filter, OpenSSL and readelf. The pinned image must already be available locally. The image provides Python/aiohttp, ffmpeg, ffprobe and s6. UID 1000 is a deliberate prototype constraint, not automatic user provisioning. No signing private key is needed or copied.

## Prepare inputs and build

Keep inputs outside public source and outside accepted evidence directories:

- The exact pinned GitHub source archive, SHA256 `fb5a04196c4be1dd332d4762e692ebb51fb5e949fafc2f3159830d44583ab005` (commit `cfa08c56b23d23280c3d0a218e5a43182c97445b`). Download URL: `https://codeload.github.com/blakeblackshear/frigate/tar.gz/cfa08c56b23d23280c3d0a218e5a43182c97445b`.
- The accepted Task 1 compiled frontend directory and complete `dist-hashes.json`. The helper checks the entire inventory and fixed index, main bundle and Monaco Worker hashes. Reuse these bytes when unchanged; do not rebuild to install or recreate a container.
- The approved portable pair directory: `bin/worker_child`, `lib/libmedia-signing-framework.so.25.12.6` and its SONAME link. The helper copies and selects the pair using the committed selector. It refuses a different Worker or OMS hash.
- A private Frigate YAML with auth and TLS enabled, intended retention and operator camera/stream configuration. The helper copies this file, not an existing database. Copy any additional files referenced by that YAML into the resulting private `config/` before starting.
- A private JSON using the existing `host_entry.py` configuration contract, mode 0600. Preserve its schema, Frigate version, policy, supervision and camera binding fields. Use one canonical playback mapping per Historical camera. RTSP sources must be accessible inside the target container; `127.0.0.1` means that container. Password-bearing RTSP URLs are refused by the existing contract.
- A public signing trust CA file and an approved read-only recordings directory using Frigate's UTC original-file layout. For this experiment, copy approved evidence outside the repository. An actual source must have matching records in this target's authenticated Frigate recordings API. The installer does not import or rewrite recording metadata.

The accepted Worker/OMS builder remains `tools/build_runtime_pair.py`, followed by explicit `tools/select_runtime_pair.py --select`. Do not rebuild the frozen pair for this procedure: a fresh output location can change OMS bytes. A newly built pair is not automatically admitted by this pinned prototype.

The frontend build recipe is the existing `render_live_frontend()` output applied to the pinned source, followed by upstream `npm install` and `npm run build` in `web/`, then the Dockerfile's Monaco step: move `dist/BASE_PATH/monacoeditorwork/*` into `dist/assets/` and remove `dist/BASE_PATH`. The upstream build stage uses Node 20. Task 1 already built and validated the accepted bytes using its matching cached dependency tree; a clean Node 20 install was not repeated. This helper stages the same committed frontend source edits for inspection, but deliberately consumes only that accepted private build. A fresh build with different hashes needs review, not a relaxed hash check.

From a Pipeline checkout containing the pinned baseline commit, run the helper with operator-owned input paths. The helper can live in a later local checkpoint: integration sources are always exported from the exact frozen commit, not the current HEAD:

```sh
python3 tools/frigate_private_install.py prepare \
  --root /absolute/private/new-install \
  --name nanexus-private-example \
  --port 18991 \
  --source-archive /absolute/private/frigate-pinned.tar.gz \
  --frontend /absolute/private/accepted-web-dist \
  --frontend-manifest /absolute/private/dist-hashes.json \
  --pair /absolute/private/approved-pair \
  --frigate-config /absolute/private/frigate.yml \
  --host-config /absolute/private/host.json \
  --recordings /absolute/private/approved-recordings \
  --public-ca /absolute/private/public-ca.pem
```

`prepare` requires a new absolute install root outside the repository. It exports `src/`, `ui/` and `tools/` from the committed Pipeline baseline, rather than taking working-tree integration code. It validates and renders the pinned nginx, API and frontend overlays, checks/copies the cached web bundle, copies the frozen pair, invokes the existing selector, generates a private Live bearer and a 30-day local TLS certificate, and writes the s6 service. If preparation fails, preserve or remove only that new incomplete directory and retry in a new directory; there is no automatic partial-install repair.

The helper preserves operator camera/policy/supervision fields and replaces only infrastructure paths/endpoints in its private copy:

- Frigate API `https://127.0.0.1:8971`, browser origin `https://127.0.0.1:<port>`.
- Historical `127.0.0.1:8766`, Live `127.0.0.1:18774`.
- `/nanexus/private/host.json`, `live.token`, `ca.pem`; `/nanexus/instance.pem` pins the generated public leaf.
- `/nanexus/pair/bin/worker_child`, `/nanexus/recordings`, `/nanexus/state`, `/nanexus/scratch`; ffmpeg/ffprobe from the pinned image.

Generated config and token are mode 0600 under a mode-0700 private directory. The TLS private key remains under the private install root. It is a local transport key, not a signing key. Access is restricted to loopback; the browser will require acceptance of this private self-signed certificate. Do not weaken the Host Entry certificate pin or Frigate authentication. The 30-day certificate must be renewed deliberately for longer experiments; this prototype does not automate certificate renewal.

## Start, use and recreate

```sh
python3 tools/frigate_private_install.py start --root /absolute/private/new-install
docker logs nanexus-private-example
docker inspect nanexus-private-example --format '{{.State.Health.Status}}'
```

The native Frigate entrypoint stays unchanged. A mounted service under Frigate's existing s6 starts Host Entry, drops supplementary groups and root to UID/GID 1000, waits for the pinned authenticated TLS listener, and runs the existing Host Entry. Host Entry owns both loopback listeners and its own reader/Worker lifecycle. s6 supplies its normal process restart behavior; no separate supervisor is introduced. Persistent `state/` and `scratch/` must remain writable by UID 1000. Sources, UI, pair, private configuration and Historical sources are mounted read-only inside the container.

Wait for Docker health and `host-entry-ready; readiness is not a signing verdict.` in private logs. Initial Frigate logs contain the generated admin password: read it privately and never paste complete logs into an issue. Open `https://127.0.0.1:<port>`, sign in natively, then choose Nanexus's Live Monitor or Historical Verification. UNKNOWN is correct when real observations contain no signing evidence. Historical admission uses the caller's Frigate session and allowed cameras; loopback access is not authentication.

Recreate without copying files or executing Host Entry manually:

```sh
python3 tools/frigate_private_install.py recreate --root /absolute/private/new-install
```

This stops/removes only the container with the matching private-root label and starts the same pinned image with the same persistent mounts. `config/` retains Frigate's database and credentials; `media/` retains native files; `tls/` retains the same transport certificate and key. The new container automatically starts non-root Host Entry again. Completed UI findings from earlier sessions are not a promise of in-flight verification resume. The helper refuses an unmanaged container, changed pair bytes or changed frontend inventory.

## Disable and recover native Frigate

```sh
python3 tools/frigate_private_install.py disable --root /absolute/private/new-install
```

This recreates that labelled target using the unmodified pinned image with only the same `config/`, `media/` and `tls/` mounts. No Nanexus API, nginx, frontend, s6 service, bearer environment or Historical mount is included. It does not delete, rewrite, migrate or restore the retained database/recordings. Native Frigate continues its own configured migrations/retention behavior, so configure retention appropriately before importing any disposable fixture. Check native authentication, API, UI, retained metadata and media afterward. There is no version history or automated rollback machinery.

To explicitly re-enable after review, use `recreate` with the same private root. To end the experiment, stop its named disposable container. Do not remove private persistent data while it is needed.

## Layout and limits

`install.json` records the target name/port, baseline and recording-source path. `pipeline/` contains the committed source export. `build/frigate-<commit>/` contains pinned Frigate source plus rendered frontend edits. `web/`, `api/`, `nginx/`, `ui/` are assembled private assets. `pair/`, `private/`, `state/`, `scratch/`, `tls/`, `config/`, `media/`, `boot.py`, `service/` and `service-enable` support the runtime. These are disposable private artifacts, not redistribution packages.

Before an ordinary-user release: resolve the missing `LICENSE.nanexus` notice requirement, qualify clean build dependencies, replace the local certificate/prototype UID constraints with a reviewed operational procedure, supply user-facing configuration and lifecycle/support guidance, and validate real private camera/recording integration. This task does not qualify another Frigate version, claim ACTIVE live evidence, design PKI or publish anything.

Offline helper checks: `python3 -m unittest discover -s tests -p test_frigate_private_install.py -v`. Do not run `tests/test_frigate_host_entry.py` against the accepted demo environment.
