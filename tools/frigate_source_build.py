#!/usr/bin/env python3
"""Pinned, operator-owned source build to external mounts; no runtime lifecycle."""
import argparse
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import stat
import struct
import subprocess
import sys
import tarfile
import uuid

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(TOOLS.parent / 'src'))
import frigate_private_install as private
import build_runtime_pair as pair
import select_runtime_pair as selector
from nanexus_trust.frigate import api_overlay, navigation_overlay, live_frontend_overlay

PROFILE = 'frigate-0182-amd64-local-source-v1'
BASELINE = '9cae6d1d695cd4d7b6e12933bcb3cf868583150c'
VERSION, COMMIT, IMAGE, DIGEST, ARCHIVE = (
    private.VERSION, private.COMMIT, private.IMAGE, private.DIGEST, private.ARCHIVE)
MANIFEST = 'source-build.json'
ACCEPTANCE = 'acceptance.json'
RUN = '#!/command/with-contenv sh\nexec python3 /nanexus/boot.py\n'
TOP = {'web', 'api', 'nginx', 'ui', 'src', 'pair', 'service', 'boot.py', 'service-enable', 'licenses'}
SONAME_PATH = 'pair/lib/' + pair.SONAME
SOURCE_PREFIXES = ('src/', 'ui/', 'tools/', 'third_party/')
NGINX_FILES = {'auth_location.conf', 'auth_request.conf', 'fastcgi.conf', 'fastcgi_params',
               'go2rtc_upstream.conf', 'koi-utf', 'koi-win', 'mime.types', 'nginx.conf',
               'proxy.conf', 'proxy_trusted_headers.conf', 'scgi_params', 'uwsgi_params', 'win-utf'}
ACK = 'I accept this operator-owned local build, not publisher authorization.'


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_json(path):
    def unique(items):
        out = {}
        for key, value in items:
            require(key not in out, 'duplicate JSON key')
            out[key] = value
        return out
    require(Path(path).is_file() and not Path(path).is_symlink(), 'regular JSON file required')
    return json.loads(Path(path).read_text(), object_pairs_hook=unique,
                      parse_constant=lambda _: require(False, 'non-finite JSON value'))


def write_json(path, data):
    with Path(path).open('x') as handle:
        json.dump(data, handle, indent=2)
        handle.write('\n')
    Path(path).chmod(0o600)


def safe_root(value, pipeline, *, fresh=False):
    path = Path(value)
    require(path.is_absolute() and '..' not in path.parts, 'absolute normalized output required')
    require(not any(p.is_symlink() for p in (path, *path.parents)), 'symlink output/ancestor refused')
    path = path.resolve()
    repo = Path(pipeline).resolve()
    require(path != Path('/') and len(path.parts) >= 3, 'unsafe output root')
    require(path != repo and repo not in path.parents and path not in repo.parents,
            'output must be outside checkout and its ancestors')
    require(not any(p.name == '.local' for p in path.parents) and path.name != '.local',
            'private evidence directories refused')
    require(not any(p.name.startswith('nanexus-m1-') for p in (path, *path.parents)),
            'accepted experiment roots refused')
    require(not any(p.name == '.git' for p in (path, *path.parents)), 'Git directory refused')
    require(':' not in str(path) and ',' not in str(path), 'ambiguous mount pathname')
    require(path.parent.is_dir(), 'output parent must already exist')
    if fresh:
        require(not path.exists(), 'fresh output root required')
    else:
        require(path.is_dir(), 'output root missing')
        require(path.stat().st_uid == os.getuid() and stat.S_IMODE(path.stat().st_mode) == 0o700,
                'output root must be operator-owned mode0700')
    return path


def inventory(root):
    """Inventory all directories, bytes and modes; permit exactly the OMS link."""
    root = Path(root)
    require(root.is_dir() and not root.is_symlink(), 'artifact root required')
    require(stat.S_IMODE(root.stat().st_mode) == 0o755, 'artifact root mode')
    result = {}
    for path in sorted(root.rglob('*')):
        rel = path.relative_to(root).as_posix()
        info = path.lstat()
        mode = stat.S_IMODE(info.st_mode)
        if stat.S_ISLNK(info.st_mode):
            require(rel == SONAME_PATH and os.readlink(path) == pair.LIBRARY_NAME,
                    'unexpected symlink or library resolution')
            result[rel] = {'kind': 'symlink', 'target': os.readlink(path)}
        elif stat.S_ISDIR(info.st_mode):
            require(mode == 0o755, 'artifact directory mode: ' + rel)
            result[rel] = {'kind': 'directory', 'mode': mode}
        else:
            require(stat.S_ISREG(info.st_mode), 'special artifact refused')
            expected = 0o755 if rel in ('service/run', 'pair/bin/worker_child') else 0o644
            require(mode == expected, 'artifact file mode: ' + rel)
            result[rel] = {'kind': 'file', 'mode': mode, 'bytes': info.st_size, 'sha256': digest(path)}
    return result


def amd64(path):
    with Path(path).open('rb') as handle:
        header = handle.read(20)
    require(len(header) == 20 and header[:6] == b'\x7fELF\x02\x01'
            and struct.unpack('<H', header[18:20])[0] == 62, 'Linux amd64 ELF required')


class References(HTMLParser):
    def __init__(self):
        super().__init__()
        self.paths = []

    def handle_starttag(self, tag, attrs):
        for key, value in attrs:
            if value and (key == 'src' or tag == 'link' and key == 'href'):
                if value.startswith('/BASE_PATH/'):
                    self.paths.append(value[len('/BASE_PATH/'):].split('?', 1)[0])


def validate_layout(root):
    root = Path(root)
    found = inventory(root)
    require({p.name for p in root.iterdir()} == TOP, 'unexpected or missing artifact group')
    for rel in ('web/index.html', 'web/login.html', 'web/assets/editor.worker.bundle.js',
                'web/assets/yaml.worker.bundle.js', 'nginx/nginx.conf',
                'src/nanexus_trust/frigate/host_entry.py', 'pair/build-manifest.json'):
        require(rel in found and found[rel]['kind'] == 'file' and found[rel]['bytes'] > 0,
                'missing required artifact: ' + rel)
    require(not (root / 'web/BASE_PATH').exists(), 'Monaco relocation missing')
    require({p.name for p in (root / 'nginx').iterdir()} == NGINX_FILES, 'complete canonical nginx tree required')
    require({p.name for p in (root / 'licenses').iterdir()} == {
        'pipeline-LICENSE', 'pipeline-NOTICE', 'frigate-LICENSE', 'frigate-TRADEMARK.md',
        'frigate-branding-LICENSE', 'oms-LICENSE', 'oms-PROVENANCE'}, 'local notice layout')
    for page in ('index.html', 'login.html'):
        parser = References()
        parser.feed((root / 'web' / page).read_text())
        for rel in parser.paths:
            require('..' not in PurePosixPath(rel).parts and (root / 'web' / rel).is_file(),
                    'missing/unsafe frontend reference')
    require({p.name for p in (root / 'api').iterdir()} == set(api_overlay.OUTPUT_NAMES), 'API layout')
    for name, expected in api_overlay.ACCEPTED_SHA256.items():
        require(digest(root / 'api' / name) == expected, 'API overlay identity')
    require({p.name for p in (root / 'ui').iterdir()} == set(navigation_overlay.UI_SHA256), 'UI layout')
    for name, expected in navigation_overlay.UI_SHA256.items():
        require(digest(root / 'ui' / name) == expected, 'UI identity')
    require((root / 'boot.py').read_text() == private.BOOT, 'BOOT identity')
    require({p.name for p in (root / 'service').iterdir()} == {'run', 'type'}, 's6 layout')
    require((root / 'service/run').read_text() == RUN and
            (root / 'service/type').read_text() == 'longrun\n' and
            (root / 'service-enable').read_bytes() == b'', 's6 identity')
    worker, worker_sha, oms_sha = selector.inspect_runtime(root / 'pair')
    amd64(worker)
    amd64(root / 'pair/lib' / pair.LIBRARY_NAME)
    require({p.name for p in (root / 'pair').iterdir()} == {'bin', 'lib', 'build-manifest.json'}, 'pair root layout')
    record = load_json(root / 'pair/build-manifest.json')
    require(record.get('schema') == 'nanexus-hv2-runtime-pair/1'
            and record.get('selected') is False
            and record.get('worker_sha256') == worker_sha and record.get('oms_sha256') == oms_sha,
            'pair build identity')
    return found, {'worker_sha256': worker_sha, 'oms_sha256': oms_sha}


def verify(output, pipeline=TOOLS.parent, *, accepted=False, expected=None):
    output = safe_root(output, pipeline)
    record = load_json(output / MANIFEST)
    require(type(record) is dict, 'manifest object required')
    require(record.get('schema') == 1 and record.get('profile') == PROFILE, 'local profile mismatch')
    frigate = record.get('frigate')
    require(type(frigate) is dict, 'Frigate identity record required')
    identity = check_image([{'Id': frigate.get('image_id'),
                             'RepoDigests': frigate.get('repo_digests'),
                             'Architecture': 'amd64', 'Os': 'linux',
                             'Descriptor': frigate.get('descriptor')}], frigate.get('image'))
    require(frigate == {'version': VERSION, 'commit': COMMIT, 'archive_sha256': ARCHIVE,
                        'image': IMAGE, **identity}, 'pinned Frigate identity mismatch')
    source = record.get('source', {})
    require(type(source) is dict and type(source.get('commit')) is str
            and type(source.get('files')) is dict, 'source record required')
    require(re.fullmatch('[0-9a-f]{40}', source.get('commit', '')) is not None
            and source.get('baseline') == BASELINE and bool(source.get('files')), 'source identity missing')
    require(type(record.get('commands')) is list and bool(record['commands'])
            and type(record.get('tools')) is dict and bool(record['tools']), 'build provenance missing')
    require(type(record.get('frontend_lock_before')) is str
            and type(record.get('runtime_source')) is dict, 'build record malformed')
    require(record.get('frontend_lock_before') == record.get('frontend_lock_after')
            and re.fullmatch('[0-9a-f]{64}', record.get('frontend_lock_before', '')) is not None,
            'frontend lock changed')
    found, identities = validate_layout(output / 'artifacts')
    require(record.get('inventory') == found, 'artifact inventory mismatch')
    require(record.get('pair') == identities, 'local pair identity mismatch')
    for rel, expected_source in record.get('runtime_source', {}).items():
        require(rel.startswith('src/') and '..' not in PurePosixPath(rel).parts
                and digest(output / 'artifacts' / rel) == expected_source, 'runtime source mismatch')
    require(bool(record.get('runtime_source')), 'runtime source provenance missing')
    require(set(record['runtime_source']) == {p for p, v in found.items()
            if p.startswith('src/') and v['kind'] == 'file'}, 'runtime source inventory incomplete')
    require(all(source['files'].get(p) == h for p, h in record['runtime_source'].items()),
            'runtime source not bound to reviewed input')
    manifest_sha = digest(output / MANIFEST)
    if expected is not None:
        require(manifest_sha == expected, 'manifest identity mismatch')
    if accepted:
        acceptance = load_json(output / ACCEPTANCE)
        require(acceptance == {'profile': PROFILE, 'manifest_sha256': manifest_sha, 'acknowledgment': ACK},
                'local build not accepted')
    return manifest_sha


def accept(output, expected, acknowledge, pipeline=TOOLS.parent):
    require(acknowledge is True, 'explicit local acknowledgment required')
    sha = verify(output, pipeline, expected=expected)
    write_json(Path(output) / ACCEPTANCE,
               {'profile': PROFILE, 'manifest_sha256': sha, 'acknowledgment': ACK})
    return sha


def check_archive(path):
    require(Path(path).is_file() and not Path(path).is_symlink() and digest(path) == ARCHIVE,
            'official archive identity mismatch')


def check_image(document, reference=IMAGE):
    """Validate a trusted daemon's exact-reference lookup, not registry attestation.

    Classic stores use a config ID; containerd stores may return an index or
    selected manifest ID. RepoDigests binds the requested name/digest locally.
    Descriptor, when provided, must agree with that lookup. Never equate Id
    universally with either the registry digest or the image-config digest.
    """
    require(reference == IMAGE, 'exact pinned image reference required')
    require(type(document) is list and len(document) == 1 and type(document[0]) is dict,
            'one unambiguous official image inspect record required')
    image = document[0]
    image_id = image.get('Id')
    require(type(image_id) is str and re.fullmatch(r'sha256:[0-9a-f]{64}', image_id) is not None,
            'valid daemon image ID required')
    require(image.get('Architecture') == 'amd64' and image.get('Os') == 'linux'
            and image.get('Variant', '') == '', 'official image platform mismatch')
    repo_digests = image.get('RepoDigests')
    require(type(repo_digests) is list and bool(repo_digests)
            and all(type(value) is str and re.fullmatch(r'[^@\s]+@sha256:[0-9a-f]{64}', value)
                    is not None for value in repo_digests), 'RepoDigests evidence required')
    require(len(set(repo_digests)) == len(repo_digests) and IMAGE in repo_digests,
            'pinned RepoDigest missing or ambiguous')
    descriptor = image.get('Descriptor')
    if descriptor is not None:
        require(type(descriptor) is dict, 'invalid image descriptor')
        media = descriptor.get('mediaType')
        indexes = {'application/vnd.oci.image.index.v1+json',
                   'application/vnd.docker.distribution.manifest.list.v2+json'}
        manifests = {'application/vnd.oci.image.manifest.v1+json',
                     'application/vnd.docker.distribution.manifest.v2+json'}
        require(type(media) is str and media in indexes | manifests
                and descriptor.get('digest') == image_id, 'inconsistent image descriptor/ID')
        declared_platform = descriptor.get('platform')
        if declared_platform is not None:
            require(type(declared_platform) is dict
                    and declared_platform.get('architecture') == 'amd64'
                    and declared_platform.get('os') == 'linux'
                    and declared_platform.get('variant', '') == '', 'descriptor platform mismatch')
        if media in indexes:
            require(image_id == DIGEST, 'index descriptor does not match pinned registry digest')
        elif image_id != DIGEST:
            require(declared_platform is not None, 'selected manifest platform evidence missing')
        # Retain only identity fields, not arbitrary daemon metadata/annotations.
        descriptor = {'mediaType': media, 'digest': image_id,
                      **({'platform': declared_platform} if declared_platform is not None else {})}
    return {'image_id': image_id, 'repo_digests': sorted(repo_digests),
            'descriptor': descriptor, 'platform': 'linux/amd64'}


class Runner:
    def __init__(self):
        self.commands = []

    def run(self, *args, cwd=None):
        argv = [str(x) for x in args]
        result = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, check=False, umask=0o022)
        self.commands.append({'argv': argv, 'cwd': str(cwd) if cwd else None,
                              'exit_code': result.returncode,
                              'stdout_sha256': hashlib.sha256(result.stdout.encode()).hexdigest(),
                              'stderr_sha256': hashlib.sha256(result.stderr.encode()).hexdigest()})
        require(result.returncode == 0, 'command failed: ' + argv[0] + ' (no automatic fallback)')
        return result.stdout.strip()


def reviewed_source(pipeline, expected, runner):
    require(pipeline == TOOLS.parent.resolve(), 'run builder from its reviewed checkout')
    require(re.fullmatch('[0-9a-f]{40}', expected) is not None, 'full reviewed source commit required')
    require(runner.run('git', '-C', pipeline, 'rev-parse', 'HEAD') == expected, 'source commit mismatch')
    runner.run('git', '-C', pipeline, 'merge-base', '--is-ancestor', BASELINE, 'HEAD')
    runner.run('git', '-C', pipeline, 'diff', '--exit-code', 'HEAD', '--', 'src', 'ui', 'tools', 'third_party', 'LICENSE', 'NOTICE')
    untracked = runner.run('git', '-C', pipeline, 'ls-files', '--others', '--exclude-standard', '--',
                           'src', 'ui', 'tools', 'third_party')
    require(not untracked, 'unreviewed build source files')
    paths = runner.run('git', '-C', pipeline, 'ls-files').splitlines()
    paths = [p for p in paths if p.startswith(SOURCE_PREFIXES) or p in ('LICENSE', 'NOTICE')]
    require('tools/frigate_source_build.py' in paths, 'builder must be in reviewed Git commit')
    for rel in paths:
        require(not (pipeline / rel).is_symlink(), 'source symlinks refused')
    return {p: digest(pipeline / p) for p in paths}


def extract_native(work, runner):
    name = 'nanexus-source-extract-' + uuid.uuid4().hex
    container = runner.run('docker', 'create', '--platform', 'linux/amd64', '--name', name, IMAGE)
    try:
        require(re.fullmatch('[0-9a-f]{64}', container) is not None, 'unexpected extraction container ID')
        runner.run('docker', 'cp', container + ':/usr/local/nginx/conf', work / 'nginx')
        runner.run('docker', 'cp', container + ':/opt/frigate/frigate/api', work / 'api')
    finally:
        runner.run('docker', 'rm', name)


def relocate_monaco(dist):
    source = dist / 'BASE_PATH/monacoeditorwork'
    require(source.is_dir() and not source.is_symlink(), 'Monaco output missing')
    for path in source.iterdir():
        require(path.is_file() and not path.is_symlink(), 'unexpected Monaco output')
        target = dist / 'assets' / path.name
        require(not target.exists(), 'Monaco target collision')
        shutil.move(str(path), target)
    require(all((dist / 'assets' / name).is_file() for name in
                ('editor.worker.bundle.js', 'yaml.worker.bundle.js')), 'Monaco workers missing')
    require(not list(source.iterdir()), 'Monaco relocation incomplete')
    # Only this fresh build's generated subtree; never an operator/source directory.
    require(set((dist / 'BASE_PATH').iterdir()) == {source}, 'unexpected BASE_PATH content')
    shutil.rmtree(dist / 'BASE_PATH')


def normalize(root):
    root.chmod(0o755)
    for path in root.rglob('*'):
        if path.is_symlink():
            continue
        rel = path.relative_to(root).as_posix()
        path.chmod(0o755 if path.is_dir() or rel in ('service/run', 'pair/bin/worker_child') else 0o644)


def build(args):
    pipeline = Path(args.pipeline).resolve()
    runner = Runner()
    require(sys.platform == 'linux' and platform.machine() in ('x86_64', 'AMD64'), 'Linux amd64 host required')
    require(hasattr(tarfile, 'data_filter'), 'Python archive data filter required')
    output = safe_root(args.output, pipeline, fresh=True)
    sources = reviewed_source(pipeline, args.source_commit, runner)
    check_archive(args.source_archive)
    image_identity = check_image(json.loads(runner.run('docker', 'image', 'inspect', IMAGE)), IMAGE)
    tools = {name: runner.run(*command) for name, command in {
        'node': ['node', '--version'], 'npm': ['npm', '--version'],
        'gcc': ['gcc', '--version'], 'g++': ['g++', '--version'],
        'meson': ['meson', '--version'], 'ninja': ['ninja', '--version'],
        'openssl_development': ['pkg-config', '--modversion', 'openssl'],
        'readelf': ['readelf', '--version'], 'docker': ['docker', '--version'],
        'git': ['git', '--version']}.items()}
    tools['python'] = sys.version
    require(re.fullmatch(r'v20\.\d+\.\d+', tools['node']) is not None, 'Node 20 required')
    require(all(pair.COMPILER_RELEASE in tools[n] for n in ('gcc', 'g++')), 'exact compiler release required')
    require(int(tools['openssl_development'].split('.')[0]) >= 3, 'OpenSSL development >=3 required')
    output.mkdir(mode=0o700)
    work = output / 'work'
    work.mkdir()
    artifacts = output / 'artifacts'
    artifacts.mkdir()
    with tarfile.open(args.source_archive) as archive:
        archive.extractall(work, filter='data')
    source = work / ('frigate-' + COMMIT)
    web = source / 'web'
    require(web.is_dir() and not (web / 'node_modules').exists() and not (web / 'dist').exists(), 'clean frontend source required')
    extract_native(work, runner)
    # Validate native originals before spending time compiling.
    api_overlay.render_api_overlay((work / 'api/auth.py').read_text(),
                                   (work / 'api/fastapi_app.py').read_text(), VERSION)
    navigation_overlay.render_navigation_overlay((work / 'nginx/nginx.conf').read_text(), VERSION)
    lock = digest(web / 'package-lock.json')
    rendered = live_frontend_overlay.render_live_frontend((web / 'src/main.tsx').read_text(), VERSION, COMMIT)
    for rel, content in rendered.items():
        path = source / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    require((web / 'patches').is_dir(), 'complete upstream postinstall patches required')
    runner.run('npm', 'install', cwd=web)
    require(digest(web / 'package-lock.json') == lock, 'npm changed pinned lock; review required')
    runner.run('npm', 'run', 'build', cwd=web)
    require(digest(web / 'package-lock.json') == lock, 'build changed pinned lock')
    relocate_monaco(web / 'dist')
    shutil.copytree(web / 'dist', artifacts / 'web')
    shutil.copytree(work / 'nginx', artifacts / 'nginx')
    conf = artifacts / 'nginx/nginx.conf'
    conf.write_text(navigation_overlay.render_navigation_overlay(conf.read_text(), VERSION))
    (artifacts / 'api').mkdir()
    for name, content in api_overlay.render_api_overlay((work / 'api/auth.py').read_text(),
                                                        (work / 'api/fastapi_app.py').read_text(), VERSION).items():
        (artifacts / 'api' / name).write_text(content)
    (artifacts / 'ui').mkdir()
    for name, path in navigation_overlay.navigation_assets(pipeline).items():
        shutil.copy2(path, artifacts / 'ui' / name)
    runtime_source = {}
    for rel in sources:
        if rel.startswith('src/') and Path(rel).suffix in ('.py', '.nginx'):
            dest = artifacts / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(pipeline / rel, dest)
            runtime_source[rel] = sources[rel]
    (artifacts / 'boot.py').write_text(private.BOOT)
    (artifacts / 'service').mkdir()
    (artifacts / 'service/run').write_text(RUN)
    (artifacts / 'service/type').write_text('longrun\n')
    (artifacts / 'service-enable').touch()
    runner.run(sys.executable, pipeline / 'tools/build_runtime_pair.py', '--pipeline', pipeline,
               '--output', artifacts / 'pair')
    (artifacts / 'licenses').mkdir()
    for src, name in [(pipeline / 'LICENSE', 'pipeline-LICENSE'), (pipeline / 'NOTICE', 'pipeline-NOTICE'),
                      (source / 'LICENSE', 'frigate-LICENSE'), (source / 'TRADEMARK.md', 'frigate-TRADEMARK.md'),
                      (source / 'web/images/branding/LICENSE', 'frigate-branding-LICENSE'),
                      (pipeline / 'third_party/media-signing-framework/LICENSE', 'oms-LICENSE'),
                      (pipeline / 'third_party/media-signing-framework/PROVENANCE', 'oms-PROVENANCE')]:
        shutil.copyfile(src, artifacts / 'licenses' / name)
    normalize(artifacts)
    files, identities = validate_layout(artifacts)
    # Refuse a source edit while commands were executing, including copied runtime source.
    require(reviewed_source(pipeline, args.source_commit, runner) == sources, 'source changed during build')
    record = {'schema': 1, 'profile': PROFILE,
              'source': {'commit': args.source_commit, 'baseline': BASELINE, 'files': sources},
              'frigate': {'version': VERSION, 'commit': COMMIT, 'archive_sha256': ARCHIVE,
                          'image': IMAGE, **image_identity},
              'tools': tools, 'commands': runner.commands,
              'native_inputs': {str(p.relative_to(work)): digest(p) for folder in ('api', 'nginx')
                                for p in sorted((work / folder).rglob('*')) if p.is_file()},
              'rendered_frontend': {rel: hashlib.sha256(text.encode()).hexdigest()
                                    for rel, text in rendered.items()},
              'frontend_lock_before': lock,
              'frontend_lock_after': digest(web / 'package-lock.json'), 'runtime_source': runtime_source,
              'pair': identities, 'inventory': files}
    write_json(output / MANIFEST, record)
    return verify(output, pipeline)


def parser():
    cli = argparse.ArgumentParser(description=__doc__)
    sub = cli.add_subparsers(dest='action', required=True)
    p = sub.add_parser('build')
    p.add_argument('--pipeline', default=str(TOOLS.parent))
    p.add_argument('--source-commit', required=True)
    p.add_argument('--source-archive', required=True)
    p.add_argument('--output', required=True)
    p = sub.add_parser('verify')
    p.add_argument('--output', required=True)
    p.add_argument('--require-accepted', action='store_true')
    p = sub.add_parser('accept')
    p.add_argument('--output', required=True)
    p.add_argument('--manifest-sha256', required=True)
    p.add_argument('--acknowledge-local-build', action='store_true', help=ACK)
    return cli


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.action == 'build':
            sha = build(args)
        elif args.action == 'accept':
            sha = accept(args.output, args.manifest_sha256, args.acknowledge_local_build)
        else:
            sha = verify(args.output, accepted=args.require_accepted)
        print(sha)
        if args.action == 'build' or args.action == 'verify' and not args.require_accepted:
            print('Integrity checked; not runnable admission. Review and explicitly accept this manifest.')
    except (ValueError, OSError, KeyError, TypeError, tarfile.TarError, pair.BuildFailure,
            selector.SelectionFailure) as exc:
        print('Source handoff failed: ' + str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
