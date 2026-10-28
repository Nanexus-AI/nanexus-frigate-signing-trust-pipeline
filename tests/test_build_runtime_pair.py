"""Private portable Worker/OMS pair. The accepted pins stay in place."""
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / 'src'))
sys.path.insert(0, str(Path(__file__).parents[1] / 'tools'))
import build_historical_worker as accepted
import build_runtime_pair as pair
from nanexus_trust.frigate.host_entry import ACCEPTED_WORKER, HostEntryError, _worker_identity
from nanexus_trust.historical_v2.continuity_protocol import ProtocolError
from nanexus_trust.historical_v2.continuity_worker import OMS_SHA256, admit_oms_library
from nanexus_trust.historical_v2.rolling_product import WORKER_SHA256

ROOT = Path(__file__).parents[1]
ACCEPTED_OMS = 'e157fd03c14ba2ae63ce19800cb00d994bc4500a80d84f88492d5537cda76c98'
ACCEPTED_WORKER_SHA = 'cfe8d86efebd749e82e308d86f88316795fcc6828bb205da3d8f2d151e8d4d22'
ACCEPTED_FILE = ROOT / '.local/runtime/hv2-cont33r2/snapshot/launcher/worker_child'


def git_bytes(relative):
    return subprocess.check_output(['git', '-C', str(ROOT), 'show', f'HEAD:{relative}'])


class PairContractTests(unittest.TestCase):
    def test_script_has_no_developer_path_and_keeps_the_runpath_literal(self):
        text = (ROOT / 'tools/build_runtime_pair.py').read_text()
        self.assertNotIn('/home/', text)
        self.assertNotIn('.local/', text)
        self.assertNotIn('192.168.', text)
        self.assertIn('$ORIGIN/../lib', text)
        self.assertIn('--enable-new-dtags', text)
        self.assertEqual(
            pair.INSTALLATION,
            'Installed executable/library directories must be owned and controlled by '
            'the installer or administrator, not writable by the Nanexus runtime '
            'service account.')
        self.assertIn('not writable by the Nanexus runtime', text)

    def test_accepted_sources_and_pins_are_unchanged(self):
        for relative in (
                'tools/build_historical_worker.py',
                'src/nanexus_trust/historical_v2/rolling_product.py'):
            self.assertEqual((ROOT / relative).read_bytes(), git_bytes(relative))
        self.assertEqual(accepted.OMS_SHA256, ACCEPTED_OMS)
        self.assertEqual(accepted.ACCEPTED_WORKER_SHA256, ACCEPTED_WORKER_SHA)
        self.assertEqual(OMS_SHA256, ACCEPTED_OMS)
        self.assertEqual(WORKER_SHA256, ACCEPTED_WORKER_SHA)
        self.assertEqual(ACCEPTED_WORKER, ACCEPTED_WORKER_SHA)
        self.assertNotIn('$ORIGIN', (ROOT / 'tools/build_historical_worker.py').read_text())

    def test_dynamic_parser_rejects_rpath_and_other_runpaths(self):
        good = pair.parse_dynamic(
            ' 0x1 (NEEDED) Shared library: [libmedia-signing-framework.so.25]\n'
            ' 0x1 (NEEDED) Shared library: [libcrypto.so.3]\n'
            ' 0x1 (NEEDED) Shared library: [libc.so.6]\n'
            ' 0x1d (RUNPATH) Library runpath: [$ORIGIN/../lib]\n')
        pair.require_worker_dynamic(good)
        rpath = pair.parse_dynamic(
            ' 0x1 (NEEDED) Shared library: [libmedia-signing-framework.so.25]\n'
            ' 0x1 (NEEDED) Shared library: [libcrypto.so.3]\n'
            ' 0x1 (NEEDED) Shared library: [libc.so.6]\n'
            ' 0xf (RPATH) Library rpath: [$ORIGIN/../lib]\n')
        with self.assertRaises(pair.BuildFailure) as caught:
            pair.require_worker_dynamic(rpath)
        self.assertEqual(caught.exception.code, 'runpath')
        absolute = dict(good, runpath=['/tmp/lib'])
        with self.assertRaises(pair.BuildFailure) as caught:
            pair.require_worker_dynamic(absolute)
        self.assertEqual(caught.exception.code, 'runpath')

    def test_refuses_pipeline_tree_and_existing_output(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            with self.assertRaises(pair.BuildFailure) as caught:
                pair.destination(ROOT, ROOT / 'third_party' / 'not-created')
            self.assertEqual(caught.exception.code, 'tree')
            self.assertFalse((ROOT / 'third_party' / 'not-created').exists())
            existing = root / 'runtime'
            existing.mkdir()
            (existing / 'keep').write_text('unchanged')
            with self.assertRaises(pair.BuildFailure) as caught:
                pair.destination(ROOT, existing)
            self.assertEqual(caught.exception.code, 'output')
            self.assertEqual((existing / 'keep').read_text(), 'unchanged')

    def test_refuses_a_directory_holding_the_accepted_worker(self):
        if not ACCEPTED_FILE.is_file():
            self.skipTest('accepted worker file is not on this machine')
        before = ACCEPTED_FILE.read_bytes()
        with self.assertRaises(pair.BuildFailure) as caught:
            pair.destination(ROOT, ACCEPTED_FILE.parent)
        self.assertEqual(caught.exception.code, 'accepted')
        self.assertEqual(ACCEPTED_FILE.read_bytes(), before)

    def test_layout_rejects_a_malformed_library_directory(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / 'runtime'
            bindir = root / 'bin'
            libdir = root / 'lib'
            bindir.mkdir(parents=True)
            libdir.mkdir()
            os.chmod(bindir, 0o755)
            os.chmod(libdir, 0o755)
            worker = bindir / 'worker_child'
            worker.write_bytes(b'worker')
            os.chmod(worker, 0o755)
            library = libdir / pair.LIBRARY_NAME
            library.write_bytes(b'library')
            os.chmod(library, 0o644)
            os.symlink(pair.LIBRARY_NAME, libdir / pair.SONAME)
            pair.require_layout(root)
            (libdir / 'glibc-hwcaps').mkdir()
            with self.assertRaises(pair.BuildFailure) as caught:
                pair.require_layout(root)
            self.assertEqual(caught.exception.code, 'layout')
            (libdir / 'glibc-hwcaps').rmdir()
            (libdir / pair.SONAME).unlink()
            os.symlink('/tmp/' + pair.LIBRARY_NAME, libdir / pair.SONAME)
            with self.assertRaises(pair.BuildFailure) as caught:
                pair.require_layout(root)
            self.assertEqual(caught.exception.code, 'layout')
            (libdir / pair.SONAME).unlink()
            os.symlink('../' + pair.LIBRARY_NAME, libdir / pair.SONAME)
            with self.assertRaises(pair.BuildFailure) as caught:
                pair.require_layout(root)
            self.assertEqual(caught.exception.code, 'layout')
            (libdir / pair.SONAME).unlink()
            library.unlink()
            os.symlink(pair.LIBRARY_NAME, libdir / pair.SONAME)
            with self.assertRaises(pair.BuildFailure) as caught:
                pair.require_layout(root)
            self.assertEqual(caught.exception.code, 'layout')


class PairBuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.parent = tempfile.TemporaryDirectory()
        cls.output = Path(cls.parent.name) / 'runtime'
        cls.record = pair.build(ROOT, cls.output)

    @classmethod
    def tearDownClass(cls):
        cls.parent.cleanup()

    def test_layout_modes_symlink_and_manifest(self):
        worker, library, link = pair.require_layout(self.output)
        self.assertEqual(stat.S_IMODE(worker.lstat().st_mode), 0o755)
        self.assertEqual(stat.S_IMODE(library.lstat().st_mode), 0o644)
        self.assertEqual(os.readlink(link), pair.LIBRARY_NAME)
        self.assertEqual(sorted(path.name for path in self.output.iterdir()),
                         ['bin', 'build-manifest.json', 'lib'])
        self.assertFalse((self.output / 'lib' / 'glibc-hwcaps').exists())
        manifest = json.loads((self.output / 'build-manifest.json').read_text())
        self.assertEqual(manifest, self.record)
        self.assertEqual(manifest['schema'], 'nanexus-hv2-runtime-pair/1')
        self.assertFalse(manifest['selected'])
        self.assertEqual(manifest['pipeline_commit'], subprocess.check_output(
            ['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True).strip())
        self.assertEqual(manifest['worker_sha256'], pair.sha256_file(worker))
        self.assertEqual(manifest['oms_sha256'], pair.sha256_file(library))
        self.assertNotEqual(manifest['worker_sha256'], ACCEPTED_WORKER_SHA)
        self.assertNotEqual(manifest['oms_sha256'], ACCEPTED_OMS)
        self.assertFalse(manifest['matches_accepted_worker'])
        self.assertFalse(manifest['matches_accepted_oms'])
        self.assertEqual(manifest['dt_runpath'], '$ORIGIN/../lib')
        self.assertEqual(manifest['dynamic_tag'], 'DT_RUNPATH')
        self.assertEqual(manifest['soname'], pair.SONAME)
        self.assertEqual(manifest['library_needed'], ['libcrypto.so.3', 'libc.so.6'])
        worker_info = pair.read_dynamic(worker)
        library_info = pair.read_dynamic(library)
        self.assertEqual(worker_info['runpath'], ['$ORIGIN/../lib'])
        self.assertEqual(worker_info['rpath'], [])
        self.assertEqual(worker_info['needed'], manifest['worker_needed'])
        self.assertEqual(library_info['soname'], [pair.SONAME])
        self.assertEqual(library_info['needed'], ['libcrypto.so.3', 'libc.so.6'])
        self.assertEqual(manifest['oms_provenance']['tag'], 'r25.12.6')
        self.assertEqual(manifest['oms_provenance']['upstream_commit'],
                         'cf7785ab993c18d921094e8e505c2c34a1350f28')
        self.assertEqual(manifest['oms_provenance']['license'], 'MIT')
        self.assertEqual(manifest['worker_provenance']['license'], 'Apache-2.0')
        self.assertEqual(manifest['installation'], pair.INSTALLATION)
        self.assertEqual(manifest['modes']['bin/worker_child'], '0755')
        self.assertEqual(manifest['modes']['lib/' + pair.LIBRARY_NAME], '0644')
        self.assertEqual(manifest['meson']['optimization'], '2')
        self.assertFalse(manifest['meson']['debug'])
        self.assertNotIn('.debug_', subprocess.check_output(
            ['readelf', '-S', str(library)], text=True))

    def test_two_output_directories_produce_the_same_binaries(self):
        other_parent = tempfile.TemporaryDirectory()
        self.addCleanup(other_parent.cleanup)
        other = Path(other_parent.name) / 'runtime'
        record = pair.build(ROOT, other)
        worker, library, _link = pair.require_layout(other)
        self.assertEqual(record['worker_sha256'], self.record['worker_sha256'])
        self.assertEqual(record['oms_sha256'], self.record['oms_sha256'])
        self.assertEqual((other / 'bin' / 'worker_child').read_bytes(),
                         (self.output / 'bin' / 'worker_child').read_bytes())
        self.assertEqual(library.read_bytes(),
                         (self.output / 'lib' / pair.LIBRARY_NAME).read_bytes())
        self.assertEqual(pair.sha256_file(worker), record['worker_sha256'])
        self.assertNotEqual(record['worker_sha256'], ACCEPTED_WORKER_SHA)
        self.assertNotEqual(record['oms_sha256'], ACCEPTED_OMS)

    def test_second_build_does_not_overwrite(self):
        before = (self.output / 'bin' / 'worker_child').read_bytes()
        with self.assertRaises(pair.BuildFailure) as caught:
            pair.build(ROOT, self.output)
        self.assertEqual(caught.exception.code, 'output')
        self.assertEqual((self.output / 'bin' / 'worker_child').read_bytes(), before)

    def test_admission_rejects_the_candidate_and_host_entry_keeps_its_pin(self):
        worker = self.output / 'bin' / 'worker_child'
        candidate = self.record['worker_sha256']
        with self.assertRaises(ProtocolError) as oms:
            admit_oms_library(worker)
        self.assertEqual(str(oms.exception), 'oms-library-identity')
        with self.assertRaises(HostEntryError) as configured:
            _worker_identity(worker, candidate)
        self.assertEqual(configured.exception.code, 'worker-identity')
        with self.assertRaises(HostEntryError) as accepted_name:
            _worker_identity(worker, ACCEPTED_WORKER_SHA)
        self.assertEqual(accepted_name.exception.code, 'worker-identity')
        self.assertEqual(OMS_SHA256, ACCEPTED_OMS)
        self.assertEqual(WORKER_SHA256, ACCEPTED_WORKER_SHA)

    def test_source_tree_has_no_build_output(self):
        status = subprocess.check_output(
            ['git', '-C', str(ROOT), 'status', '--porcelain', '--', 'third_party', 'NOTICE', 'LICENSE'],
            text=True)
        self.assertEqual(status, '')
        self.assertTrue(self.output.is_relative_to(Path(self.parent.name)))
        self.assertFalse(pair.inside(ROOT, self.output))
        self.assertFalse(any(self.output.rglob('*.o')))
