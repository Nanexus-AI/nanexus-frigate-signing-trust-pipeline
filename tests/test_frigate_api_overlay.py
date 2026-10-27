"""Focused checks for the removable Frigate 0.18.2 API overlay."""
import ast
import hashlib
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from nanexus_trust.frigate import api_overlay as overlay
from nanexus_trust.frigate import navigation_overlay

ROOT = Path(__file__).resolve().parents[1]
PRODUCT_SOURCE = (
    ROOT / 'src/nanexus_trust/frigate/api_overlay.py',
    ROOT / 'src/nanexus_trust/frigate/api_nanexus.py',
)
FORBIDDEN = (
    '/home/', '.local', '192.168.', 'frigate-ui-admin', 'nanexus-3cam', 'live-token',
)
IMAGE = 'ghcr.io/blakeblackshear/frigate@' + overlay.IMAGE_DIGEST


def _sha256(text):
    return hashlib.sha256(text.encode()).hexdigest()


def _upstream_from_image():
    name = f'nanexus-api-overlay-test-{os.getpid()}'
    subprocess.run(['docker', 'rm', '-f', name], check=False, capture_output=True)
    subprocess.run(['docker', 'create', '--name', name, IMAGE], check=True, capture_output=True)
    try:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for filename in ('auth.py', 'fastapi_app.py'):
                subprocess.run(
                    ['docker', 'cp', f'{name}:/opt/frigate/frigate/api/{filename}', root / filename],
                    check=True, capture_output=True)
            return (root / 'auth.py').read_text(), (root / 'fastapi_app.py').read_text()
    finally:
        subprocess.run(['docker', 'rm', '-f', name], check=True, capture_output=True)


class ApiOverlayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.auth, cls.fastapi = _upstream_from_image()

    def test_version_and_upstream_hashes_are_enforced(self):
        self.assertEqual(overlay.FRIGATE_VERSION, '0.18.2-cfa08c5')
        self.assertEqual(overlay.UPSTREAM_COMMIT, 'cfa08c56b23d23280c3d0a218e5a43182c97445b')
        self.assertEqual(_sha256(self.auth), overlay.UPSTREAM_SHA256['auth.py'])
        self.assertEqual(_sha256(self.fastapi), overlay.UPSTREAM_SHA256['fastapi_app.py'])
        for version in ('0.18.0', '0.18.0-77a66e7', '0.18.2', '0.18.2-cfa08c56'):
            with self.assertRaises(overlay.OverlayBaselineError) as caught:
                overlay.render_api_overlay(self.auth, self.fastapi, version)
            self.assertEqual(caught.exception.reason, 'frigate-version')
        with self.assertRaises(overlay.OverlayBaselineError) as caught:
            overlay.render_api_overlay(self.auth + '\n', self.fastapi, overlay.FRIGATE_VERSION)
        self.assertEqual(caught.exception.reason, 'upstream-hash')

    def test_render_matches_the_accepted_api(self):
        first = overlay.render_api_overlay(self.auth, self.fastapi, overlay.FRIGATE_VERSION)
        second = overlay.render_api_overlay(self.auth, self.fastapi, overlay.FRIGATE_VERSION)
        self.assertEqual(first, second)
        for name, digest in overlay.ACCEPTED_SHA256.items():
            self.assertEqual(_sha256(first[name]), digest)
            ast.parse(first[name])
        self.assertEqual(first['auth.py'].replace(overlay.AUTH_INSERT, overlay.AUTH_ANCHOR, 1), self.auth)
        restored = first['fastapi_app.py'].replace(overlay.ROUTER_INSERT, overlay.ROUTER_ANCHOR, 1)
        restored = restored.replace(overlay.IMPORT_INSERT, overlay.IMPORT_ANCHOR, 1)
        self.assertEqual(restored, self.fastapi)
        self.assertEqual(first['auth.py'].count('"/nanexus/live/status"'), 1)
        self.assertEqual(first['fastapi_app.py'].count('include_router(nanexus.router)'), 1)
        self.assertNotIn('"/nanexus"', first['auth.py'])

    def test_authorization_boundary_is_preserved(self):
        module = (ROOT / 'src/nanexus_trust/frigate/api_nanexus.py').read_text()
        tree = ast.parse(module)
        found = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                found.append((node.module, tuple(alias.name for alias in node.names)))
        self.assertIn(('frigate.api.auth', ('allow_any_authenticated', 'require_camera_access')), found)
        self.assertIn(('frigate.version', ('VERSION',)), found)
        self.assertFalse(any(name and 'historical' in name for name, _names in found))
        self.assertIn('dependencies=[Depends(allow_any_authenticated())]', module)
        self.assertIn('await require_camera_access(camera_name=camera, request=request)', module)
        self.assertIn('raise HTTPException(status_code=404, detail="Camera unavailable")', module)
        self.assertIn('status_code=422, detail="Invalid playback request"', module)
        self.assertIn('result = unknown()', module)
        self.assertIn('if VERSION != "0.18.2-cfa08c5":', module)
        self.assertIn('or upstream.username or upstream.password', module)
        for needle in ('8766', '__nanexus', '/window/', 'historical-bridge'):
            self.assertNotIn(needle, module)

    def test_duplicate_or_unknown_input_is_rejected(self):
        rendered = overlay.render_api_overlay(self.auth, self.fastapi, overlay.FRIGATE_VERSION)
        with self.assertRaises(overlay.OverlayBaselineError) as caught:
            overlay.render_api_overlay(rendered['auth.py'], rendered['fastapi_app.py'], overlay.FRIGATE_VERSION)
        self.assertEqual(caught.exception.reason, 'upstream-hash')
        accepted_hashes = {name: _sha256(rendered[name]) for name in ('auth.py', 'fastapi_app.py')}
        with patch.object(overlay, 'UPSTREAM_SHA256', accepted_hashes):
            with self.assertRaises(overlay.OverlayBaselineError) as caught:
                overlay.render_api_overlay(rendered['auth.py'], rendered['fastapi_app.py'], overlay.FRIGATE_VERSION)
        self.assertEqual(caught.exception.reason, 'already-present')
        doubled = 'keep\n' + overlay.AUTH_ANCHOR + overlay.AUTH_ANCHOR
        fastapi = overlay.IMPORT_ANCHOR + overlay.ROUTER_ANCHOR
        with patch.object(overlay, 'UPSTREAM_SHA256', {'auth.py': _sha256(doubled), 'fastapi_app.py': _sha256(fastapi)}):
            with self.assertRaises(overlay.OverlayBaselineError) as caught:
                overlay.render_api_overlay(doubled, fastapi, overlay.FRIGATE_VERSION)
        self.assertEqual(caught.exception.reason, 'auth-anchor')

    def test_output_stays_outside_the_repository(self):
        rendered = overlay.render_api_overlay(self.auth, self.fastapi, overlay.FRIGATE_VERSION)
        with tempfile.TemporaryDirectory() as temporary:
            destination = overlay.write_api_overlay(rendered, temporary)
            self.assertEqual(destination, Path(temporary).resolve() / 'frigate' / 'api')
            for name, digest in overlay.ACCEPTED_SHA256.items():
                self.assertEqual(_sha256((destination / name).read_text()), digest)
            with self.assertRaises(overlay.OverlayBaselineError) as caught:
                overlay.write_api_overlay(rendered, temporary)
            self.assertEqual(caught.exception.reason, 'output-area')
        with self.assertRaises(overlay.OverlayBaselineError):
            overlay.write_api_overlay(rendered, ROOT)
        with self.assertRaises(overlay.OverlayBaselineError):
            overlay.write_api_overlay(rendered, 'relative-output')

    def test_public_source_excludes_private_runtime_material(self):
        for path in PRODUCT_SOURCE:
            text = path.read_text()
            for needle in FORBIDDEN:
                self.assertNotIn(needle, text, f'{path.name} contains {needle}')
        self.assertNotIn('docker', (ROOT / 'src/nanexus_trust/frigate/api_overlay.py').read_text())

    def test_navigation_overlay_remains_independent(self):
        self.assertEqual(navigation_overlay.FRIGATE_VERSION, '0.18.2-cfa08c5')
        self.assertEqual(
            navigation_overlay.NGINX_BASELINE_SHA256,
            '563a6e4b5219b7d3a3b9d6992ebf1f5e1ac27b5be570be903a26fe5697d9a7fd')
        self.assertEqual(
            navigation_overlay.UPSTREAM_COMMIT,
            'cfa08c56b23d23280c3d0a218e5a43182c97445b')


if __name__ == '__main__':
    unittest.main()
