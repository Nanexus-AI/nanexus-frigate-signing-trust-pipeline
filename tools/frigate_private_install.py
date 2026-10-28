#!/usr/bin/env python3
"""Private, single-baseline Frigate prototype. No publication or camera discovery."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import uuid

PIPELINE = 'ac183e6fda6cc6bb9e0211d53c5b8e1202edbac5'
COMMIT = 'cfa08c56b23d23280c3d0a218e5a43182c97445b'
VERSION = '0.18.2-cfa08c5'
DIGEST = 'sha256:fd60f99ffd4eb6c42360ed4e14953e270586f239309ab3983e55b2e97ffac09a'
IMAGE = 'ghcr.io/blakeblackshear/frigate@' + DIGEST
ARCHIVE = 'fb5a04196c4be1dd332d4762e692ebb51fb5e949fafc2f3159830d44583ab005'
WORKER = '51680915bac480670266a8386cf648f153e41d257f718c374f6b8ec19ce9f247'
OMS = '37b4b5b68836ecdbdc3ab816d4a0695752fd9fa403c8df13278bf6995a152818'
LABEL = 'io.nanexus.private-root'
# Accepted private build from Task 1, including upstream Monaco relocation.
FRONTEND = {
    'index.html': '7fc3d613569da30ce6bfa443aa8ba63ae834a8b89b4b20d552de246db513f90a',
    'assets/main-IqTY7RQj.js': 'a0113642e0f2cd9b0bc4462a3c96433ce9acf611d45f7a53fe12ef0471a362cb',
    'assets/editor.worker.bundle.js': 'e350695ad538674e18a539dd79a58e3d4877a65ee5edf47ea03649c8bd37a5ea',
    'assets/yaml.worker.bundle.js': '8f36add8ba96c918044d5554cf6986bfd04353a2e63cc9fd5e7f7f1133bdc9f5',
}

BOOT = '''"""Start the existing Host Entry under native s6, dropping root first."""
import os, ssl, sys, time
from urllib.request import urlopen
from urllib.error import HTTPError, URLError
sys.path.insert(0, '/nanexus/src')
os.setgroups([])
os.setgid(1000)
os.setuid(1000)
os.umask(0o077)
context = ssl.create_default_context(cafile='/nanexus/instance.pem')
context.check_hostname = False
for attempt in range(120):
    try:
        urlopen('https://127.0.0.1:8971/api/version', context=context, timeout=1).close()
        break
    except HTTPError as exc:
        if exc.code == 401:
            break
    except (OSError, URLError):
        pass
    time.sleep(1)
else:
    sys.exit('Pinned authenticated Frigate listener unavailable')
from nanexus_trust.frigate.host_entry import main
sys.exit(main(['--config', '/nanexus/private/host.json']))
'''


def run(*args):
    """Run an explicit command without a shell or secret output."""
    return subprocess.check_output([str(x) for x in args], text=True).strip()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def inventory(root):
    return {str(p.relative_to(root)): sha(p) for p in sorted(root.rglob('*')) if p.is_file()}


def check_frontend(dist, manifest):
    require(not dist.is_symlink() and dist.is_dir(), 'frontend directory required')
    require(not any(p.is_symlink() for p in dist.rglob('*')), 'frontend symlinks refused')
    require(inventory(dist) == manifest, 'frontend inventory mismatch')
    require(all(manifest.get(n) == h for n, h in FRONTEND.items()), 'unaccepted private frontend')
    require(not (dist / 'BASE_PATH').exists(), 'Monaco relocation required')


def external_root(value, repo):
    p = Path(value)
    require(p.is_absolute() and not p.is_symlink(), 'absolute non-symlink install root required')
    p = p.resolve()
    require(p != repo and repo not in p.parents and p != Path('/'), 'install outside repository')
    require(':' not in str(p) and ',' not in str(p), 'unsupported mount pathname')
    return p


def prepare(a):
    """Export committed overlays and assemble one persistent private directory."""
    repo = Path(__file__).resolve().parents[1]
    require(run('git', '-C', repo, 'rev-parse', PIPELINE + '^{commit}') == PIPELINE,
            'pipeline baseline unavailable')
    root = external_root(a.root, repo)
    require(os.getuid() == 1000, 'this private prototype requires operator UID 1000')
    require(not root.exists(), 'install root must be new')
    require(a.name.startswith('nanexus-private-') and all(c.isalnum() or c in '-_' for c in a.name),
            'target name must start nanexus-private-')
    require(1024 <= a.port <= 65535, 'invalid loopback port')
    require(sha(a.source_archive) == ARCHIVE, 'pinned upstream archive mismatch')
    manifest = json.loads(Path(a.frontend_manifest).read_text())
    check_frontend(Path(a.frontend), manifest)
    require(sha(Path(a.pair) / 'bin/worker_child') == WORKER, 'approved Worker mismatch')
    require(sha(Path(a.pair) / 'lib/libmedia-signing-framework.so.25.12.6') == OMS, 'approved OMS mismatch')
    recordings = Path(a.recordings).resolve()
    require(recordings.is_dir() and not Path(a.recordings).is_symlink(), 'recording directory required')
    # Refuse access to the accepted demonstration/evidence trees through this helper.
    require(repo not in recordings.parents, 'copy approved evidence outside repository first')
    require(':' not in str(recordings) and ',' not in str(recordings), 'unsupported recording pathname')
    require(run('docker', 'image', 'inspect', IMAGE, '--format', '{{.Id}}') == DIGEST, 'image ID mismatch')
    root.mkdir(mode=0o700)
    committed = subprocess.check_output(['git', '-C', str(repo), 'archive', PIPELINE,
                                         'src', 'ui', 'tools'])
    with tarfile.open(fileobj=io.BytesIO(committed)) as archive:
        archive.extractall(root / 'pipeline', filter='data')
    with tarfile.open(a.source_archive) as archive:
        archive.extractall(root / 'build', filter='data')
    source = root / 'build' / ('frigate-' + COMMIT)
    sys.path.insert(0, str(root / 'pipeline/src'))
    from nanexus_trust.frigate.api_overlay import render_api_overlay
    from nanexus_trust.frigate.navigation_overlay import render_navigation_overlay, navigation_assets
    from nanexus_trust.frigate.live_frontend_overlay import render_live_frontend
    for rel, text in render_live_frontend((source / 'web/src/main.tsx').read_text(), VERSION, COMMIT).items():
        p = source / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    # Source exists for inspection/rebuild; reuse only the previously accepted compiled bytes.
    shutil.copytree(a.frontend, root / 'web')
    (root / 'frontend-manifest.json').write_text(json.dumps(manifest, indent=2))
    scratch_name = 'nanexus-private-extract-' + uuid.uuid4().hex[:12]
    run('docker', 'create', '--name', scratch_name, IMAGE)
    try:
        run('docker', 'cp', scratch_name + ':/usr/local/nginx/conf', root / 'nginx')
        run('docker', 'cp', scratch_name + ':/opt/frigate/frigate/api', root / 'upstream-api')
    finally:
        run('docker', 'rm', scratch_name)
    p = root / 'nginx/nginx.conf'
    p.write_text(render_navigation_overlay(p.read_text(), VERSION))
    api = root / 'upstream-api'
    (root / 'api').mkdir()
    for name, text in render_api_overlay((api / 'auth.py').read_text(), (api / 'fastapi_app.py').read_text(), VERSION).items():
        (root / 'api' / name).write_text(text)
    (root / 'ui').mkdir()
    for name, path in navigation_assets(root / 'pipeline').items():
        shutil.copy2(path, root / 'ui' / name)
    shutil.copytree(a.pair, root / 'pair', symlinks=True)
    for name in ('private', 'state', 'scratch', 'config', 'media', 'tls', 'service'):
        (root / name).mkdir(mode=0o700 if name in ('private', 'state', 'scratch', 'tls') else 0o755)
    require(os.getuid() == 1000, 'this private prototype requires operator UID 1000')
    shutil.copy2(a.frigate_config, root / 'config/config.yml')
    shutil.copy2(a.public_ca, root / 'private/ca.pem')
    import secrets
    (root / 'private/live.token').write_text(secrets.token_urlsafe(48))
    (root / 'private/live.token').chmod(0o600)
    subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '30',
                    '-subj', '/CN=127.0.0.1', '-keyout', str(root / 'tls/privkey.pem'),
                    '-out', str(root / 'tls/fullchain.pem')], check=True, capture_output=True)
    (root / 'tls/privkey.pem').chmod(0o600)
    doc = json.loads(Path(a.host_config).read_text())
    doc.update(api_origin='https://127.0.0.1:8971', browser_origin=f'https://127.0.0.1:{a.port}',
               instance_certificate='/nanexus/instance.pem', token_file='/nanexus/private/live.token',
               ffmpeg='/usr/lib/ffmpeg/8.0/bin/ffmpeg', ffprobe='/usr/lib/ffmpeg/8.0/bin/ffprobe',
               state_root='/nanexus/state', listen={'live_host': '127.0.0.1', 'live_port': 18774,
                                                   'historical_host': '127.0.0.1', 'historical_port': 8766})
    doc['historical'].update(media_root='/nanexus/recordings', scratch_root='/nanexus/scratch',
                             public_ca='/nanexus/private/ca.pem')
    host = root / 'private/host.json'
    host.write_text(json.dumps(doc, indent=2))
    host.chmod(0o600)
    run(sys.executable, root / 'pipeline/tools/select_runtime_pair.py', '--runtime', root / 'pair',
        '--config', host, '--select')
    # Selector checks the actual persistent copy; container path differs only by its mount prefix.
    doc = json.loads(host.read_text())
    doc['historical']['worker'] = '/nanexus/pair/bin/worker_child'
    host.write_text(json.dumps(doc, indent=2))
    (root / 'boot.py').write_text(BOOT)
    (root / 'service/run').write_text('#!/command/with-contenv sh\nexec python3 /nanexus/boot.py\n')
    (root / 'service/run').chmod(0o755)
    (root / 'service/type').write_text('longrun\n')
    (root / 'service-enable').touch()
    settings = {'schema': 1, 'pipeline': PIPELINE, 'image': IMAGE, 'name': a.name,
                'port': a.port, 'recordings': str(recordings)}
    (root / 'install.json').write_text(json.dumps(settings, indent=2))
    print('Prepared private install at ' + str(root))


def owned_container(root, settings):
    p = subprocess.run(['docker', 'container', 'inspect', settings['name']], capture_output=True, text=True)
    if p.returncode:
        # Absence is the only permissible inspect failure; do not mask daemon errors.
        require('No such' in p.stderr, 'container inspection failed')
        return False
    d = json.loads(p.stdout)[0]
    require((d['Config'].get('Labels') or {}).get(LABEL) == str(root), 'refusing unmanaged container')
    return True


def command(root, settings, *, native=False):
    args = ['docker', 'run', '-d', '--name', settings['name'], '--label', LABEL + '=' + str(root),
            '--restart', 'no', '--shm-size=256m', '-p', f'127.0.0.1:{settings["port"]}:8971']
    def mount(src, dest, ro=True):
        args.extend(['--mount', f'type=bind,src={src},dst={dest}' + (',readonly' if ro else '')])
    mount(root / 'config', '/config', False)
    mount(root / 'media', '/media/frigate', False)
    mount(root / 'tls', '/etc/letsencrypt/live/frigate', False)
    if not native:
        args += ['-e', 'NANEXUS_LIVE_UPSTREAM=http://127.0.0.1:18774',
                 '-e', 'NANEXUS_LIVE_TOKEN_FILE=/nanexus/private/live.token']
        for src, dst, ro in [
            (root / 'nginx', '/usr/local/nginx/conf', False), (root / 'web', '/opt/frigate/web', True),
            (root / 'ui', '/nanexus-navigation', True), (root / 'pipeline/src', '/nanexus/src', True),
            (root / 'pair', '/nanexus/pair', True), (root / 'private', '/nanexus/private', True),
            (root / 'state', '/nanexus/state', False), (root / 'scratch', '/nanexus/scratch', False),
            (root / 'tls/fullchain.pem', '/nanexus/instance.pem', True),
            (Path(settings['recordings']), '/nanexus/recordings', True),
            (root / 'boot.py', '/nanexus/boot.py', True),
            (root / 'service', '/etc/s6-overlay/s6-rc.d/nanexus-private', True),
            (root / 'service-enable', '/etc/s6-overlay/s6-rc.d/user/contents.d/nanexus-private', True),
        ]:
            mount(src, dst, ro)
        for n in ('auth.py', 'fastapi_app.py', 'nanexus.py'):
            mount(root / 'api' / n, '/opt/frigate/frigate/api/' + n)
    args.append(IMAGE)
    return args


def lifecycle(a):
    """Recreate only this helper's labelled target; never remove persistent data."""
    repo = Path(__file__).resolve().parents[1]
    root = external_root(a.root, repo)
    s = json.loads((root / 'install.json').read_text())
    require(s.get('schema') == 1 and s.get('pipeline') == PIPELINE and s.get('image') == IMAGE,
            'install identity mismatch')
    require(s['name'].startswith('nanexus-private-'), 'invalid managed target')
    require(run('docker', 'image', 'inspect', IMAGE, '--format', '{{.Id}}') == DIGEST, 'image mismatch')
    if a.action != 'disable':
        check_frontend(root / 'web', json.loads((root / 'frontend-manifest.json').read_text()))
        require(sha(root / 'pair/bin/worker_child') == WORKER, 'Worker changed')
        require(sha(root / 'pair/lib/libmedia-signing-framework.so.25.12.6') == OMS, 'OMS changed')
    exists = owned_container(root, s)
    if exists:
        require(a.action != 'start', 'target exists; use recreate or disable')
        run('docker', 'stop', s['name'])
        run('docker', 'rm', s['name'])
    result = run(*command(root, s, native=a.action == 'disable'))
    print(result)
    print(('Native Frigate' if a.action == 'disable' else 'Integrated Frigate') +
          f' launched at https://127.0.0.1:{s["port"]}; inspect health before use')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    p = sub.add_parser('prepare')
    for name in ('root', 'name', 'source-archive', 'frontend', 'frontend-manifest', 'pair',
                 'frigate-config', 'host-config', 'recordings', 'public-ca'):
        p.add_argument('--' + name, required=True)
    p.add_argument('--port', type=int, default=18991)
    for action in ('start', 'recreate', 'disable'):
        sub.add_parser(action).add_argument('--root', required=True)
    a = parser.parse_args()
    try:
        prepare(a) if a.action == 'prepare' else lifecycle(a)
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        # Do not dump command stderr/configuration, which can contain private material.
        parser.exit(1, f'Private installation failed: {type(exc).__name__}: {exc}\n')


if __name__ == '__main__':
    main()
