"""Safety and persistent mount checks for the private prototype; no live services."""
import argparse
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    'private_install', Path(__file__).resolve().parents[1] / 'tools/frigate_private_install.py')
install = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(install)


class PrivateInstallTests(unittest.TestCase):
    def settings(self):
        return {'name': 'nanexus-private-test', 'port': 18991, 'recordings': '/tmp/readonly-recordings'}

    def test_native_recovery_keeps_data_and_omits_all_overlays(self):
        root = Path('/tmp/private-install')
        integrated = install.command(root, self.settings())
        native = install.command(root, self.settings(), native=True)
        for target in ('/config', '/media/frigate', '/etc/letsencrypt/live/frigate'):
            self.assertTrue(any('dst=' + target in item for item in native))
        self.assertFalse(any('dst=/nanexus' in item or 's6-rc.d' in item or
                             'NANEXUS_LIVE_' in item or 'dst=/opt/frigate/' in item for item in native))
        self.assertEqual(native[-1], install.IMAGE)
        self.assertTrue(any('dst=/nanexus/recordings,readonly' in item for item in integrated))
        self.assertTrue(any('dst=/nanexus/pair,readonly' in item for item in integrated))

    def test_refuses_unmanaged_existing_container(self):
        result = type('Result', (), {'returncode': 0, 'stdout': json.dumps([
            {'Config': {'Labels': {install.LABEL: '/some/other/install'}}}])})()
        with patch.object(install.subprocess, 'run', return_value=result):
            with self.assertRaisesRegex(ValueError, 'unmanaged'):
                install.owned_container(Path('/tmp/private-install'), self.settings())

    def test_daemon_failure_is_not_container_absence(self):
        result = type('Result', (), {'returncode': 1, 'stderr': 'permission denied'})()
        with patch.object(install.subprocess, 'run', return_value=result):
            with self.assertRaisesRegex(ValueError, 'inspection failed'):
                install.owned_container(Path('/tmp/private-install'), self.settings())

    def test_rejects_repository_install_and_symlink(self):
        with self.assertRaises(ValueError):
            install.external_root('/tmp/repository/local', Path('/tmp/repository'))
        with tempfile.TemporaryDirectory() as directory:
            link = Path(directory) / 'link'
            link.symlink_to('/tmp')
            with self.assertRaises(ValueError):
                install.external_root(str(link), Path('/tmp/repository'))

    def test_prepare_uses_frozen_commit_after_checkpoint(self):
        # A later checkpoint HEAD must not prevent using the accepted baseline.
        # Stop before filesystem/Docker work and confirm which identity Git resolves.
        with patch.object(install, 'run', return_value=install.PIPELINE) as git, \
                patch.object(install, 'external_root', side_effect=ValueError('stop before writes')):
            with self.assertRaisesRegex(ValueError, 'stop before writes'):
                install.prepare(argparse.Namespace(root='/tmp/private-new'))
        self.assertEqual(git.call_args.args[-1], install.PIPELINE + '^{commit}')

    def test_cached_frontend_mutation_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            dist = Path(directory)
            (dist / 'index.html').write_text('modified')
            with self.assertRaisesRegex(ValueError, 'inventory mismatch'):
                install.check_frontend(dist, {'index.html': '0' * 64})


if __name__ == '__main__':
    unittest.main()
