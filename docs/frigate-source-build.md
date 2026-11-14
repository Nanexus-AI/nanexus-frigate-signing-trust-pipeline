# Frigate 0.18.2 source-only developer prototype

This guide builds local components from source and mounts them into the **unchanged official Frigate image**. It does not create an integrated image or patch an existing deployment. Public delivery is source only. Local manifest acceptance means operator-owned build integrity, not publisher authorization, equivalence to private accepted binaries or independent attestation. Licensing/branding/publication gates remain separate; this guide does not clear redistribution of generated outputs.

Supported binding only:

- Latest local G8 technical reference commit `e84a7b7cfdbae960558a0582f4bd3cbf7dfee5e5`, with required ancestor `9cae6d1d695cd4d7b6e12933bcb3cf868583150c`. A later documentation/source handoff must name its own reviewed full commit; never guess a release ref.
- Frigate `0.18.2-cfa08c5`, upstream `cfa08c56b23d23280c3d0a218e5a43182c97445b`.
- Official runtime `ghcr.io/blakeblackshear/frigate@sha256:fd60f99ffd4eb6c42360ed4e14953e270586f239309ab3983e55b2e97ffac09a`.
- Official source archive SHA256 `fb5a04196c4be1dd332d4762e692ebb51fb5e949fafc2f3159830d44583ab005`.

No private integrated image, retained compiled frontend/pair, private cache or `.local` experiment is an input. The technical baseline completed an independent Node20 build, local G1–G8 artifact acceptance and native official-image checks. Bounded browser validation demonstrated login/navigation, synthetic Historical wrapping and synthetic camera isolation. D5 exercised real three-camera Live and terminal Historical paths on the prior grid. The restored timeline subsequently passed focused fixture validation, owner visual approval and one independent clean Build/G1–G8 acceptance at that latest source reference. It was not rerun through the full D5 E2E. Independent ordinary-user installation/lifecycle and broader UI coverage remain separate gates. These instructions are a pre-publication handoff, not proof of a complete end-user qualification. Do not use the legacy packaging service.

## 1. Prepare the developer environment

Use native Linux amd64. The prototype runtime account is UID/GID1000; use an operator with that identity, Docker access and a private external workspace. Docker access is administrator-equivalent; obtain it through the host's existing authorized setup, not socket chmod or sandbox bypass. Do not run the entire container with `--user 1000`: native /init starts normally and existing BOOT drops Host Entry privileges.

Administrator-provided Ubuntu24.04 amd64 packages are the initial compiler profile. A representative package installation, performed by your administrator in the build environment, is:

```sh
apt-get install --no-install-recommends \
  gcc g++ gcc-13=13.3.0-6ubuntu2~24.04.1 g++-13=13.3.0-6ubuntu2~24.04.1 \
  libssl-dev pkg-config binutils python3 python3-venv meson ninja-build \
  git curl ca-certificates openssl
```

Package availability is not guaranteed. Both `/usr/bin/gcc` and `/usr/bin/g++` must actually report `13.3.0-6ubuntu2~24.04.1`; installing another compiler on PATH is insufficient for the existing OMS builder. Stop if this profile cannot be supplied; do not silently relax its version check. Obtain Node20 from the official [Node release directory](https://nodejs.org/dist/latest-v20.x/) and Docker from its official Linux installation instructions. No Buildx is needed. The upstream recipe fixes Node major20, not an exact patch/npm release; record actual versions. OMS needs OpenSSL development>=3, Meson>=0.49 (recorded reference1.12.1), Ninja and readelf. Use a dedicated environment if necessary; this tool does not install dependencies or alter system alternatives.

Your administrator supplies Node20 with its npm executable on PATH; this guide does not install or upgrade it. If installed outside the system PATH, set `NODE20_BIN` to that installation's `bin` directory and run `export PATH="$NODE20_BIN:$PATH"` in this shell. Record actual versions, not a developer cache location. No Compose or Buildx is required. Initial acquisition requires authorized access to the source handoff, official Node/package origins, GitHub codeload, GHCR and the npm registry/dependency origins used by upstream postinstall scripts. Offline/private-cache availability is not assumed.

Use the same Bash session for the following commands. Stop on errors and keep operator-created files private; the builder sets its build subprocess umask explicitly to022 for the existing paired layout. Check before building:

```sh
set -e
umask 077
uname -m
id
node --version
npm --version
/usr/bin/gcc --version
/usr/bin/g++ --version
meson --version
ninja --version
pkg-config --modversion openssl
readelf --version
python3 -c 'import sys,tarfile; print(sys.version); assert hasattr(tarfile,"data_filter")'
docker --version
```

Python3.11+ with actual tar data-filter support is required. Allow disk for full upstream source, npm dependencies, compiler intermediates, complete dist, pair and official image; check available disk/RAM before starting. No measured minimum is claimed. Docker operations are limited to official image inspection and a newly created **unstarted** temporary container for native file extraction. The builder never starts a runtime. The native ABI must later be checked against the official image, not inferred from matching filenames.

## 2. Acquire reviewed public sources and official upstream inputs

Use a full Git clone of [Nanexus-AI/nanexus-frigate-signing-trust-pipeline](https://github.com/Nanexus-AI/nanexus-frigate-signing-trust-pipeline) containing the paired builder's required ancestry, not a source ZIP or shallow HEAD. Repository access depends on visibility and your permissions; these commands do not create a repository. Set `PIPELINE_ROOT` to the clean clone. The builder must be tracked in the reviewed commit; an uncommitted implementation checkout is intentionally not build-admitted.

Use the origin below and the **exact full publication-source commit recorded in the approved handoff**. `main` is a moving branch, not an immutable acceptance identity. The full reviewed SHA is supplied alongside the handoff rather than embedded as this document's own self-referential commit hash. The accepted technical build remains sourced from `e84a7b7cfdbae960558a0582f4bd3cbf7dfee5e5`; a documentation/publication checkpoint is distinct and does not independently inherit G8 acceptance. A new operator build records and admits its own manifest. No release tag, compiled package or permission to redistribute generated outputs is implied.

```sh
SOURCE_ORIGIN=https://github.com/Nanexus-AI/nanexus-frigate-signing-trust-pipeline.git
REVIEWED_SOURCE_COMMIT=REPLACE_WITH_REVIEWED_FULL_HANDOFF_COMMIT
PIPELINE_ROOT=/absolute/path/to/fresh-operator-clone
# Supply the reviewed full handoff SHA before running; full clone, no --depth/source ZIP.
git clone --no-local "$SOURCE_ORIGIN" "$PIPELINE_ROOT"
git -C "$PIPELINE_ROOT" checkout --detach "$REVIEWED_SOURCE_COMMIT"
cd "$PIPELINE_ROOT"
test "$(git rev-parse HEAD)" = "$REVIEWED_SOURCE_COMMIT"
test -z "$(git status --porcelain --untracked-files=normal)"
SOURCE_COMMIT=$(git rev-parse HEAD)
FRIGATE_IMAGE=ghcr.io/blakeblackshear/frigate@sha256:fd60f99ffd4eb6c42360ed4e14953e270586f239309ab3983e55b2e97ffac09a
DEVELOPER_ROOT=/absolute/private/developer-workspace
mkdir -p "$DEVELOPER_ROOT/inputs"
chmod 700 "$DEVELOPER_ROOT"
curl --fail --location \
  https://codeload.github.com/blakeblackshear/frigate/tar.gz/cfa08c56b23d23280c3d0a218e5a43182c97445b \
  --output "$DEVELOPER_ROOT/inputs/frigate.tar.gz"
printf '%s  %s\n' fb5a04196c4be1dd332d4762e692ebb51fb5e949fafc2f3159830d44583ab005 \
  "$DEVELOPER_ROOT/inputs/frigate.tar.gz" | sha256sum --check
docker pull --platform linux/amd64 "$FRIGATE_IMAGE"
```

All build/output/runtime roots must be outside the Git checkout, `.local`, Git metadata and accepted evidence/experiment roots. Do not use shared media/config directories as scratch. The archive must be obtained independently; a private reference copy is not the clean-build validation acquisition path.

## 3. Build mount-ready artifacts

Choose a fresh, nonexistent external output with an existing parent. Relevant `src/ui/tools/third_party/LICENSE/NOTICE` inputs must match the reviewed Git commit with no untracked build source. The builder records source hashes, commands/tool versions, upstream lock identity, complete artifact bytes/modes and the sole allowed OMS symlink. It invokes the unchanged paired builder. It does not accept `--frontend` or `--pair` imports.

```sh
BUILD_OUTPUT="$DEVELOPER_ROOT/source-output"
PYTHONDONTWRITEBYTECODE=1 python3 tools/frigate_source_build.py build \
  --pipeline "$PIPELINE_ROOT" --source-commit "$SOURCE_COMMIT" \
  --source-archive "$DEVELOPER_ROOT/inputs/frigate.tar.gz" --output "$BUILD_OUTPUT"
```

The tool renders the existing seven-source frontend patch into the complete upstream web tree, retains upstream postinstall patches, uses `npm install` then `npm run build`, relocates generated Monaco Workers from `dist/BASE_PATH/monacoeditorwork/` into `dist/assets/` and removes only that build's empty BASE_PATH subtree. It refuses a changed lockfile, different Node major or compiler and does not retry with different dependency strategies. Original API/nginx hashes are checked through existing renderers before compilation. The temporary stopped official-image extraction container is removed on copy failure as well as success.

Image identity verification uses a lookup of the **full pinned reference**, requires that exact entry in Docker's RepoDigests, validates Linux/amd64, and checks descriptor/ID/platform consistency when the daemon provides a descriptor. Tags alone or a plausible-looking local ID are insufficient. Missing/mismatched/ambiguous evidence fails closed. `source-build.json` records the daemon-reported `image_id`, `repo_digests` and optional `descriptor` separately from the pinned reference; it does not invent a configuration digest. Classic image stores generally report a configuration ID, while containerd-backed stores may report an index or selected manifest ID. These values need not equal the pinned registry digest. See [Docker inspection](https://docs.docker.com/reference/cli/docker/image/inspect/) and the [OCI image identity terminology](https://github.com/opencontainers/image-spec/blob/main/config.md#imageid).

This trusts the authorized local Docker daemon's reference resolution and metadata; it is not independent registry-content verification or publisher/signature attestation. A missing descriptor is supported for classic Docker only when exact RepoDigest association and platform/ID evidence exist. Additional repository associations can legitimately name the same image. The builder explicitly requests Linux/amd64 for its unstarted extraction container. No registry client or new Docker API minimum is introduced.

`$BUILD_OUTPUT/artifacts/` contains full web, three API files, complete canonical nginx tree, four UI assets, required Python/nginx package source, BOOT, service run/type/enable, portable pair/build-manifest and local license/provenance records. `work/` is build scratch; never mount it or treat it as an artifact input. `source-build.json` is separate from its inventoried artifacts; `acceptance.json` does not exist yet. Failed/incomplete roots cannot be resumed/admitted; diagnose before deliberately choosing a new root. No frozen artifact is overwritten.

## 4. Verify, review and explicitly accept

```sh
python3 tools/frigate_source_build.py verify --output "$BUILD_OUTPUT"
sha256sum "$BUILD_OUTPUT/source-build.json"
```

Review source commit/files, exact Frigate/archive/image identities, commands/tool versions, unchanged dependency lock, pair provenance, every output hash/mode and relative SONAME symlink. Successful verification without acceptance is **not runnable admission**. After deliberate review, copy the exact displayed manifest SHA256 into `REVIEWED_MANIFEST_SHA256`:

```sh
REVIEWED_MANIFEST_SHA256=REPLACE_WITH_REVIEWED_64_HEX_DIGEST
python3 tools/frigate_source_build.py accept --output "$BUILD_OUTPUT" \
  --manifest-sha256 "$REVIEWED_MANIFEST_SHA256" --acknowledge-local-build
python3 tools/frigate_source_build.py verify --output "$BUILD_OUTPUT" --require-accepted
ARTIFACTS="$BUILD_OUTPUT/artifacts"
```

Acceptance creates a separate record and refuses an existing record. Output/manifest changes invalidate acceptance. Do not edit/reseal a manifest to admit replacement binaries or copy someone else's acceptance. Rebuild and explicitly review a new output root instead. Self-generated hashes are not Nanexus publisher approval. Existing runtime pair selection and pre-exec verification remain necessary.

## 5. Prepare independent private runtime inputs

The following examples are **templates**, not valid until operator placeholders are replaced. They are not production defaults. Use one canonical native camera/Live stream binding, matching `source_camera` and `signed_source` in both examples. The Host Entry grid mapping supplies the monitor; do not add ambiguous duplicate source identities. `camera.live.streams` selects the native stream observed by the monitor. Supply a current credential-free signed RTSP endpoint that the container can reach; Host Entry rejects password-bearing URLs. Do not use protected restream services or replay a capture as fresh Live. Native YAML streams and Host Entry direct signing-reader URL must represent the same intended source. The supplied Live policy/supervision values preserve the bounded accepted profile, not a target state.

```sh
RUNTIME_ROOT="$DEVELOPER_ROOT/runtime"
mkdir -m 700 "$RUNTIME_ROOT"
mkdir -m 700 "$RUNTIME_ROOT/private" "$RUNTIME_ROOT/trust" "$RUNTIME_ROOT/state" "$RUNTIME_ROOT/scratch"
mkdir -p "$RUNTIME_ROOT/config" "$RUNTIME_ROOT/media/recordings" "$RUNTIME_ROOT/tls"
install -m 600 docs/examples/frigate-source/frigate.yml "$RUNTIME_ROOT/config/config.yml"
install -m 600 docs/examples/frigate-source/host.json "$RUNTIME_ROOT/private/host.json"
# Edit both private copies: replace EVERY operator placeholder with authorized values.
# Copy a real PUBLIC verification trust CA for your signer (never its private key):
install -m 600 /absolute/operator/public-verification-ca.pem "$RUNTIME_ROOT/trust/ca.pem"
python3 - "$RUNTIME_ROOT/private/live.token" <<'PYCODE'
import os,secrets,sys
fd=os.open(sys.argv[1],os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
with os.fdopen(fd,'w') as f: f.write(secrets.token_urlsafe(48))
PYCODE
openssl req -x509 -newkey rsa:2048 -nodes -days 30 -subj /CN=127.0.0.1 \
  -keyout "$RUNTIME_ROOT/tls/privkey.pem" -out "$RUNTIME_ROOT/tls/fullchain.pem"
chmod 600 "$RUNTIME_ROOT/tls/privkey.pem"
```

**A single trusted CA certificate is sufficient for the current supported setup.** The flow creates `trust/` only; it never creates, downloads or embeds a signing CA. Supply the public CA from the actual signing environment through its operator/vendor's trusted channel. A signing private key stays on the signer. Its signer certificate identifies that signer; the CA is the issuing authority, and the public CA certificate you choose is the verifier's trust anchor. A leaf signer certificate is not automatically a CA trust anchor. Do not copy a laboratory test CA into a public package.

Two source paths use the same verification configuration: an **optional Nanexus Gateway**, with signing/key setup managed separately, or a **third-party standards-compliant signing source** within the supported H.264/evidence subset. The verifier need not run Gateway or obtain vendor-only telemetry. Not every ONVIF device/profile is qualified. Both private templates must identify the same authorized source; Host Entry rejects credential-bearing RTSP URLs.

After supplying the CA, check it as the UID1000 operator before activation:

```sh
test -r "$RUNTIME_ROOT/trust/ca.pem" && test -s "$RUNTIME_ROOT/trust/ca.pem"
openssl x509 -in "$RUNTIME_ROOT/trust/ca.pem" -noout -subject -issuer
python3 - "$RUNTIME_ROOT/trust/ca.pem" <<'PYCODE'
from pathlib import Path
import sys
p=Path(sys.argv[1])
assert not p.is_symlink() and p.is_file()
assert p.read_bytes().count(b'-----BEGIN CERTIFICATE-----') == 1, 'Supply exactly one public CA certificate'
PYCODE
```

These checks establish readability and parseability, not signer-chain compatibility. A missing CA or wrong/symlink path fails shared Host Entry configuration. An existing unreadable or malformed CA can pass the initial file-existence check and fail later during Historical trust reading/admission; `host-entry-ready` does not prove CA usability. Historical trust input is limited to 32768 bytes. Mismatched trust retains its actual operational/verification finding, never a prescribed RED result, manufactured media verdict or fallback test anchor. Live presence/freshness status does not evaluate this CA. Keep `historical.public_ca` at `/nanexus/trust/ca.pem`, with the trust directory mounted read-only.

This is an independent local **transport** key/certificate, not a signing key. Browser acceptance of its self-signed identity is manual; Host Entry pins its public leaf. The prototype certificate expires after30 days; deliberate renewal requires native TLS/Host Entry repinning and remount/restart. Never disable backend pinning/native HTTPS to bypass this. Keep these files/token/native logs private. Host config/token/state/scratch must be owned by UID1000, with directory0700/private files0600. UID1000 must read/traverse Historical originals. No extra user provisioning is provided.

Select the newly built pair explicitly, then translate only the executable path into the container:

```sh
python3 tools/select_runtime_pair.py --runtime "$ARTIFACTS/pair" \
  --config "$RUNTIME_ROOT/private/host.json" --select
python3 - "$RUNTIME_ROOT/private/host.json" <<'PYCODE'
import json,sys
from pathlib import Path
p=Path(sys.argv[1]); d=json.loads(p.read_text())
d['historical']['worker']='/nanexus/pair/bin/worker_child'
p.write_text(json.dumps(d,indent=2)+'\n'); p.chmod(0o600)
PYCODE
```

The selector writes both actual hashes. Preserve them, the versioned library and `libmedia-signing-framework.so.25 -> libmedia-signing-framework.so.25.12.6`; mount the whole pair RO. No LD_LIBRARY_PATH or alternate library directory. Host filesystem must permit executing the mounted binary; existing admission refuses ambiguous resolution/hwcaps. Host administrator controls the backing files: RO container mounts cannot prevent hostile host replacement or eliminate existing TOCTOU limits.

Historical needs originals in UTC `YYYY-MM-DD/HH/<native-camera>/MM.SS.mp4` layout matching this target's authenticated native recordings API. For new independent recordings, use the RO view of `$RUNTIME_ROOT/media/recordings` below. An approved existing external evidence copy may replace that RO source only if it has coherent authorized metadata; copying files does not create native catalog rows. Never mount original retained media or its DB under this target's writable native config/media. No metadata import/forgery or recording rewrite is included.

## 6. Prepare and verify the isolated nginx working copy

```sh
python3 tools/frigate_source_build.py verify --output "$BUILD_OUTPUT" --require-accepted
test ! -e "$RUNTIME_ROOT/nginx" && cp -R "$ARTIFACTS/nginx" "$RUNTIME_ROOT/nginx"
```

The destination must not exist; do not accidentally nest a copy into a previously initialized directory. It must contain every base include/mime file. Native startup rewrites worker_processes and generates base_path.conf/listen.conf. Only this complete operational directory is RW. Canonical `$ARTIFACTS/nginx` remains inventoried and never mounted writable. Entire conf-directory RO would break generation; mounting only a RO nginx.conf can suppress native CPU worker sizing because sed's bind-file rename fails nonfatally.

Before initial start compare the entire copy against canonical. After native startup, use this bounded comparison and manually inspect generated fragments against your configured TLS/listen/base path:

```sh
python3 - "$ARTIFACTS/nginx" "$RUNTIME_ROOT/nginx" <<'PYCODE'
from pathlib import Path
import re,sys
canonical,working=map(Path,sys.argv[1:])
base={p.name for p in canonical.iterdir()}
extra={p.name for p in working.iterdir()}-base
assert extra in (set(), {'base_path.conf','listen.conf'})
for original in canonical.iterdir():
    current=working/original.name
    assert current.is_file() and not current.is_symlink()
    a,b=original.read_bytes(),current.read_bytes()
    if original.name=='nginx.conf' and a!=b:
        match=re.search(rb'worker_processes ([1-4]);',b)
        assert match and b==a.replace(b'worker_processes auto;',match.group(0),1)
    else: assert a==b, original.name
for name in sorted(extra):
    p=working/name; assert p.is_file() and not p.is_symlink()
    print(name, p.read_text())
PYCODE
```

For initialized operation both generated fragments must exist. Confirm listen.conf enables intended HTTPS8971/internal5000, and base_path.conf reflects the intended empty root base path (this guide does not configure ingress prefixes); inspect `nginx -T` privately if necessary. Do not bless arbitrary generated fragment edits. Native regeneration happens on startup; make changes only through deliberate external native configuration. Keep resources fixed across this validation: once auto has been rewritten, restarting the same nginx working copy may retain that count. For a deliberate later resource change, stop the target and materialize a **new** working copy from canonical; never overwrite a live directory or adjust canonical hashes to match operational changes.

## 7. Run the unchanged official image with exact mounts

Use a new independent name/unused loopback HTTPS port; the template browser_origin is18991. If changing that port, change **all three** together: Docker host binding below, private `host.json` browser_origin, and browser/forwarded URL. Keep the same scheme/host/port at the browser because Historical origin/host checks enforce it. The container API origin remains8971; do not change internal listener ports. No entrypoint/user override, host daemon, bootstrap hook, Docker socket or privileged mode. Use a clean browser profile for the new origin. Full frontend hides base-only stale chunks; native login/public assets/Monaco files must all come from the complete local build. Static asset caching can persist for a year; don't hot-swap builds under old URLs and assume clients refresh.

The table and shell function below describe the validated 20-mount layout. `$ARTIFACTS` is build-generated and immutable; `$RUNTIME_ROOT` is the fresh operator-created private runtime. The nginx directory is an operator-created complete copy of the canonical build output, not a writable alias to it.

| Host source | Container destination | Mode | Created by / purpose |
|---|---|---|---|
| `$RUNTIME_ROOT/config` | `/config` | RW | Operator / native configuration and database |
| `$RUNTIME_ROOT/tls` | `/etc/letsencrypt/live/frigate` | RW | Operator / native transport TLS |
| `$ARTIFACTS/service` | `/etc/s6-overlay/s6-rc.d/nanexus-private` | RO | Builder / individual API or s6 integration |
| `$ARTIFACTS/service-enable` | `/etc/s6-overlay/s6-rc.d/user/contents.d/nanexus-private` | RO | Builder / individual API or s6 integration |
| `$RUNTIME_ROOT/media` | `/media/frigate` | RW | Operator / native media store |
| `$ARTIFACTS/ui` | `/nanexus-navigation` | RO | Builder / four Nanexus UI source assets |
| `$ARTIFACTS/boot.py` | `/nanexus/boot.py` | RO | Builder / existing non-root Host startup |
| `$RUNTIME_ROOT/tls/fullchain.pem` | `/nanexus/instance.pem` | RO | Operator / public transport leaf pin |
| `$ARTIFACTS/pair` | `/nanexus/pair` | RO | Builder / accepted local Worker and paired OMS |
| `$RUNTIME_ROOT/private` | `/nanexus/private` | RO | Operator / Host configuration and Live bearer |
| `$RUNTIME_ROOT/media/recordings` | `/nanexus/recordings` | RO | Operator / RO view of the same actual native recordings |
| `$RUNTIME_ROOT/scratch` | `/nanexus/scratch` | RW | Operator / Nanexus scratch |
| `$ARTIFACTS/src` | `/nanexus/src` | RO | Builder / backend source |
| `$RUNTIME_ROOT/state` | `/nanexus/state` | RW | Operator / Nanexus state |
| `$RUNTIME_ROOT/trust` | `/nanexus/trust` | RO | Operator / public CA only, no fabricated CA |
| `$ARTIFACTS/api/auth.py` | `/opt/frigate/frigate/api/auth.py` | RO | Builder / individual API or s6 integration |
| `$ARTIFACTS/api/fastapi_app.py` | `/opt/frigate/frigate/api/fastapi_app.py` | RO | Builder / individual API or s6 integration |
| `$ARTIFACTS/api/nanexus.py` | `/opt/frigate/frigate/api/nanexus.py` | RO | Builder / individual API or s6 integration |
| `$ARTIFACTS/web` | `/opt/frigate/web` | RO | Builder / individual API or s6 integration |
| `$RUNTIME_ROOT/nginx` | `/usr/local/nginx/conf` | RW | Operator copy of build nginx / native generated fragments |

The shell function below is a documented command for this one target, not a lifecycle installer. Run it only after private placeholders and certificate/CA/media access are correct:

```sh
RUN_NAME=nanexus-source-local
run_integrated() {
  python3 "$PIPELINE_ROOT/tools/frigate_source_build.py" verify \
    --output "$BUILD_OUTPUT" --require-accepted || return
  test -r "$RUNTIME_ROOT/trust/ca.pem" && test -s "$RUNTIME_ROOT/trust/ca.pem" || return
  docker run --platform linux/amd64 -d --name "$RUN_NAME" --restart no --shm-size=256m --cpus=4 --memory=4g \
    -p 127.0.0.1:18991:8971 \
    -e NANEXUS_LIVE_UPSTREAM=http://127.0.0.1:18774 \
    -e NANEXUS_LIVE_TOKEN_FILE=/nanexus/private/live.token \
    --mount "type=bind,src=$ARTIFACTS/web,dst=/opt/frigate/web,readonly" \
    --mount "type=bind,src=$ARTIFACTS/api/auth.py,dst=/opt/frigate/frigate/api/auth.py,readonly" \
    --mount "type=bind,src=$ARTIFACTS/api/fastapi_app.py,dst=/opt/frigate/frigate/api/fastapi_app.py,readonly" \
    --mount "type=bind,src=$ARTIFACTS/api/nanexus.py,dst=/opt/frigate/frigate/api/nanexus.py,readonly" \
    --mount "type=bind,src=$RUNTIME_ROOT/nginx,dst=/usr/local/nginx/conf" \
    --mount "type=bind,src=$ARTIFACTS/ui,dst=/nanexus-navigation,readonly" \
    --mount "type=bind,src=$ARTIFACTS/src,dst=/nanexus/src,readonly" \
    --mount "type=bind,src=$ARTIFACTS/boot.py,dst=/nanexus/boot.py,readonly" \
    --mount "type=bind,src=$ARTIFACTS/service,dst=/etc/s6-overlay/s6-rc.d/nanexus-private,readonly" \
    --mount "type=bind,src=$ARTIFACTS/service-enable,dst=/etc/s6-overlay/s6-rc.d/user/contents.d/nanexus-private,readonly" \
    --mount "type=bind,src=$ARTIFACTS/pair,dst=/nanexus/pair,readonly" \
    --mount "type=bind,src=$RUNTIME_ROOT/private,dst=/nanexus/private,readonly" \
    --mount "type=bind,src=$RUNTIME_ROOT/trust,dst=/nanexus/trust,readonly" \
    --mount "type=bind,src=$RUNTIME_ROOT/tls/fullchain.pem,dst=/nanexus/instance.pem,readonly" \
    --mount "type=bind,src=$RUNTIME_ROOT/config,dst=/config" \
    --mount "type=bind,src=$RUNTIME_ROOT/media,dst=/media/frigate" \
    --mount "type=bind,src=$RUNTIME_ROOT/media/recordings,dst=/nanexus/recordings,readonly" \
    --mount "type=bind,src=$RUNTIME_ROOT/tls,dst=/etc/letsencrypt/live/frigate" \
    --mount "type=bind,src=$RUNTIME_ROOT/state,dst=/nanexus/state" \
    --mount "type=bind,src=$RUNTIME_ROOT/scratch,dst=/nanexus/scratch" \
    "$FRIGATE_IMAGE"
}
run_integrated
```

Only the separate nginx copy, independent native config/media/TLS and Nanexus state/scratch are writable. Do not expose a writable alias to artifacts or original external evidence. Runtime nginx writes may be owned by container root; don't recursively chown original data. No complete API/native s6/user directories are hidden. s6 compiles mounted run/type/member before startup; its runtime bookkeeping stays under /run. BOOT waits for native HTTPS and starts Host Entry non-root. No manual Companion/Bridge launch is required.

## 8. Observe actual startup and product behavior

Inspect only this new target. Initial native logs contain the generated admin password; read them privately and do not paste them into issues/evidence. Examples:

```sh
docker inspect --format '{{.Image}} {{.Config.Image}} {{json .Config.Entrypoint}} {{json .State.Health}}' "$RUN_NAME"
docker exec "$RUN_NAME" nginx -t
docker logs --tail 100 "$RUN_NAME"
```

Confirm the exact requested official reference, Linux/amd64, native /init, health and UID1000 Host Entry readiness marker. Relate the target image ID to a fresh lookup of that exact reference on the same daemon; do not compare `.Image` directly with the registry digest or assume it is always a configuration digest. Platform-qualified and default inspections can expose different descriptor IDs. The build manifest records the default exact-reference inspection, not a universal container-ID equality. Docker health probes native API, not Nanexus functionality. Internal go2rtc API1984/RTSP8554 must remain consistent; no host publication needed. Open `https://127.0.0.1:18991` in a browser on the container host, manually review/accept your private transport identity, log in natively, and choose **N → Live Monitor / Historical Verification**. The initial native username is `admin`; retrieve its generated password privately from this target's logs and store it in your operator-controlled private location before removing a container. Do not put passwords/cookies in reports or screenshots. Reuse existing credentials/token/TLS on restart; do not rerun the initial generation commands. Preserve native session/camera authorization; anonymous static assets are normal, anonymous protected data is not. Native editor should load Monaco workers without404. Verify UI/API agree, not merely index200.

### Windows 11 / browser on another machine

The host's `127.0.0.1` is not your Windows computer's loopback. Before starting a human review, obtain explicit approval for a client-local SSH forward using an already authorized SSH identity/host alias. Do not broaden Docker to `0.0.0.0`, create a public proxy, change origin validation, or disable HTTPS. In Windows PowerShell with the existing OpenSSH client:

```powershell
$K11SshAlias = 'REPLACE_WITH_EXISTING_AUTHORIZED_SSH_ALIAS'
# Confirm client port18991 is free; close only your own conflicting session, never an unknown service.
Get-NetTCPConnection -LocalPort 18991 -State Listen -ErrorAction SilentlyContinue
ssh -N -L 127.0.0.1:18991:127.0.0.1:18991 $K11SshAlias
```

Keep that approved forward open and browse `https://127.0.0.1:18991` on Windows. If using a different approved host port, substitute the identical port at both forward ends and in browser_origin/Docker/browser URL. Existing remote tooling does not prove a browser forward exists. Check the presented transport fingerprint against the privately obtained certificate; a certificate error/expiry must be resolved deliberately, not by globally disabling browser validation. Keep native authentication. If no safe access path exists, stop before handing off a running session. This example is a future authorized access step, not a tunnel created by the guide.

### Bounded readiness and camera membership

Allow up to 180 seconds for initial native/Host readiness in an operator test, then stop the named target normally on failure; no automatic restart/repair. Docker health only checks native API. Look for `host-entry-ready; readiness is not a signing verdict.` and inspect UID1000 with:

```sh
docker top "$RUN_NAME" -eo pid,ppid,uid,args
```

Observe an authenticated native profile and Nanexus session/Live availability in the browser's network panel; anonymous protected data must return401/403. Do not export authorization headers/cookies or run the Host Entry CLI as a dry run: it starts readers/listeners and has no --check-only mode.

Tiles come from **authorized, enabled, non-replay native cameras**, matched to one canonical Host camera/stream/context binding each. There is no camera-add control inside Live Monitor. The monitor reads membership when opened; after deliberate configuration/restart, leave/reopen the monitor. Use one live stream per camera for this M1 setup (the entry chooses the first sorted stream label). Empty tiles with disabled cameras are not a signing verdict. See the [single integrated E2E runbook](frigate-e2e-acceptance.md) for the three-camera/operator acceptance gate.

With your authorized real signed stream, record actual current receipt freshness, mapping/session, states/reasons. UNKNOWN/DEGRADED/ACTIVE are observations, not installer targets; Live is structural/freshness policy, not real-time crypto trust. Historical requires matching originals/catalog/CA and a bounded product request that reaches the selected native Worker/OMS; report its true verdict/coverage, not a predetermined expected color. Missing real sample/trust/network is a distinct functional gap. Do not replay old captures or add fault injection.

### First Historical request (later operator acceptance)

After actual native recording is finalized and visible in Frigate's Catalog, open the existing **N → Historical Verification** entry. Choose an authorized camera, date and increasing UTC hour/minute interval of at most30 minutes, then click **Verify once**. The API can represent fractional seconds; the UI's minute controls are not an exact fractional-segment selector. Never change Catalog rows or file timestamps to match a chosen interval.

Results use one time-proportional timeline when query coverage is available. Finding intervals follow whole-file Catalog times clipped to the selected query; background gaps/outside regions are recording metadata, not signing findings or proof of signing coverage. Hover shows the recording identity, status, Catalog start/end and explicit UTC/raw-time provenance. When optional coverage is absent, an explicitly labelled chronological fixed-width wrapping grid preserves the recording results; equal widths in that fallback do not encode duration. Blocks do not navigate or select recordings. Pale gray is pending, green positive scope, amber partial/indeterminate and red integrity-negative, as returned by the backend. Operational failures and unavailable media are not forged cryptographic failures. Cancellation waits for termination; a timed-out submission is not proof it was never accepted, so do not blindly resubmit. Record the actual result rather than seeking a particular color.

The current window UI does not adopt an existing API-created job or promise durable result/evidence export or resume. Its terminal release/expiry concerns Nanexus memory only. Successful execution is not all-VALID signing or complete context: the first prior real API request returned AMBER with105 VERIFIED_SIGNING,15 INDETERMINATE,16 unresolved and zero unprocessed, while origin remained HOLD and continuity/coverage UNKNOWN. Prefix context was missing. That is evidence of a product execution, not an expected result for your source or a new browser acceptance of this candidate.

Enable native recording/detection as in the template, adjusting dimensions only to your actual input. Verify finalized playable recordings and genuine completed CPU detector responses in fresh native stats; a live detector process or initialized inference speed alone is insufficient. No ordinary-user reliability/endurance claim follows from this prototype.

## 9. Stop, restart, recreate and recover without deleting data

Keep native admin credentials and logs private and preserved before removing a container. Native cache and Docker logs are container-local in this 20-mount contract; they are not a new persistent mount. Finalize/check recordings before recreation and capture needed logs separately. Check accepted immutable output and the nginx working-copy comparison before reusing it. Normal stop/start and recreation affect only your named target:

```sh
docker stop --timeout 30 "$RUN_NAME"
python3 tools/frigate_source_build.py verify --output "$BUILD_OUTPUT" --require-accepted
docker start "$RUN_NAME"
# After inspection, recreate using the same independent mounts:
docker stop --timeout 30 "$RUN_NAME"
docker rm "$RUN_NAME"
run_integrated
```

Host Entry/readers/Worker must terminate with native s6 and start again automatically. Keep data, config, certificate/token, selected hashes and working-copy state; do not run docker-exec to launch observers. In-flight Historical resume is not promised. If stop times out or processes remain, record a failure for review; don't call forced termination graceful.

For a configuration update: stop only your named target, privately back up the operator config, edit those copies, retain selected pair hashes/path/trust, check syntax/mapping/UID permissions, verify accepted artifacts and the nginx copy, then start/reopen. Do not edit source, reseal hashes, restore original databases or change policy to obtain a target state. A parse check is not runtime admission.

For native-only recovery, omit every integration mount/environment, including nginx/API/web/source/pair/s6/Historical:

```sh
docker stop --timeout 30 "$RUN_NAME"
docker rm "$RUN_NAME"
docker run --platform linux/amd64 -d --name "$RUN_NAME" --restart no --shm-size=256m --cpus=4 --memory=4g \
  -p 127.0.0.1:18991:8971 \
  --mount "type=bind,src=$RUNTIME_ROOT/config,dst=/config" \
  --mount "type=bind,src=$RUNTIME_ROOT/media,dst=/media/frigate" \
  --mount "type=bind,src=$RUNTIME_ROOT/tls,dst=/etc/letsencrypt/live/frigate" \
  "$FRIGATE_IMAGE"
```

Verify native login/API/UI and retained recording playback. This preserves independent files; it is not DB byte restoration. Native migrations/retention still apply. Keep original evidence outside native writable mounts and do not mount /homeassistant or existing original databases: native prepare has legitimate migration behavior. No cleanup command here deletes your runtime media/config or accepted evidence. For uninstall, retain these operator-owned directories and detach integration mounts/environment as above; there is no automated media/database deletion procedure.

## Common failures and limits

- Wrong archive/source/official image or platform: fail before admission. No version fallback.
- Dirty/untracked build source or helper not in reviewed commit: obtain the reviewed full clone; don't bypass checks. The implementation checkout alone is not public build qualification.
- Node/compiler/dependency/lock failure: stop, retain the partial external root for private diagnosis and seek review. No automatic npm-ci/Node-major switch or admission of a private dist.
- Missing worker/reference, alternate symlink, mode drift or changed bytes: verification fails. Never reseal manually to hide a replacement.
- Missing acceptance: review manifest digest, use explicit accept, then verify with --require-accepted. Existing private fixed-hash installer is a different workflow and remains unchanged.
- Nginx missing include/permission error: complete writable operational copy is required; inspect generated fragments against intended TLS/listen settings. Never relax canonical inventory.
- Missing CA path: Host configuration fails. Unreadable/malformed/oversized CA may fail later when Historical reads or admits trust; check the RO mount, UID1000 access, one PEM and the 32768-byte cap. Wrong but parseable CA retains its actual finding. No signing key/test-root fallback; PEM parsing is not chain compatibility.
- Host Entry configuration/identity/TLS/permission failure: check placeholders, host/container Worker path translation, both selected hashes, UID1000 modes, readable original media and matching pinned leaf. Do not disable auth/pinning.
- Pair executable blocked or ABI mismatch: inspect host noexec/architecture and actual pinned runtime library resolution. Source identity alone does not qualify arbitrary toolchains.
- Live or Historical unavailable: distinguish native readiness, mapping/network/trust/media/metadata gaps from actual signing result; report honest state.

Focused offline checks only: `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p test_frigate_source_build.py -v`. Do not run broad test discovery or `tests/test_frigate_host_entry.py` against existing services. A separately reviewed clean build/runtime validation remains required before claiming this handoff works end to end.

## Source and release boundaries

Read [source license boundaries](source-license-boundaries.md) before sharing source or generated outputs. The current Apache/MIT contribution schedule and complete retained terms are in NOTICE and the linked boundaries document; a separate `LICENSE.nanexus` is not required. Frigate native branding is preserved; Nanexus is an independent integration, not an endorsed fork. Ship source only, not the locally built web/fonts/icons/ELF, official-image layers, credentials, CA, media/DB or private task evidence. Private-image/legacy packaging instructions are not this installation route.

The latest frozen technical reference is committed and locally G8 accepted. D5 completed real three-camera functional E2E on the preceding D4 frontend; the restored Historical timeline received separate focused browser/owner approval and clean-build acceptance, without another real-source E2E. Later source outputs require their own reviewed manifest and G8 acknowledgment; acceptance is not inherited from the frozen technical reference. A reviewed handoff and independent ordinary-user installation/recovery remain necessary before claiming that release qualification. Preserve the old accepted manifest/acceptance; a fresh build needs its own reviewed identities and local acknowledgment. Native cleanup warnings and unresolved R2 failure-isolation limits remain documented in the [support contract](support-matrix.md); no HA/production guarantee follows.
