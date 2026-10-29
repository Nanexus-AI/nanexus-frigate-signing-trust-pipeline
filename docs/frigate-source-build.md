# Frigate 0.18.2 source-only developer prototype

This guide builds local components from source and mounts them into the **unchanged official Frigate image**. It does not create an integrated image or patch an existing deployment. Public delivery is source only. Local manifest acceptance means operator-owned build integrity, not publisher authorization, equivalence to private accepted binaries or independent attestation. Licensing/branding/publication gates remain separate; this guide does not clear redistribution of generated outputs.

Supported binding only:

- Pipeline ancestor `9cae6d1d695cd4d7b6e12933bcb3cf868583150c`, plus the reviewed commit containing this handoff.
- Frigate `0.18.2-cfa08c5`, upstream `cfa08c56b23d23280c3d0a218e5a43182c97445b`.
- Official runtime `ghcr.io/blakeblackshear/frigate@sha256:fd60f99ffd4eb6c42360ed4e14953e270586f239309ab3983e55b2e97ffac09a`.
- Official source archive SHA256 `fb5a04196c4be1dd332d4762e692ebb51fb5e949fafc2f3159830d44583ab005`.

No private integrated image, retained compiled frontend/pair, private cache or `.local` experiment is an input. Clean Node20 compilation and runtime use require separate validation; the implementation's offline tests do not establish those results. Do not use the legacy packaging service.

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

Use a full public Git clone containing the paired builder's required ancestry, not a source ZIP or shallow HEAD. The public repository URL and release commit must come from the reviewed source release; no repository is created by this guide. Set `PIPELINE_ROOT` to that clean clone. This tool must itself be tracked in the reviewed commit; an uncommitted implementation checkout is intentionally not build-admitted.

```sh
PIPELINE_ROOT=/absolute/path/to/reviewed-public-clone
cd "$PIPELINE_ROOT"
git status --short
SOURCE_COMMIT=$(git rev-parse HEAD)
# Compare SOURCE_COMMIT with the independently reviewed release commit before continuing.
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
mkdir -m 700 "$RUNTIME_ROOT/private" "$RUNTIME_ROOT/state" "$RUNTIME_ROOT/scratch"
mkdir -p "$RUNTIME_ROOT/config" "$RUNTIME_ROOT/media/recordings" "$RUNTIME_ROOT/tls"
install -m 600 docs/examples/frigate-source/frigate.yml "$RUNTIME_ROOT/config/config.yml"
install -m 600 docs/examples/frigate-source/host.json "$RUNTIME_ROOT/private/host.json"
# Edit both private copies: replace EVERY operator placeholder with authorized values.
# Copy a real PUBLIC verification trust CA for your signer (never its private key):
install -m 600 /absolute/operator/public-verification-ca.pem "$RUNTIME_ROOT/private/ca.pem"
python3 - "$RUNTIME_ROOT/private/live.token" <<'PYCODE'
import os,secrets,sys
fd=os.open(sys.argv[1],os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
with os.fdopen(fd,'w') as f: f.write(secrets.token_urlsafe(48))
PYCODE
openssl req -x509 -newkey rsa:2048 -nodes -days 30 -subj /CN=127.0.0.1 \
  -keyout "$RUNTIME_ROOT/tls/privkey.pem" -out "$RUNTIME_ROOT/tls/fullchain.pem"
chmod 600 "$RUNTIME_ROOT/tls/privkey.pem"
```

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

Use a new independent name/unused loopback HTTPS port; the template browser_origin is18991. No entrypoint/user override, host daemon, bootstrap hook, Docker socket or privileged mode. Use a clean browser profile for the new origin. Full frontend hides base-only stale chunks; native login/public assets/Monaco files must all come from the complete local build. Static asset caching can persist for a year; don't hot-swap builds under old URLs and assume clients refresh.

The shell function below is a documented command for this one target, not a lifecycle installer. Run it only after private placeholders and certificate/CA/media access are correct:

```sh
RUN_NAME=nanexus-source-local
run_integrated() {
  python3 "$PIPELINE_ROOT/tools/frigate_source_build.py" verify \
    --output "$BUILD_OUTPUT" --require-accepted || return
  docker run --platform linux/amd64 -d --name "$RUN_NAME" --restart no --shm-size=256m \
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

Confirm the exact requested official reference, Linux/amd64, native /init, health and UID1000 Host Entry readiness marker. Relate the target image ID to a fresh lookup of that exact reference on the same daemon; do not compare `.Image` directly with the registry digest or assume it is always a configuration digest. Platform-qualified and default inspections can expose different descriptor IDs. The build manifest records the default exact-reference inspection, not a universal container-ID equality. Docker health probes native API, not Nanexus functionality. Internal go2rtc API1984/RTSP8554 must remain consistent; no host publication needed. Open `https://127.0.0.1:18991`, manually accept your transport leaf, log in natively, inspect native UI and choose Live Monitor/Historical Verification. Preserve native session/camera authorization; anonymous static assets are normal, anonymous protected data is not. Native editor should load Monaco workers without404. Verify UI/API agree, not merely index200.

With your authorized real signed stream, record actual current receipt freshness, mapping/session, states/reasons. UNKNOWN/DEGRADED/ACTIVE are observations, not installer targets; Live is structural/freshness policy, not real-time crypto trust. Historical requires matching originals/catalog/CA and a bounded product request that reaches the selected native Worker/OMS; report its true verdict/coverage, not a predetermined expected color. Missing real sample/trust/network is a distinct functional gap. Do not replay old captures or add fault injection.

Enable native recording/detection as in the template, adjusting dimensions only to your actual input. Verify finalized playable recordings and genuine completed CPU detector responses in fresh native stats; a live detector process or initialized inference speed alone is insufficient. No ordinary-user reliability/endurance claim follows from this prototype.

## 9. Stop, restart, recreate and recover without deleting data

Check accepted immutable output and the nginx working-copy comparison before reusing it. Normal stop/start and recreation affect only your named target:

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

For native-only recovery, omit every integration mount/environment, including nginx/API/web/source/pair/s6/Historical:

```sh
docker stop --timeout 30 "$RUN_NAME"
docker rm "$RUN_NAME"
docker run --platform linux/amd64 -d --name "$RUN_NAME" --restart no --shm-size=256m \
  -p 127.0.0.1:18991:8971 \
  --mount "type=bind,src=$RUNTIME_ROOT/config,dst=/config" \
  --mount "type=bind,src=$RUNTIME_ROOT/media,dst=/media/frigate" \
  --mount "type=bind,src=$RUNTIME_ROOT/tls,dst=/etc/letsencrypt/live/frigate" \
  "$FRIGATE_IMAGE"
```

Verify native login/API/UI and retained recording playback. This preserves independent files; it is not DB byte restoration. Native migrations/retention still apply. Keep original evidence outside native writable mounts and do not mount /homeassistant or existing original databases: native prepare has legitimate migration behavior. No cleanup command here deletes your runtime media/config or accepted evidence.

## Common failures and limits

- Wrong archive/source/official image or platform: fail before admission. No version fallback.
- Dirty/untracked build source or helper not in reviewed commit: obtain the reviewed full clone; don't bypass checks. The implementation checkout alone is not public build qualification.
- Node/compiler/dependency/lock failure: stop, retain the partial external root for private diagnosis and seek review. No automatic npm-ci/Node-major switch or admission of a private dist.
- Missing worker/reference, alternate symlink, mode drift or changed bytes: verification fails. Never reseal manually to hide a replacement.
- Missing acceptance: review manifest digest, use explicit accept, then verify with --require-accepted. Existing private fixed-hash installer is a different workflow and remains unchanged.
- Nginx missing include/permission error: complete writable operational copy is required; inspect generated fragments against intended TLS/listen settings. Never relax canonical inventory.
- Host Entry configuration/identity/TLS/permission failure: check placeholders, host/container Worker path translation, both selected hashes, UID1000 modes, readable original media and matching pinned leaf. Do not disable auth/pinning.
- Pair executable blocked or ABI mismatch: inspect host noexec/architecture and actual pinned runtime library resolution. Source identity alone does not qualify arbitrary toolchains.
- Live or Historical unavailable: distinguish native readiness, mapping/network/trust/media/metadata gaps from actual signing result; report honest state.

Focused offline checks only: `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p test_frigate_source_build.py -v`. Do not run broad test discovery or `tests/test_frigate_host_entry.py` against existing services. A separately reviewed clean build/runtime validation remains required before claiming this handoff works end to end.
