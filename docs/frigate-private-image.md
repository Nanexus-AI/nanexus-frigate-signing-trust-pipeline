# Private integrated Frigate image

This is a single Linux amd64 private prototype for Pipeline checkpoint `827171b7c33d0d56e70778a80381635d39e00e0f` and Frigate `0.18.2-cfa08c5`. The base is exactly `ghcr.io/blakeblackshear/frigate@sha256:fd60f99ffd4eb6c42360ed4e14953e270586f239309ab3983e55b2e97ffac09a`. It is not a public image or a new installation framework.

## Build from retained bytes

Read the retained artifact README and inventory first. From the repository root:

```sh
(cd .local/artifacts/frigate-0.18.2-cfa08c5-nanexus-827171b7 && sha256sum --check --quiet SHA256SUMS)
docker buildx build --builder default --platform linux/amd64 \
  --pull=false --network=none --load \
  --tag nanexus-frigate:private-m1-827171b7 \
  --file docker/frigate-private.Dockerfile \
  .local/artifacts/frigate-0.18.2-cfa08c5-nanexus-827171b7
```

The verified retained directory is the build context, never a private runtime directory or the repository root. The Dockerfile copies only the accepted web/API/nginx/UI assets, Python source, portable pair, startup files and existing notices. It performs no RUN step, package install or frontend/Worker/OMS build. The pinned base must be cached locally. `--load` places the result in the local image store; it does not push it. The local image name is not a release tag. No credentials, operator configuration, trust anchors, certificates, keys, recordings, databases or writable state are baked in.

Native `/init`, user, command and healthcheck remain inherited. Existing s6 launches the unchanged boot code, which drops to UID/GID 1000 before running Host Entry. Pair hashes and the relative OMS symlink remain unchanged. Native nginx may rewrite worker_processes during startup and generate its normal configuration fragments.

## External runtime contract

Keep a new private runtime root outside the repository and build context. Use the existing Host Entry configuration contract and pair-selection mechanism described in [the private install procedure](frigate-private-install.md). Select the retained `pair/` with the existing `tools/select_runtime_pair.py --select` against the private config. As in the accepted installer, translate only its selected executable path to `/nanexus/pair/bin/worker_child` for the container; preserve both selected hashes. Do not substitute the legacy pair or change identity admission.

Prepare these operator-owned files/directories before starting:

| Host path under private root | Container path | Access/purpose |
| --- | --- | --- |
| `config/` | `/config` | Writable native Frigate configuration/database; auth and TLS enabled |
| `media/` | `/media/frigate` | Writable native Frigate recordings, with intended retention |
| `tls/` | `/etc/letsencrypt/live/frigate` | External native TLS `fullchain.pem` and `privkey.pem` |
| `tls/fullchain.pem` | `/nanexus/instance.pem` | Read-only public leaf pinned by Host Entry |
| `private/` | `/nanexus/private` | Read-only `host.json`, `live.token`, public signing `ca.pem` |
| `recordings/` | `/nanexus/recordings` | Read-only approved Historical source matching this Frigate API's metadata |
| `state/`, `scratch/` | `/nanexus/state`, `/nanexus/scratch` | Writable by UID/GID 1000 |

Use mode 0700 for private/state/scratch directories and 0600 for host.json, live.token and the TLS key. Keep the token in a file, not a command-line argument. Public signing trust material is operator input; no signing private key is required. Keep the certificate and native database stable across recreation. The validated prototype uses a private self-signed transport leaf, explicitly accepted by the browser and pinned by Host Entry; do not disable the backend pin or native authentication. Follow the existing private procedure for certificate lifetime/renewal limitations.

Host Entry paths are `/nanexus/private/host.json`, `/nanexus/private/live.token`, `/nanexus/private/ca.pem`, `/nanexus/instance.pem`, `/nanexus/pair/bin/worker_child`, `/nanexus/recordings`, `/nanexus/state` and `/nanexus/scratch`. Use `/usr/lib/ffmpeg/8.0/bin/{ffmpeg,ffprobe}`. Native API origin is `https://127.0.0.1:8971`; Historical listens on `127.0.0.1:8766`, Live on `127.0.0.1:18774`. Set browser origin to the exact externally used loopback URL, including port. Use one canonical mapping per Historical camera. RTSP sources must be reachable inside the container. The image supplies only the fixed relay endpoint/token-file paths through ENV, never a token value.

For example, after preparing private inputs:

```sh
PRIVATE_ROOT=/absolute/private/image-runtime
docker run -d --name nanexus-private-image-example --restart no --shm-size=256m \
  -p 127.0.0.1:19001:8971 \
  --mount type=bind,src="$PRIVATE_ROOT/config",dst=/config \
  --mount type=bind,src="$PRIVATE_ROOT/media",dst=/media/frigate \
  --mount type=bind,src="$PRIVATE_ROOT/tls",dst=/etc/letsencrypt/live/frigate \
  --mount type=bind,src="$PRIVATE_ROOT/tls/fullchain.pem",dst=/nanexus/instance.pem,readonly \
  --mount type=bind,src="$PRIVATE_ROOT/private",dst=/nanexus/private,readonly \
  --mount type=bind,src="$PRIVATE_ROOT/recordings",dst=/nanexus/recordings,readonly \
  --mount type=bind,src="$PRIVATE_ROOT/state",dst=/nanexus/state \
  --mount type=bind,src="$PRIVATE_ROOT/scratch",dst=/nanexus/scratch \
  nanexus-frigate:private-m1-827171b7
```

Wait for native Docker health and `host-entry-ready; readiness is not a signing verdict.` in private logs. Initial logs contain the native generated admin password; do not publish them. Open `https://127.0.0.1:19001`, sign in natively and select Nanexus Live Monitor or Historical Verification. UNKNOWN is a valid connected state when observations contain no signing evidence. Historical uses the caller's native session and camera authorization.

To recreate, stop and remove only this disposable container, then repeat the same docker run with the same private mounts and recorded local image ID. Do not recopy source, regenerate credentials/certificates, delete the private root or run docker exec to launch Host Entry. Native s6 starts it automatically again. In-flight verification resume is not promised.

## Return to native pinned Frigate

Stop/remove only the disposable integrated target, then start the original pinned image against the same native data mounts:

```sh
docker stop nanexus-private-image-example
docker rm nanexus-private-image-example
docker run -d --name nanexus-private-image-example --restart no --shm-size=256m \
  -p 127.0.0.1:19001:8971 \
  --mount type=bind,src="$PRIVATE_ROOT/config",dst=/config \
  --mount type=bind,src="$PRIVATE_ROOT/media",dst=/media/frigate \
  --mount type=bind,src="$PRIVATE_ROOT/tls",dst=/etc/letsencrypt/live/frigate \
  ghcr.io/blakeblackshear/frigate@sha256:fd60f99ffd4eb6c42360ed4e14953e270586f239309ab3983e55b2e97ffac09a
```

No Nanexus service, relay environment, code overlay or Historical mount accompanies native recovery. Nothing in this procedure removes or rewrites retained media/database files. Native Frigate still applies its configured retention and startup behavior, so retain approved fixtures appropriately before testing. Verify authentication, native UI/API and retained recording metadata afterward.

## Distribution limits

This remains private and version-specific. LICENSE.nanexus is unresolved; Frigate branding and all applicable third-party distribution/license obligations need review before any user-facing image distribution. The image reuses the accepted Node 22/cached-dependency frontend, not a newly qualified clean Node 20 build. Ordinary configuration guidance, supported identity/permissions, certificate operation, real-camera validation and release/security/support qualification remain open. Do not push or publish this prototype.
