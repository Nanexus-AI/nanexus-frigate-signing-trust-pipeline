"""Explicit portable-pair selection. The compiled pins stay in place."""
import hashlib
import json
import os
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_historical_v2_rolling_product as product
from nanexus_trust.frigate.host_entry import HostEntryError, start, validate_config
from nanexus_trust.historical_v2.continuity_protocol import ProtocolError
from nanexus_trust.historical_v2.continuity_windows import isolated_product_coordinator
from nanexus_trust.historical_v2.continuity_worker import OMS_SHA256, admit_oms_library, run_context
from nanexus_trust.historical_v2.rolling_product import WORKER_SHA256
import select_runtime_pair as selection

ROOT = Path(__file__).resolve().parents[1]
FROZEN = ROOT / '.local/runtime/hv2-portable-pair-c1fb7d7'
LEGACY_WORKER = ROOT / '.local/runtime/hv2-cont33r2/snapshot/launcher/worker_child'
LEGACY_WORKER_SHA = 'cfe8d86efebd749e82e308d86f88316795fcc6828bb205da3d8f2d151e8d4d22'
LEGACY_OMS = 'e157fd03c14ba2ae63ce19800cb00d994bc4500a80d84f88492d5537cda76c98'
PORTABLE_WORKER = '51680915bac480670266a8386cf648f153e41d257f718c374f6b8ec19ce9f247'
PORTABLE_OMS = '37b4b5b68836ecdbdc3ab816d4a0695752fd9fa403c8df13278bf6995a152818'


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _port():
    reserved = {8971, 8766, 18774}
    while True:
        sock = socket.socket()
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
        sock.close()
        if port not in reserved:
            return port


class SelectionContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not (FROZEN / 'bin/worker_child').is_file():
            raise unittest.SkipTest('frozen portable pair is not on this machine')
        cls.parent = tempfile.TemporaryDirectory()
        cls.runtime = Path(cls.parent.name) / 'runtime'
        shutil.copytree(FROZEN, cls.runtime, symlinks=True)
        os.chmod(cls.parent.name, 0o700)

    @classmethod
    def tearDownClass(cls):
        cls.parent.cleanup()

    def test_script_names_no_developer_path_and_leaves_the_pins(self):
        text = (ROOT / 'tools/select_runtime_pair.py').read_text()
        self.assertNotIn('/home/', text)
        self.assertNotIn('.local/', text)
        self.assertNotIn('192.168.', text)
        self.assertNotIn('select_pair(', (ROOT / 'tools/build_runtime_pair.py').read_text())
        self.assertEqual(WORKER_SHA256, LEGACY_WORKER_SHA)
        self.assertEqual(OMS_SHA256, LEGACY_OMS)
        self.assertEqual(_sha(FROZEN / 'bin/worker_child'), PORTABLE_WORKER)
        self.assertEqual(_sha(FROZEN / 'lib/libmedia-signing-framework.so.25.12.6'), PORTABLE_OMS)

    def test_selection_is_explicit_and_records_the_file_hashes(self):
        config = Path(self.parent.name) / 'host.json'
        original = {
            'schema': 1,
            'untouched': 'keep',
            'historical': {
                'media_root': '/tmp/media',
                'scratch_root': '/tmp/scratch',
                'public_ca': '/tmp/ca.pem',
                'worker': '/tmp/old/worker_child',
                'worker_sha256': LEGACY_WORKER_SHA,
            },
        }
        config.write_text(json.dumps(original))
        before = config.read_text()
        manifest_before = (self.runtime / 'build-manifest.json').read_text()
        with self.assertRaises(selection.SelectionFailure) as caught:
            selection.select_pair(self.runtime, config, select=False)
        self.assertEqual(caught.exception.code, 'selection')
        self.assertEqual(config.read_text(), before)
        self.assertEqual(selection.main([
            '--runtime', str(self.runtime), '--config', str(config)]), 2)
        self.assertEqual(config.read_text(), before)
        record = selection.select_pair(self.runtime, config, select=True)
        written = json.loads(config.read_text())
        self.assertEqual(record['worker_sha256'], PORTABLE_WORKER)
        self.assertEqual(record['oms_sha256'], PORTABLE_OMS)
        self.assertEqual(written['untouched'], 'keep')
        self.assertEqual(written['schema'], 1)
        self.assertEqual(written['historical']['worker'], str(self.runtime / 'bin/worker_child'))
        self.assertEqual(written['historical']['worker_sha256'], PORTABLE_WORKER)
        self.assertEqual(written['historical']['oms_sha256'], PORTABLE_OMS)
        self.assertEqual(stat.S_IMODE(config.stat().st_mode), 0o600)
        self.assertEqual((self.runtime / 'build-manifest.json').read_text(), manifest_before)
        self.assertFalse(json.loads(manifest_before)['selected'])
        self.assertEqual(_sha(FROZEN / 'bin/worker_child'), PORTABLE_WORKER)

    def test_malformed_inputs_do_not_write_the_config(self):
        config = Path(self.parent.name) / 'malformed.json'
        config.write_text(json.dumps({
            'historical': {
                'media_root': '/tmp/media', 'scratch_root': '/tmp/scratch',
                'public_ca': '/tmp/ca.pem', 'worker': '/tmp/old/worker_child',
                'worker_sha256': LEGACY_WORKER_SHA,
            },
        }))
        before = config.read_text()
        with self.assertRaises(selection.SelectionFailure) as caught:
            selection.select_pair(self.runtime / 'missing', config, select=True)
        self.assertEqual(caught.exception.code, 'runtime')
        self.assertEqual(config.read_text(), before)
        broken = self.runtime / 'build-manifest.json'
        saved = broken.read_text()
        try:
            record = json.loads(saved)
            record['oms_sha256'] = LEGACY_OMS
            broken.write_text(json.dumps(record))
            with self.assertRaises(selection.SelectionFailure) as caught:
                selection.select_pair(self.runtime, config, select=True)
            self.assertEqual(caught.exception.code, 'manifest')
            self.assertEqual(config.read_text(), before)
        finally:
            broken.write_text(saved)
        config.write_text('{')
        with self.assertRaises(selection.SelectionFailure) as caught:
            selection.select_pair(self.runtime, config, select=True)
        self.assertEqual(caught.exception.code, 'configuration')
        self.assertEqual(config.read_text(), '{')


class SelectedAdmissionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not (FROZEN / 'bin/worker_child').is_file() or not LEGACY_WORKER.is_file():
            raise unittest.SkipTest('worker fixtures are not on this machine')
        cls.parent = tempfile.TemporaryDirectory()
        root = Path(cls.parent.name)
        os.chmod(root, 0o700)
        cls.runtime = root / 'runtime'
        shutil.copytree(FROZEN, cls.runtime, symlinks=True)
        subprocess.run([
            'openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-keyout', str(root / 'key.pem'),
            '-out', str(root / 'cert.pem'), '-days', '1', '-nodes', '-subj', '/CN=127.0.0.1',
        ], check=True, capture_output=True)
        cls.cert = root / 'cert.pem'
        cls.root = root

    @classmethod
    def tearDownClass(cls):
        cls.parent.cleanup()

    def _tree(self):
        directory = Path(tempfile.mkdtemp(dir=self.root))
        os.chmod(directory, 0o700)
        for name in ('media', 'scratch', 'state'):
            path = directory / name
            path.mkdir()
            os.chmod(path, 0o700)
        token = directory / 'token'
        token.write_text('a' * 48)
        os.chmod(token, 0o600)
        reader = directory / 'reader.py'
        reader.write_text(
            '#!/usr/bin/env python3\n'
            'from pathlib import Path\nimport time\n'
            'Path(__file__).with_name("started").write_text("yes")\n'
            'time.sleep(30)\n')
        os.chmod(reader, 0o700)
        return directory, token, reader

    def _document(self, directory, token, reader, historical):
        return {
            'schema': 1,
            'frigate_version': '0.18.2-cfa08c5',
            'api_origin': 'https://127.0.0.1:9',
            'browser_origin': 'https://browser.test:8443',
            'instance_certificate': str(self.cert),
            'listen': {
                'live_host': '127.0.0.1', 'live_port': _port(),
                'historical_host': '127.0.0.1', 'historical_port': _port(),
            },
            'token_file': str(token),
            'ffmpeg': str(reader),
            'ffprobe': '/bin/true',
            'policy': {
                'policy_id': 'test-policy', 'evaluation_interval': 0.5,
                'active_freshness_limit': 2, 'observation_horizon': 6, 'media_silence_limit': 1.5,
            },
            'supervision': {
                'poll_seconds': 0.2, 'backoff_initial_seconds': 0.5, 'backoff_max_seconds': 5,
            },
            'cameras': [{
                'camera': 'gate', 'stream': 'gate-main', 'context': 'grid', 'rendition': 'main',
                'source_id': 'source-gate', 'revision': 'rev-1', 'rtsp_url': 'rtsp://127.0.0.1:8554/gate',
            }],
            'historical': historical,
            'state_root': str(directory / 'state'),
        }

    def _historical(self, **replace):
        values = {
            'media_root': None,
            'scratch_root': None,
            'public_ca': str(self.cert),
            'worker': str(self.runtime / 'bin/worker_child'),
            'worker_sha256': PORTABLE_WORKER,
        }
        values.update(replace)
        return values

    def test_legacy_config_still_requires_the_compiled_pins(self):
        directory, token, reader = self._tree()
        legacy = self._document(directory, token, reader, {
            'media_root': str(directory / 'media'),
            'scratch_root': str(directory / 'scratch'),
            'public_ca': str(self.cert),
            'worker': str(LEGACY_WORKER),
            'worker_sha256': LEGACY_WORKER_SHA,
        })
        loaded = validate_config(legacy)
        self.assertIsNone(loaded['oms_sha256'])
        self.assertEqual(loaded['worker_sha256'], LEGACY_WORKER_SHA)
        portable = self._document(directory, token, reader, self._historical(
            media_root=str(directory / 'media'), scratch_root=str(directory / 'scratch')))
        with self.assertRaises(HostEntryError) as caught:
            validate_config(portable)
        self.assertEqual(caught.exception.code, 'worker-identity')
        self.assertFalse((directory / 'started').exists())

    def test_malformed_selection_fails_before_the_reader_starts(self):
        directory, token, reader = self._tree()
        document = self._document(directory, token, reader, self._historical(
            media_root=str(directory / 'media'), scratch_root=str(directory / 'scratch'),
            oms_sha256='not-a-hash'))
        with self.assertRaises(HostEntryError) as caught:
            start(document)
        self.assertEqual(caught.exception.code, 'configuration')
        self.assertFalse((directory / 'started').exists())
        missing = self._document(directory, token, reader, self._historical(
            media_root=str(directory / 'media'), scratch_root=str(directory / 'scratch'),
            worker=str(directory / 'absent-worker'), oms_sha256=PORTABLE_OMS))
        with self.assertRaises(HostEntryError) as caught:
            start(missing)
        self.assertEqual(caught.exception.code, 'worker-identity')
        self.assertFalse((directory / 'started').exists())

    def test_selected_pair_launches_and_oms_bytes_are_checked_at_admission(self):
        directory, token, reader = self._tree()
        document = self._document(directory, token, reader, self._historical(
            media_root=str(directory / 'media'), scratch_root=str(directory / 'scratch'),
            oms_sha256=LEGACY_OMS))
        running = start(document)
        try:
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline and not (directory / 'started').exists():
                time.sleep(0.05)
            self.assertTrue((directory / 'started').exists())
            self.assertEqual(running.coordinator.worker_sha256, PORTABLE_WORKER)
            self.assertEqual(running.coordinator.oms_sha256, LEGACY_OMS)
        finally:
            running.stop()
        plan, request, *_ = product.fixture(1)
        started = []

        def explode(*args, **kwargs):
            started.append(args)
            raise AssertionError('popen')

        with patch('nanexus_trust.historical_v2.continuity_worker.subprocess.Popen', explode):
            rejected = run_context(
                self.runtime / 'bin/worker_child', plan, request, 'owner', 'attempt', b'CA',
                lambda *args: None, lambda *args: None, lambda: None, timeout=5, _fault='check',
                worker_sha256=PORTABLE_WORKER, oms_sha256=LEGACY_OMS)
        self.assertEqual(started, [])
        self.assertIsNone(rejected.pid)
        self.assertIn('oms-library-identity', rejected.error)
        admitted = run_context(
            self.runtime / 'bin/worker_child', plan, request, 'owner', 'attempt', b'CA',
            lambda *args: None, lambda *args: None, lambda: None, timeout=5, _fault='check',
            worker_sha256=PORTABLE_WORKER, oms_sha256=PORTABLE_OMS)
        self.assertEqual(admitted.state, 'ISOLATION_CHECKED')
        self.assertIsNone(admitted.error)
        with self.assertRaises(ProtocolError) as caught:
            admit_oms_library(self.runtime / 'bin/worker_child')
        self.assertEqual(str(caught.exception), 'oms-library-identity')
        admit_oms_library(self.runtime / 'bin/worker_child', PORTABLE_OMS)

    def test_removing_the_optional_hash_restores_the_legacy_config(self):
        directory, token, reader = self._tree()
        config = directory / 'host.json'
        document = self._document(directory, token, reader, {
            'media_root': str(directory / 'media'),
            'scratch_root': str(directory / 'scratch'),
            'public_ca': str(self.cert),
            'worker': str(LEGACY_WORKER),
            'worker_sha256': LEGACY_WORKER_SHA,
        })
        config.write_text(json.dumps(document))
        os.chmod(config, 0o600)
        selection.select_pair(self.runtime, config, select=True)
        selected = json.loads(config.read_text())
        self.assertEqual(selected['historical']['oms_sha256'], PORTABLE_OMS)
        historical = selected['historical']
        del historical['oms_sha256']
        historical['worker'] = str(LEGACY_WORKER)
        historical['worker_sha256'] = LEGACY_WORKER_SHA
        restored = validate_config(selected)
        self.assertIsNone(restored['oms_sha256'])
        self.assertEqual(restored['worker_sha256'], LEGACY_WORKER_SHA)
        self.assertEqual(_sha(LEGACY_WORKER), LEGACY_WORKER_SHA)
        self.assertEqual(_sha(FROZEN / 'lib/libmedia-signing-framework.so.25.12.6'), PORTABLE_OMS)

    def test_coordinator_keeps_one_explicit_library_hash(self):
        from nanexus_trust.frigate.historical_v2_api import FrigateSession
        from nanexus_trust.historical_v2.prototype import DirectSessionApiProfile
        import ssl
        profile = DirectSessionApiProfile(
            FrigateSession('https://frigate.test', ssl.create_default_context()),
            self.runtime, self.runtime, self.runtime / 'bin/worker_child', self.cert, Path('/bin/true'))
        chosen = isolated_product_coordinator(
            profile, self.runtime / 'bin/worker_child', PORTABLE_WORKER, oms_sha256=PORTABLE_OMS)
        self.assertEqual(chosen.oms_sha256, PORTABLE_OMS)
        self.assertEqual(chosen.worker_sha256, PORTABLE_WORKER)
        with self.assertRaises(ProtocolError) as caught:
            isolated_product_coordinator(
                profile, self.runtime / 'bin/worker_child', PORTABLE_WORKER, oms_sha256='ABC')
        self.assertEqual(str(caught.exception), 'oms-library-identity')
