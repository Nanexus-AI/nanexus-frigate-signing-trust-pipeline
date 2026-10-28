#!/usr/bin/env python3
"""Select one portable Worker/OMS pair into an existing Host Entry config.

The operator names the runtime directory and the configuration file. This
command re-hashes those files and writes ``worker``, ``worker_sha256``, and
``oms_sha256``. A build manifest is not authorization. Selection does not
prove who published the bytes.
"""
import argparse
import json
import os
import stat
import sys
import tempfile
from pathlib import Path

from build_runtime_pair import (
    BuildFailure, read_dynamic, require_layout, require_library_dynamic,
    require_worker_dynamic, sha256_file)

HISTORICAL = {'media_root', 'scratch_root', 'public_ca', 'worker', 'worker_sha256'}


class SelectionFailure(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _sha256_text(value):
    return type(value) is str and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def inspect_runtime(runtime):
    runtime = Path(runtime)
    if not runtime.is_absolute():
        runtime = Path.cwd() / runtime
    if runtime.is_symlink() or not runtime.is_dir():
        raise SelectionFailure('runtime', 'runtime directory is missing')
    runtime = runtime.resolve()
    try:
        worker, library, _link = require_layout(runtime)
        require_worker_dynamic(read_dynamic(worker))
        require_library_dynamic(read_dynamic(library))
    except BuildFailure as exc:
        raise SelectionFailure(exc.code, str(exc)) from exc
    worker_sha = sha256_file(worker)
    oms_sha = sha256_file(library)
    manifest_path = runtime / 'build-manifest.json'
    if manifest_path.is_symlink():
        raise SelectionFailure('manifest', 'build manifest is a symlink')
    if manifest_path.exists():
        try:
            record = json.loads(manifest_path.read_text())
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise SelectionFailure('manifest', 'build manifest is malformed') from exc
        if (type(record) is not dict or record.get('worker_sha256') != worker_sha
                or record.get('oms_sha256') != oms_sha):
            raise SelectionFailure('manifest', 'build manifest does not match the files')
    return worker, worker_sha, oms_sha


def load_config(path):
    path = Path(path)
    if not path.is_absolute():
        path = Path.cwd() / path
    if path.is_symlink() or not path.is_file():
        raise SelectionFailure('configuration', 'configuration file is missing')
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > 65536:
            raise OSError()
        document = json.loads(path.read_text())
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SelectionFailure('configuration', 'configuration file is malformed') from exc
    if type(document) is not dict or type(document.get('historical')) is not dict:
        raise SelectionFailure('configuration', 'configuration has no historical section')
    keys = set(document['historical'])
    if keys != HISTORICAL and keys != HISTORICAL | {'oms_sha256'}:
        raise SelectionFailure('configuration', 'historical section is malformed')
    return path, document


def write_config(path, document):
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix='.select-', suffix='.tmp')
    try:
        with os.fdopen(descriptor, 'w') as handle:
            json.dump(document, handle, indent=2)
            handle.write('\n')
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            os.unlink(temporary)


def select_pair(runtime, config, *, select):
    """Write the selected pair only when ``select`` is true."""
    if select is not True:
        raise SelectionFailure('selection', 'selection requires an explicit --select')
    worker, worker_sha, oms_sha = inspect_runtime(runtime)
    path, document = load_config(config)
    historical = document['historical']
    historical['worker'] = str(worker)
    historical['worker_sha256'] = worker_sha
    historical['oms_sha256'] = oms_sha
    if not _sha256_text(worker_sha) or not _sha256_text(oms_sha):
        raise SelectionFailure('hash', 'computed identity is malformed')
    write_config(path, document)
    return {'worker': str(worker), 'worker_sha256': worker_sha, 'oms_sha256': oms_sha}


def main(argv):
    parser = argparse.ArgumentParser(description='Select a portable Worker and OMS pair.')
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--select', action='store_true')
    args = parser.parse_args(argv)
    try:
        record = select_pair(args.runtime, args.config, select=args.select)
    except SelectionFailure as exc:
        print(f'{exc.code}: {exc}', file=sys.stderr)
        return 2
    print(record['worker_sha256'])
    print(record['oms_sha256'])
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
