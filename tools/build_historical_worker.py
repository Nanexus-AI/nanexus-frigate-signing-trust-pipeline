#!/usr/bin/env python3
"""Build the Historical Native Worker from Pipeline-owned source.

OMS headers and the pinned OMS shared library are explicit inputs. This entry
does not install the binary, change source, or select a runtime location.
"""
import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

PIPELINE_ANCESTOR = 'd6f41afc855690187e01f27e0bb87b77380176e6'
COMPILER_RELEASE = '13.3.0-6ubuntu2~24.04.1'
OMS_SHA256 = 'e157fd03c14ba2ae63ce19800cb00d994bc4500a80d84f88492d5537cda76c98'
ONVIF_COMMIT = 'cf7785ab993c18d921094e8e505c2c34a1350f28'
ONVIF_TAG = 'r25.12.6'
REFERENCE_REBUILD_SHA256 = 'd12125f5923f82e6b17d923791b0069ff30820c1381fd405b47f78c0f18e3f47'
ACCEPTED_WORKER_SHA256 = 'cfe8d86efebd749e82e308d86f88316795fcc6828bb205da3d8f2d151e8d4d22'
TOOLKIT_ORIGIN = '678d8135f738a813831898388266842ede90c5e7'
INCLUDE = 'src/nanexus_trust/historical_v2/videotrust/include'

# Byte-identical copies. The first path is the Toolkit path at TOOLKIT_ORIGIN.
MIGRATED = {
    'include/videotrust/historical_observation.hpp': (
        'src/nanexus_trust/historical_v2/videotrust/include/videotrust/historical_observation.hpp',
        '0671952c0e9ad35932f9b98c8005e63e4dfd14295c1ae024ec18f5d71fbfb389'),
    'include/videotrust/session.hpp': (
        'src/nanexus_trust/historical_v2/videotrust/include/videotrust/session.hpp',
        'bee821aec9e3e07347b49fb19ecd910deb42aac37e2bbee2940b771e8896e8bf'),
    'include/videotrust/error.hpp': (
        'src/nanexus_trust/historical_v2/videotrust/include/videotrust/error.hpp',
        'bcb2db1174676ce598bc0655650642930b8c866d5ae8261fe599fb5db1ac065c'),
    'include/videotrust/result.hpp': (
        'src/nanexus_trust/historical_v2/videotrust/include/videotrust/result.hpp',
        'e114e6b275c6dbeff335f23fd4b53babe14cb59ce5de69f8085ac880edd06703'),
    'src/verification/historical_observation.cpp': (
        'src/nanexus_trust/historical_v2/videotrust/historical_observation.cpp',
        'afba316c9d7668ee5c3b0758cfaeef5f89fcb8bbf7b86030ef7c8f1f0cee4db1'),
    'src/verification/historical_reconciliation.hpp': (
        'src/nanexus_trust/historical_v2/videotrust/historical_reconciliation.hpp',
        '9f0a0677e87e31500fa3db5924697df46a06b965fbfca87e5f70718c5aea6820'),
    'src/upstream/session.cpp': (
        'src/nanexus_trust/historical_v2/videotrust/session.cpp',
        '2c9032284d3c4f074f3ec11e5fe011a6adb8dc3d51e6dd10c8b3cfdf313dc45c'),
    'src/core/error.cpp': (
        'src/nanexus_trust/historical_v2/videotrust/error.cpp',
        'b0732f07a76e7728e8ebd33a881d096a23be36217acf0c93ca432c9fb1c52f25'),
}
PIPELINE_SOURCES = {
    'src/nanexus_trust/historical_v2/product_worker_entry.cpp':
        'd7f3b89859fd224a2acb1552b8e877983a050b329fc22d0f871e37f044f3e681',
    'src/nanexus_trust/historical_v2/worker_child.cpp':
        '760c93a44e49aa1dbcfd83d557a5a2013138fe1ee0524aa09a7a484dadf63e62',
    'src/nanexus_trust/historical_v2/worker_updates.hpp':
        'b2284c075fd6d05ef52d918d69297c6c436d69623234c6be074bc99a354627e1',
}
PIPELINE_SOURCES.update({path: digest for path, digest in MIGRATED.values()})
OMS_HEADERS = {
    'onvif_media_signing_common.h': '896b08be4531f8851bb2d93db5c15d24520331daa337de62c199b5f237c2fdf2',
    'onvif_media_signing_signer.h': 'a07464998421c6d93d1f7ab0a1ee41fd6e1749097db6d19313d274dfcdbc4056',
    'onvif_media_signing_validator.h': '5dde31fcf47bf5fb95551f8379089bac0a2e7764ea90e7371d8c92ec6eb586c4',
}
ENTRY = 'src/nanexus_trust/historical_v2/product_worker_entry.cpp'
OBSERVATION = 'src/nanexus_trust/historical_v2/videotrust/historical_observation.cpp'
SESSION = 'src/nanexus_trust/historical_v2/videotrust/session.cpp'
ERROR = 'src/nanexus_trust/historical_v2/videotrust/error.cpp'


class BuildFailure(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def git_output(repo, *args):
    try:
        return subprocess.check_output(['git', '-C', str(repo), *args], text=True, stderr=subprocess.PIPE).strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = exc.stderr.strip() if isinstance(exc, subprocess.CalledProcessError) else str(exc)
        raise BuildFailure('git', f'git {" ".join(args)} failed in {repo}: {detail}') from exc


def compiler_text():
    try:
        return subprocess.check_output(['g++', '--version'], text=True, stderr=subprocess.STDOUT)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise BuildFailure('compiler', f'g++ is unavailable: {exc}') from exc


def inside(root, candidate):
    root = Path(root).resolve()
    candidate = Path(candidate).resolve()
    return candidate == root or root in candidate.parents


def outputs(output):
    output = Path(output)
    if output.exists() and not output.is_dir():
        raise BuildFailure('output', f'build directory must be a directory: {output}')
    parent = output.parent
    if not parent.is_dir():
        raise BuildFailure('output', f'parent of the build directory is missing: {parent}')
    resolved = output.resolve() if output.exists() else parent.resolve() / output.name
    binary = resolved / 'worker_child'
    record = resolved / 'build-record.json'
    for path in (binary, record):
        if not inside(resolved, path):
            raise BuildFailure('output', f'refusing to write outside the build directory: {path}')
        if path.is_symlink() or path.exists():
            raise BuildFailure('output', f'refusing to overwrite {path}')
    return resolved, binary, record


def verify_tree(repo, relative, digest, label):
    path = Path(repo) / relative
    if not path.is_file():
        raise BuildFailure('missing', f'{label} missing: {path}')
    actual = sha256_file(path)
    if actual != digest:
        raise BuildFailure('hash', f'{label} sha256 {actual} != {digest}')
    return path


def verify_oms_header(headers, name, digest):
    path = Path(headers) / name
    if not path.is_file():
        raise BuildFailure('missing', f'OMS header missing: {name}')
    actual = sha256_file(path)
    if actual != digest:
        raise BuildFailure('hash', f'OMS header sha256 {actual} != {digest}')
    return path


def verify(pipeline, oms_headers, oms_library, output):
    pipeline = Path(pipeline).resolve()
    headers = Path(oms_headers)
    library = Path(oms_library)
    if not pipeline.is_dir() or not headers.is_dir():
        raise BuildFailure('missing', 'pipeline and OMS header directories are required')
    resolved, binary, record = outputs(output)
    for banned in (pipeline / 'src', pipeline / '.git'):
        if banned.exists() and inside(banned, resolved):
            raise BuildFailure('output', f'build directory is inside {banned}')
    head = git_output(pipeline, 'rev-parse', 'HEAD')
    if subprocess.call(['git', '-C', str(pipeline), 'merge-base', '--is-ancestor', PIPELINE_ANCESTOR, 'HEAD']) != 0:
        raise BuildFailure('version', f'pipeline HEAD {head} is not {PIPELINE_ANCESTOR} or a descendant')
    compiler = compiler_text()
    if COMPILER_RELEASE not in compiler:
        raise BuildFailure('version', f'g++ is not {COMPILER_RELEASE}')
    sources = {name: verify_tree(pipeline, name, digest, name) for name, digest in PIPELINE_SOURCES.items()}
    checked_headers = {name: verify_oms_header(headers, name, digest) for name, digest in OMS_HEADERS.items()}
    if not library.is_file():
        raise BuildFailure('missing', f'OMS library missing: {library}')
    library_hash = sha256_file(library)
    if library_hash != OMS_SHA256:
        raise BuildFailure('hash', f'OMS library sha256 {library_hash} != {OMS_SHA256}')
    libdir = library.resolve().parent
    command = [
        'g++', '-std=c++20', '-O1', '-Wno-deprecated-declarations',
        '-I', str((pipeline / INCLUDE).resolve()),
        '-I', str(headers.resolve()),
        str(sources[ENTRY]),
        str(sources[OBSERVATION]),
        str(sources[SESSION]),
        str(sources[ERROR]),
        str(library.resolve()),
        '-lcrypto', '-Wl,-z,now', f'-Wl,-rpath,{libdir}',
        '-o', str(binary),
    ]
    return {
        'pipeline_head': head,
        'compiler': compiler.splitlines()[0],
        'command': command,
        'output': resolved,
        'binary': binary,
        'record': record,
        'library_sha256': library_hash,
        'headers': {name: str(path.resolve()) for name, path in checked_headers.items()},
        'sources': {name: sha256_file(path) for name, path in sources.items()},
    }


def dynamic_requirements(binary):
    try:
        text = subprocess.check_output(['readelf', '-d', str(binary)], text=True, stderr=subprocess.PIPE)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise BuildFailure('readelf', f'cannot read dynamic dependencies of {binary}: {exc}') from exc
    needed, runpath = [], ''
    for line in text.splitlines():
        if '(NEEDED)' in line and '[' in line:
            needed.append(line.split('[', 1)[1].split(']', 1)[0])
        elif '(RUNPATH)' in line and '[' in line:
            runpath = line.split('[', 1)[1].split(']', 1)[0]
    if not needed:
        raise BuildFailure('readelf', f'no NEEDED entries in {binary}')
    return needed, runpath


def build(pipeline, oms_headers, oms_library, output):
    plan = verify(pipeline, oms_headers, oms_library, output)
    plan['output'].mkdir(exist_ok=True)
    result = subprocess.run(plan['command'], cwd=pipeline, text=True, capture_output=True, timeout=120)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or '').strip()
        raise BuildFailure('compile', f'g++ exited {result.returncode}: {detail[-2000:]}')
    if not inside(plan['output'], plan['binary']) or not plan['binary'].is_file():
        raise BuildFailure('output', 'compiler did not write the binary inside the build directory')
    needed, runpath = dynamic_requirements(plan['binary'])
    digest = sha256_file(plan['binary'])
    record = {
        'schema': 'nanexus-hv2-worker-build/1',
        'pipeline_head': plan['pipeline_head'],
        'toolkit_origin': {
            'repository': 'nanexus-video-trust-toolkit',
            'commit': TOOLKIT_ORIGIN,
            'license': 'Apache-2.0',
            'copyright': 'Copyright 2026 Nanexus-AI contributors',
            'files': {origin: {'pipeline': pipeline_path, 'sha256': digest}
                      for origin, (pipeline_path, digest) in MIGRATED.items()},
        },
        'onvif_commit': ONVIF_COMMIT,
        'onvif_tag': ONVIF_TAG,
        'compiler': plan['compiler'],
        'compiler_release': COMPILER_RELEASE,
        'sources': plan['sources'],
        'oms_headers': OMS_HEADERS,
        'oms_sha256': plan['library_sha256'],
        'command': plan['command'],
        'binary': plan['binary'].name,
        'binary_sha256': digest,
        'needed': needed,
        'runpath': runpath,
        'reference_rebuild_sha256': REFERENCE_REBUILD_SHA256,
        'matches_reference_rebuild': digest == REFERENCE_REBUILD_SHA256,
        'accepted_worker_sha256': ACCEPTED_WORKER_SHA256,
        'compiler_stderr': result.stderr,
    }
    plan['record'].write_text(json.dumps(record, indent=2) + '\n')
    if not inside(plan['output'], plan['record']):
        raise BuildFailure('output', 'build record escaped the build directory')
    return record


def main(argv):
    parser = argparse.ArgumentParser(description='Build the Historical Native Worker into an explicit directory.')
    parser.add_argument('--pipeline', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--oms-headers', type=Path, required=True)
    parser.add_argument('--oms-library', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        record = build(args.pipeline, args.oms_headers, args.oms_library, args.output)
    except BuildFailure as exc:
        print(f'{exc.code}: {exc}', file=sys.stderr)
        return 2
    print(record['binary_sha256'])
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
