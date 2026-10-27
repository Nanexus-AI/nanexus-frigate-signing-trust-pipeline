"""Explicit path-and-hash launch. Does not replace the accepted Worker or either bridge."""
import hashlib
import os
import stat
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from nanexus_trust.historical_v2.continuity_protocol import ProtocolError
from nanexus_trust.historical_v2.continuity_windows import (
    ProductRollingWindowCoordinator, isolated_product_coordinator)
from nanexus_trust.historical_v2.continuity_worker import run_context
from nanexus_trust.historical_v2.prototype import DirectSessionApiProfile
from nanexus_trust.historical_v2.rolling_product import CONFIGURATION, WORKER_SHA256
from nanexus_trust.frigate.historical_v2_api import FrigateSession
import test_historical_v2_rolling_product as product

ROOT = Path(__file__).resolve().parents[1]
ACCEPTED = 'cfe8d86efebd749e82e308d86f88316795fcc6828bb205da3d8f2d151e8d4d22'
PRODUCT = 'd12125f5923f82e6b17d923791b0069ff30820c1381fd405b47f78c0f18e3f47'
ACCEPTED_WORKER = ROOT / '.local/runtime/hv2-cont33r2/snapshot/launcher/worker_child'
STAGED = ROOT / '.local/runtime/hv2-product-worker-isolated/worker_child'
BRIDGES = (
    ROOT / '.local/runtime/three-cam-e2e/h2-staged/historical-bridge.py',
    ROOT / '.local/runtime/three-cam-e2e/historical-bridge.py',
)


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class IsolatedWorkerTests(unittest.TestCase):
    def test_default_pin_and_private_runtime_are_unchanged(self):
        source = (ROOT / 'src/nanexus_trust/historical_v2/rolling_product.py').read_text()
        self.assertIn(f"WORKER_SHA256 = '{ACCEPTED}'", source)
        self.assertEqual(WORKER_SHA256, ACCEPTED)
        self.assertEqual(sha256(ACCEPTED_WORKER), ACCEPTED)
        self.assertEqual(sha256(ROOT / '.local/runtime/hv2-cont09/child'), ACCEPTED)
        for bridge in BRIDGES:
            text = bridge.read_text()
            self.assertIn('snapshot/launcher/worker_child', text)
            self.assertNotIn(PRODUCT, text)
            self.assertNotIn('hv2-product-worker-isolated', text)
        for rel in (
                'src/nanexus_trust/historical_v2/continuity_worker.py',
                'src/nanexus_trust/historical_v2/continuity_windows.py',
                'src/nanexus_trust/historical_v2/rolling_product.py'):
            text = (ROOT / rel).read_text()
            self.assertNotIn('/home/', text)
            self.assertNotIn('.local/', text)
            self.assertNotIn('192.168.', text)
            self.assertNotIn(PRODUCT, text)

    def test_isolated_selection_is_the_staged_executable(self):
        self.assertEqual(sha256(STAGED), PRODUCT)
        info, parent = STAGED.stat(), STAGED.parent.stat()
        self.assertEqual(stat.S_IMODE(parent.st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(info.st_mode), 0o555)
        self.assertEqual(info.st_uid, os.geteuid())
        profile = DirectSessionApiProfile(
            FrigateSession('https://frigate.test', __import__('ssl').create_default_context()),
            STAGED.parent, STAGED.parent, STAGED.parent / 'not-the-worker', STAGED, Path('/usr/bin/ffprobe'))
        default = ProductRollingWindowCoordinator(profile)
        selected = isolated_product_coordinator(profile, STAGED, PRODUCT)
        self.assertIsNone(default.worker_sha256)
        self.assertEqual(default.profile.worker, STAGED.parent / 'not-the-worker')
        self.assertEqual(selected.worker_sha256, PRODUCT)
        self.assertEqual(selected.profile.worker, STAGED)
        sibling = STAGED.parent / 'other-worker'
        self.assertFalse(sibling.exists())
        with self.assertRaises(ProtocolError):
            isolated_product_coordinator(profile, 'worker_child', PRODUCT)
        with self.assertRaises(ProtocolError):
            isolated_product_coordinator(profile, STAGED, ACCEPTED[:-1] + 'g')

    def test_execution_boundary_admits_match_and_rejects_before_popen(self):
        plan, request, *_ = product.fixture(1)
        trust = b'CA'
        def refuse(*args, **kwargs):
            raise AssertionError('load')
        def launch(executable, expected=None):
            calls = []
            def spy(argv, *args, **kwargs):
                calls.append(list(argv))
                return real(argv, *args, **kwargs)
            real = __import__('subprocess').Popen
            with patch('nanexus_trust.historical_v2.continuity_worker.subprocess.Popen', spy):
                outcome = run_context(executable, plan, request, 'owner', 'attempt', trust, refuse, refuse,
                                      lambda: None, timeout=5, _fault='check', worker_sha256=expected)
            return outcome, calls
        accepted, accepted_calls = launch(ACCEPTED_WORKER)
        self.assertEqual(accepted.state, 'ISOLATION_CHECKED')
        self.assertEqual(accepted_calls[0][0], str(ACCEPTED_WORKER.resolve()))
        self.assertEqual(accepted_calls[0][2], 'check')
        staged, staged_calls = launch(STAGED, PRODUCT)
        self.assertEqual(staged.state, 'ISOLATION_CHECKED')
        self.assertEqual(staged_calls[0][0], str(STAGED.resolve()))
        self.assertEqual(sha256(ACCEPTED_WORKER), ACCEPTED)
        default_blocked = []
        def default_explode(argv, *args, **kwargs):
            default_blocked.append(argv)
            raise AssertionError('popen')
        with patch('nanexus_trust.historical_v2.continuity_worker.subprocess.Popen', default_explode):
            rejected = run_context(STAGED, plan, request, 'owner', 'attempt', trust, refuse, refuse,
                                   lambda: None, timeout=5, _fault='check')
        self.assertEqual(default_blocked, [])
        self.assertEqual(rejected.state, 'FAILED')
        self.assertIn('product-worker-identity', rejected.error)
        blocked = []
        def explode(argv, *args, **kwargs):
            blocked.append(argv)
            raise AssertionError('popen')
        with tempfile.TemporaryDirectory() as temp:
            wrong = Path(temp) / 'wrong-worker'
            wrong.write_bytes(b'not-the-product-worker')
            decoy = Path(temp) / 'worker_child'
            decoy.write_bytes(STAGED.read_bytes())
            self.assertEqual(sha256(decoy), PRODUCT)
            with patch('nanexus_trust.historical_v2.continuity_worker.subprocess.Popen', explode):
                outcome = run_context(wrong, plan, request, 'owner', 'attempt', trust, refuse, refuse,
                                      lambda: None, timeout=5, _fault='check', worker_sha256=PRODUCT)
            self.assertEqual(blocked, [])
            self.assertEqual(outcome.state, 'FAILED')
            self.assertIn('product-worker-identity', outcome.error)

    def test_product_protocol_contract_remains_enforced(self):
        plan, request, *_ = product.fixture(1)
        spec = plan.attempt(request, 'owner', 'attempt')
        self.assertEqual(spec.configuration, CONFIGURATION)
        self.assertEqual(spec.profile, 'native-hv2-cont09-v1')
        self.assertEqual(spec.policy, 'standalone-candidate-v1')
        for field, value in (
                ('profile', 'native-hv2-cont03a-v1'),
                ('policy', 'qualified-rolling-context-v1'),
                ('configuration', 'f3d7c70f0a9e8c0e5b49b41d0be5cfdffe785d1620953ec238bbc8a50a7cee26')):
            with self.subTest(field=field), self.assertRaises(ProtocolError):
                replace(spec, **{field: value})


if __name__ == '__main__':
    unittest.main()
