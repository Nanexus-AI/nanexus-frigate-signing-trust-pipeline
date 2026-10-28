#!/usr/bin/env python3
"""Build one private Historical Worker and OMS library pair.

The pair uses the committed Pipeline sources. Its RUNPATH is ``$ORIGIN/../lib``.
This command does not select the pair, install it, or change the accepted
Worker and OMS identity constants.

Installed executable and library directories must be owned and controlled by
the installer or administrator, not writable by the Nanexus runtime service
account. This command does not perform that installation. Writable state,
scratch space, and logs stay outside ``bin/`` and ``lib/``.
"""
import argparse
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

from build_historical_worker import (
    ACCEPTED_WORKER_SHA256, COMPILER_RELEASE, ERROR, ENTRY, INCLUDE, OBSERVATION,
    OMS_HEADERS, OMS_SHA256, ONVIF_COMMIT, ONVIF_TAG, PIPELINE_ANCESTOR,
    PIPELINE_SOURCES, SESSION, TOOLKIT_ORIGIN, inside, sha256_file)

OMS_ROOT = 'third_party/media-signing-framework'
OMS_INCLUDE = OMS_ROOT + '/lib/src/includes'
SONAME = 'libmedia-signing-framework.so.25'
LIBRARY_NAME = 'libmedia-signing-framework.so.25.12.6'
RUNPATH = '$ORIGIN/../lib'
INSTALLATION = (
    'Installed executable/library directories must be owned and controlled by '
    'the installer or administrator, not writable by the Nanexus runtime '
    'service account.'
)
OMS_FILES = {
    'LICENSE': 'e98749974d7173d5d821a2e305791ab38d9885e28927d8b5b5b9403285a2adec',
    'meson.build': '53e13053c969c897964554039643f17077b31a6eb6879c7a77fa095a77c3f402',
    'meson_options.txt': '533c49dd3904ac4e7d2353d77f70722f6dee1c7de9e8d2598b9d4c6eca182fa7',
    'lib/meson.build': '665b65d306846a53b40bb2dc42ebac6c19c67217aa74526b9fcb16faca8423ce',
    'lib/plugins/meson.build': '8e323de2a7a2f6cd66886db978f42af553a5e1def66196152d759d34b45772d7',
    'lib/plugins/unthreaded-signing/meson.build':
        '509ce2bc3775c7a6a9b9c60b61884a0b13c80507a979a344f27175b3258243f4',
    'lib/plugins/unthreaded-signing/plugin.c':
        '715948fd96272f8c874c699ade253cf210cf24de268cd5b784b54cf8923a79ba',
    'lib/src/meson.build': '75f26132c11d02f1589e0418bdf86230c255769b585aec0c23e57a856f86153e',
    'lib/src/includes/onvif_media_signing_common.h':
        '896b08be4531f8851bb2d93db5c15d24520331daa337de62c199b5f237c2fdf2',
    'lib/src/includes/onvif_media_signing_helpers.h':
        'b39a0dc4fc23490cca2caaad4813e3fb9a40d490ba235bde581cbf1648dc6149',
    'lib/src/includes/onvif_media_signing_plugin.h':
        'b8367f248ca5743c3a1e3b55b591de9b9c582f8b55fd6f1e7058f9f2c1cb74db',
    'lib/src/includes/onvif_media_signing_signer.h':
        'a07464998421c6d93d1f7ab0a1ee41fd6e1749097db6d19313d274dfcdbc4056',
    'lib/src/includes/onvif_media_signing_validator.h':
        '5dde31fcf47bf5fb95551f8379089bac0a2e7764ea90e7371d8c92ec6eb586c4',
    'lib/src/oms_authenticity_report.c':
        'e006923981af4c2bd06e203a1345034469403ec0e31f76b1708b83e007da0a86',
    'lib/src/oms_authenticity_report.h':
        'a24ec666a9408699c61a158b9fbde70462845f72f72894a0d2b0f891b765b922',
    'lib/src/oms_common.c': 'e77251edafdc0daa21ea2eaff1e908fa7396fd18c9c1007136d7768425e572d4',
    'lib/src/oms_defines.h': '890542586f01a55d4609b482d18fb972d02d6eb0db5a237b917761a81ab37e88',
    'lib/src/oms_helpers.c': 'dda37819a652a9c9c027f8b6a6c8fdc37d628e30e4fcff15ce471945e61048ea',
    'lib/src/oms_internal.h': '5b2807cb99efe2494f3ef93fa7e2214515b5e929ef23e6c06ee1ad7cdda6f821',
    'lib/src/oms_nalu_list.c': 'db2473cf9d48a55ce1274b6db3061dcb2b10d8dd8a7d3b1cb90b69ee67e999c7',
    'lib/src/oms_nalu_list.h': '5c0fde9ceed16eee481fe97913c09f44803fe6e0f978e761f27b7b21979245fa',
    'lib/src/oms_openssl.c': 'd0cf9ef9e9723c4f2552b3ba82780ea218b19c816e396206aca8ec877be050e3',
    'lib/src/oms_openssl_internal.h':
        'b0ac536b25acb1f2d3ad9de4d5b8d50993a6c1042e3552f7e649cb625ab4f44a',
    'lib/src/oms_signer.c': '46b191c97407ce71e034efa7c7a6819a0f508763c795b5752d97ba12b6f81188',
    'lib/src/oms_tlv.c': '4569868d43c39ec189e6792b53c671abcdcd4714ea40e9b200257c847884b45d',
    'lib/src/oms_tlv.h': '9b2c08482988ccb66a3fb38aee9c2479d1f9f0123aeaaae01510fb02435d52b1',
    'lib/src/oms_validator.c': '78be9ce6f577ac015a7dd09b179f7d144612b37dc2434d93d1ba0d9358240477',
}
OMS_UNITS = {
    'lib/plugins/unthreaded-signing/plugin.c',
    'lib/src/oms_authenticity_report.c',
    'lib/src/oms_common.c',
    'lib/src/oms_helpers.c',
    'lib/src/oms_nalu_list.c',
    'lib/src/oms_openssl.c',
    'lib/src/oms_signer.c',
    'lib/src/oms_tlv.c',
    'lib/src/oms_validator.c',
}
ACCEPTED_PATHS = (
    'worker_child',
    'bin/worker_child',
    LIBRARY_NAME,
    'lib/' + LIBRARY_NAME,
)


class BuildFailure(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def git_output(repo, *args):
    try:
        return subprocess.check_output(
            ['git', '-C', str(repo), *args], text=True, stderr=subprocess.PIPE).strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = exc.stderr.strip() if isinstance(exc, subprocess.CalledProcessError) else str(exc)
        raise BuildFailure('git', f'git {" ".join(args)} failed in {repo}: {detail}') from exc


def tool_text(name):
    try:
        return subprocess.check_output([name, '--version'], text=True, stderr=subprocess.STDOUT)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise BuildFailure('compiler', f'{name} is unavailable: {exc}') from exc


def mode_of(path):
    return stat.S_IMODE(path.lstat().st_mode)


def require_mode(path, expected, kind):
    info = path.lstat()
    if kind == 'dir' and (stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode)):
        raise BuildFailure('layout', f'{path.name} is not a real directory')
    if kind == 'file' and (stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode)):
        raise BuildFailure('layout', f'{path.name} is not a regular file')
    if mode_of(path) != expected:
        raise BuildFailure('layout', f'{path.name} mode is {mode_of(path):04o}, expected {expected:04o}')


def parse_dynamic(text):
    needed, runpath, rpath, soname = [], [], [], []
    for line in text.splitlines():
        if '[' not in line or ']' not in line:
            continue
        value = line.split('[', 1)[1].split(']', 1)[0]
        if '(NEEDED)' in line:
            needed.append(value)
        elif '(RUNPATH)' in line:
            runpath.append(value)
        elif '(RPATH)' in line:
            rpath.append(value)
        elif '(SONAME)' in line:
            soname.append(value)
    return {'needed': needed, 'runpath': runpath, 'rpath': rpath, 'soname': soname}


def read_dynamic(path):
    try:
        text = subprocess.check_output(['readelf', '-d', str(path)], text=True, stderr=subprocess.PIPE)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise BuildFailure('readelf', f'cannot read dynamic tags of {path.name}: {exc}') from exc
    return parse_dynamic(text)


def require_worker_dynamic(info):
    if info['rpath'] or info['runpath'] != [RUNPATH]:
        raise BuildFailure('runpath', f'worker DT_RUNPATH is {info["runpath"] or info["rpath"]!r}')
    if info['needed'].count(SONAME) != 1 or 'libcrypto.so.3' not in info['needed'] or 'libc.so.6' not in info['needed']:
        raise BuildFailure('needed', f'worker DT_NEEDED is {info["needed"]}')


def require_library_dynamic(info):
    if info['soname'] != [SONAME]:
        raise BuildFailure('soname', f'library SONAME is {info["soname"]}')
    if info['needed'] != ['libcrypto.so.3', 'libc.so.6']:
        raise BuildFailure('needed', f'library DT_NEEDED is {info["needed"]}')


def require_present(path):
    if not os.path.lexists(path):
        raise BuildFailure('layout', f'missing {path.name}')


def in_tracked_tree(pipeline, output):
    """Generated binaries stay out of tracked source. Private .local output is allowed."""
    pipeline = Path(pipeline).resolve()
    if output == pipeline or pipeline not in output.parents:
        return output == pipeline
    return output.relative_to(pipeline).parts[0] != '.local'


def require_layout(root):
    """Require the private runtime directories, modes, and SONAME symlink."""
    root = Path(root)
    bindir = root / 'bin'
    libdir = root / 'lib'
    worker = bindir / 'worker_child'
    library = libdir / LIBRARY_NAME
    link = libdir / SONAME
    for path in (bindir, libdir, worker, library, link):
        require_present(path)
    require_mode(bindir, 0o755, 'dir')
    require_mode(libdir, 0o755, 'dir')
    require_mode(worker, 0o755, 'file')
    require_mode(library, 0o644, 'file')
    if not link.is_symlink():
        raise BuildFailure('layout', 'SONAME entry is not a symlink')
    target = os.readlink(link)
    if target != LIBRARY_NAME or '/' in target or target in ('.', '..'):
        raise BuildFailure('layout', f'SONAME symlink target is {target!r}')
    if not (libdir / target).is_file() or (libdir / target).is_symlink():
        raise BuildFailure('layout', 'SONAME symlink does not name the versioned library')
    names = sorted(path.name for path in libdir.iterdir())
    if names != [SONAME, LIBRARY_NAME]:
        raise BuildFailure('layout', f'lib contains {names}')
    try:
        (libdir / 'glibc-hwcaps').lstat()
    except FileNotFoundError:
        pass
    else:
        raise BuildFailure('layout', 'lib contains glibc-hwcaps')
    return worker, library, link


def guarded_paths(root):
    paths = []
    for relative in ACCEPTED_PATHS:
        candidate = root / relative
        if candidate.is_symlink() or not candidate.is_file():
            continue
        if candidate.stat().st_size > 8 << 20:
            continue
        paths.append(candidate)
    return paths


def protects_accepted(root):
    pins = {ACCEPTED_WORKER_SHA256, OMS_SHA256}
    return any(sha256_file(path) in pins for path in guarded_paths(root))


def destination(pipeline, output):
    output = Path(output)
    if not output.is_absolute():
        output = Path.cwd() / output
    parent = output.parent
    if not parent.is_dir():
        raise BuildFailure('output', f'parent of the build directory is missing: {parent}')
    output = parent.resolve() / output.name
    pipeline = Path(pipeline).resolve()
    if in_tracked_tree(pipeline, output):
        raise BuildFailure('tree', f'refusing to write inside the pipeline tree: {output}')
    if output.is_symlink() or output.exists():
        if output.is_dir() and not output.is_symlink() and protects_accepted(output):
            raise BuildFailure('accepted', f'refusing to overwrite accepted bytes in {output}')
        raise BuildFailure('output', f'refusing to overwrite existing output: {output}')
    return output


def verify_tree(pipeline):
    pipeline = Path(pipeline).resolve()
    if not (pipeline / '.git').exists():
        raise BuildFailure('missing', f'pipeline is not a git checkout: {pipeline}')
    head = git_output(pipeline, 'rev-parse', 'HEAD')
    if subprocess.call(
            ['git', '-C', str(pipeline), 'merge-base', '--is-ancestor', PIPELINE_ANCESTOR, 'HEAD']) != 0:
        raise BuildFailure('version', f'pipeline HEAD {head} is not a descendant of {PIPELINE_ANCESTOR}')
    gcc = tool_text('gcc')
    gxx = tool_text('g++')
    if COMPILER_RELEASE not in gcc or COMPILER_RELEASE not in gxx:
        raise BuildFailure('version', f'compiler is not {COMPILER_RELEASE}')
    sources = {}
    for relative, digest in PIPELINE_SOURCES.items():
        path = pipeline / relative
        if not path.is_file() or sha256_file(path) != digest:
            raise BuildFailure('hash', f'worker source hash mismatch: {relative}')
        sources[relative] = digest
    oms = {}
    for relative, digest in OMS_FILES.items():
        path = pipeline / OMS_ROOT / relative
        if not path.is_file() or sha256_file(path) != digest:
            raise BuildFailure('hash', f'OMS source hash mismatch: {relative}')
        oms[relative] = digest
    for name, digest in OMS_HEADERS.items():
        if oms['lib/src/includes/' + name] != digest:
            raise BuildFailure('hash', f'OMS header hash mismatch: {name}')
    return {
        'pipeline': pipeline,
        'head': head,
        'gcc': gcc.splitlines()[0],
        'gxx': gxx.splitlines()[0],
        'sources': sources,
        'oms': oms,
    }


def run(command, cwd, env=None):
    result = subprocess.run(command, cwd=cwd, env=env, text=True, capture_output=True, timeout=180)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or '').strip()
        raise BuildFailure('compile', f'{" ".join(command[:2])} exited {result.returncode}: {detail[-2000:]}')
    return result


def compile_closure(pipeline, build):
    commands = json.loads((build / 'compile_commands.json').read_text())
    units = set()
    root = (pipeline / OMS_ROOT).resolve()
    for item in commands:
        text = item['command'] + '\n' + item['file']
        if 'nanexus-video-trust-toolkit' in text or 'OMS_UNIT_TEST' in text:
            raise BuildFailure('closure', 'OMS compile uses a checkout or unit-test flag')
        source = Path(item['file'])
        if not source.is_absolute():
            source = (build / source).resolve()
        if not inside(root, source):
            raise BuildFailure('closure', f'OMS compile input is outside the vendored tree: {source}')
        units.add(source.relative_to(root).as_posix())
    if units != OMS_UNITS:
        raise BuildFailure('closure', f'OMS compile units are {sorted(units)}')


def compile_oms(pipeline, objects):
    meson = shutil.which('meson')
    if meson is None or shutil.which('ninja') is None:
        raise BuildFailure('missing', 'meson and ninja are required on PATH')
    source = pipeline / OMS_ROOT
    env = os.environ.copy()
    env['CC'] = '/usr/bin/gcc'
    env['CXX'] = '/usr/bin/g++'
    run([
        meson, 'setup', str(objects), str(source),
        '-Dsigningplugin=unthreaded', '-Dwerror=false', '--buildtype=debugoptimized',
    ], pipeline, env)
    run([meson, 'compile', '-C', str(objects)], pipeline, env)
    version = run([meson, '--version'], pipeline, env).stdout.strip()
    compile_closure(pipeline, objects)
    built = [path for path in objects.rglob(LIBRARY_NAME) if path.is_file() and not path.is_symlink()]
    if len(built) != 1:
        raise BuildFailure('layout', f'expected one {LIBRARY_NAME}, found {len(built)}')
    require_library_dynamic(read_dynamic(built[0]))
    return built[0], version


def link_worker(identity, library, binary):
    pipeline = identity['pipeline']
    command = [
        'g++', '-std=c++20', '-O1', '-Wno-deprecated-declarations',
        '-I', str((pipeline / INCLUDE).resolve()),
        '-I', str((pipeline / OMS_INCLUDE).resolve()),
        str((pipeline / ENTRY).resolve()),
        str((pipeline / OBSERVATION).resolve()),
        str((pipeline / SESSION).resolve()),
        str((pipeline / ERROR).resolve()),
        str(library.resolve()),
        '-lcrypto', '-Wl,--enable-new-dtags', '-Wl,-z,now', f'-Wl,-rpath,{RUNPATH}',
        '-o', str(binary),
    ]
    for part in command:
        if 'nanexus-video-trust-toolkit' in part:
            raise BuildFailure('closure', 'worker link references a Toolkit checkout')
    run(command, pipeline)
    os.chmod(binary, 0o755)
    info = read_dynamic(binary)
    require_worker_dynamic(info)
    return command, info


def place_library(built, libdir):
    library = libdir / LIBRARY_NAME
    shutil.copyfile(built, library)
    os.chmod(library, 0o644)
    os.symlink(LIBRARY_NAME, libdir / SONAME)
    require_library_dynamic(read_dynamic(library))
    return library


def manifest(identity, worker, library, worker_info, library_info, meson_version):
    worker_sha = sha256_file(worker)
    library_sha = sha256_file(library)
    return {
        'schema': 'nanexus-hv2-runtime-pair/1',
        'selected': False,
        'pipeline_commit': identity['head'],
        'compiler_release': COMPILER_RELEASE,
        'compiler': identity['gxx'],
        'meson': {
            'version': meson_version,
            'signingplugin': 'unthreaded',
            'werror': False,
            'buildtype': 'debugoptimized',
        },
        'worker_sha256': worker_sha,
        'oms_sha256': library_sha,
        'soname': SONAME,
        'library_needed': library_info['needed'],
        'worker_needed': worker_info['needed'],
        'dt_runpath': worker_info['runpath'][0],
        'dynamic_tag': 'DT_RUNPATH',
        'modes': {
            'bin': '0755',
            'lib': '0755',
            'bin/worker_child': '0755',
            'lib/' + LIBRARY_NAME: '0644',
            'lib/' + SONAME: 'symlink:' + LIBRARY_NAME,
        },
        'accepted_worker_sha256': ACCEPTED_WORKER_SHA256,
        'accepted_oms_sha256': OMS_SHA256,
        'matches_accepted_worker': worker_sha == ACCEPTED_WORKER_SHA256,
        'matches_accepted_oms': library_sha == OMS_SHA256,
        'worker_provenance': {
            'license': 'Apache-2.0',
            'copyright': 'Copyright 2026 Nanexus-AI contributors',
            'toolkit_origin': TOOLKIT_ORIGIN,
            'files': identity['sources'],
        },
        'oms_provenance': {
            'repository': 'https://github.com/onvif/media-signing-framework',
            'tag': ONVIF_TAG,
            'upstream_commit': ONVIF_COMMIT,
            'license': 'MIT',
            'copyright': 'Copyright (c) 2025 ONVIF',
            'tlv_sha256': OMS_FILES['lib/src/oms_tlv.c'],
            'files': identity['oms'],
        },
        'installation': INSTALLATION,
    }


def assemble(identity, output):
    parent = output.parent
    partial = Path(tempfile.mkdtemp(dir=parent, prefix=f'.{output.name}-'))
    objects = Path(tempfile.mkdtemp(dir=parent, prefix=f'.{output.name}-oms-'))
    try:
        if in_tracked_tree(identity['pipeline'], partial) or in_tracked_tree(identity['pipeline'], objects):
            raise BuildFailure('tree', 'refusing to compile inside the pipeline tree')
        built, meson_version = compile_oms(identity['pipeline'], objects)
        bindir = partial / 'bin'
        libdir = partial / 'lib'
        bindir.mkdir(mode=0o755)
        libdir.mkdir(mode=0o755)
        os.chmod(bindir, 0o755)
        os.chmod(libdir, 0o755)
        library = place_library(built, libdir)
        worker = bindir / 'worker_child'
        _command, worker_info = link_worker(identity, library, worker)
        require_layout(partial)
        library_info = read_dynamic(library)
        record = manifest(identity, worker, library, worker_info, library_info, meson_version)
        manifest_path = partial / 'build-manifest.json'
        manifest_path.write_text(json.dumps(record, indent=2) + '\n')
        os.chmod(manifest_path, 0o644)
        os.rename(partial, output)
        partial = None
    finally:
        if partial is not None:
            shutil.rmtree(partial, ignore_errors=True)
        shutil.rmtree(objects, ignore_errors=True)
    return record


def build(pipeline, output):
    pipeline = Path(pipeline).resolve()
    output = destination(pipeline, output)
    identity = verify_tree(pipeline)
    try:
        record = assemble(identity, output)
        require_layout(output)
        if protects_accepted(output):
            raise BuildFailure('accepted', 'candidate bytes match an accepted identity')
    except Exception:
        if output.exists():
            shutil.rmtree(output, ignore_errors=True)
        raise
    return record


def main(argv):
    parser = argparse.ArgumentParser(description='Build a private portable Worker and OMS pair.')
    parser.add_argument('--pipeline', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        record = build(args.pipeline, args.output)
    except BuildFailure as exc:
        print(f'{exc.code}: {exc}', file=sys.stderr)
        return 2
    print(record['worker_sha256'])
    print(record['oms_sha256'])
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
