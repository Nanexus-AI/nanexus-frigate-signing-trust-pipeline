"""Focused checks for the removable Frigate 0.18.2 navigation overlay."""
import hashlib
import unittest
from pathlib import Path
from unittest.mock import patch
from nanexus_trust.frigate import navigation_overlay as overlay

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'tests/fixtures/frigate-0.18.2-cfa08c5.nginx.conf'
PRODUCT_SOURCE = (
    ROOT / 'src/nanexus_trust/frigate/navigation_overlay.py',
    ROOT / 'src/nanexus_trust/frigate/navigation_locations.nginx',
    FIXTURE,
)
RENDERED_SHA256 = '9ad43d323fddc12f7911abf137df2da95d48386d956994616d42cb4184af40c9'
FORBIDDEN = (
    '/home/', '.local', '192.168.', 'credential', 'live-token',
    'frigate-ui-admin', 'password', 'nanexus-3cam',
)


class NavigationOverlayTests(unittest.TestCase):
    def setUp(self):
        self.baseline = FIXTURE.read_text()

    def test_overlay_targets_only_the_frozen_baseline(self):
        self.assertEqual(overlay.FRIGATE_VERSION, '0.18.2-cfa08c5')
        self.assertEqual(overlay.UPSTREAM_COMMIT, 'cfa08c56b23d23280c3d0a218e5a43182c97445b')
        self.assertEqual(hashlib.sha256(self.baseline.encode()).hexdigest(), overlay.NGINX_BASELINE_SHA256)
        rendered = overlay.render_navigation_overlay(self.baseline, overlay.FRIGATE_VERSION)
        self.assertEqual(hashlib.sha256(rendered.encode()).hexdigest(), RENDERED_SHA256)
        self.assertIn('worker_processes auto;', rendered)
        self.assertNotIn('worker_processes 4;', rendered)

    def test_expected_nginx_additions_are_exact_and_unique(self):
        rendered = overlay.render_navigation_overlay(self.baseline, overlay.FRIGATE_VERSION)
        fragment = (ROOT / 'src/nanexus_trust/frigate/navigation_locations.nginx').read_text()
        self.assertIn(fragment, rendered)
        self.assertEqual(rendered.count(fragment), 1)
        self.assertIn('$nanexus_markup', rendered)
        self.assertIn('alias /nanexus-navigation/;', rendered)
        self.assertIn('proxy_pass http://127.0.0.1:8766/session;', rendered)
        for route in overlay.ROUTES:
            self.assertEqual(rendered.count(route), 1)
        for name in overlay.UI_SHA256:
            self.assertEqual(rendered.count(f'/__nanexus/{name}'), 1)
        doubled = fragment + fragment
        with patch.object(overlay, '_locations', return_value=doubled):
            with self.assertRaises(overlay.OverlayBaselineError) as caught:
                overlay.render_navigation_overlay(self.baseline, overlay.FRIGATE_VERSION)
        self.assertEqual(caught.exception.reason, 'duplicate-route')
        with self.assertRaises(overlay.OverlayBaselineError) as stacked:
            overlay.render_navigation_overlay(rendered, overlay.FRIGATE_VERSION)
        self.assertEqual(stacked.exception.reason, 'nginx-baseline')

    def test_ui_resources_come_from_committed_product_source(self):
        names = sorted(path.name for path in (ROOT / 'ui/nanexus').iterdir())
        self.assertEqual(names, sorted(overlay.UI_SHA256))
        found = overlay.navigation_assets(ROOT)
        self.assertEqual(set(found), set(overlay.UI_SHA256))
        for name, path in found.items():
            self.assertEqual(path, ROOT / 'ui' / 'nanexus' / name)
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), overlay.UI_SHA256[name])
        copied = list((ROOT / 'src/nanexus_trust/frigate').glob('*.js'))
        copied.extend((ROOT / 'src/nanexus_trust/frigate').glob('*.css'))
        self.assertEqual(copied, [])

    def test_unknown_upstream_nginx_is_rejected(self):
        changed = self.baseline.replace('worker_processes auto;', 'worker_processes 2;', 1)
        with self.assertRaises(overlay.OverlayBaselineError) as caught:
            overlay.render_navigation_overlay(changed, overlay.FRIGATE_VERSION)
        self.assertEqual(caught.exception.reason, 'nginx-baseline')
        with self.assertRaises(overlay.OverlayBaselineError):
            overlay.render_navigation_overlay(None, overlay.FRIGATE_VERSION)

    def test_old_frigate_pin_is_not_accepted(self):
        for version in ('0.18.0', '0.18.0-77a66e7', '0.18.2', '0.18.2-cfa08c56'):
            with self.assertRaises(overlay.OverlayBaselineError) as caught:
                overlay.render_navigation_overlay(self.baseline, version)
            self.assertEqual(caught.exception.reason, 'frigate-version')

    def test_public_source_excludes_private_runtime_material(self):
        for path in PRODUCT_SOURCE:
            text = path.read_text()
            for needle in FORBIDDEN:
                self.assertNotIn(needle, text, f'{path.name} contains {needle}')
        module = PRODUCT_SOURCE[0].read_text()
        self.assertNotIn('docker', module)
        self.assertNotIn('.write_text', module)
        self.assertNotIn('.write_bytes', module)

    def test_overlay_removal_leaves_the_upstream_baseline(self):
        before = FIXTURE.read_bytes()
        rendered = overlay.render_navigation_overlay(self.baseline, overlay.FRIGATE_VERSION)
        self.assertEqual(FIXTURE.read_bytes(), before)
        self.assertNotEqual(rendered, self.baseline)
        self.assertNotIn('__nanexus', self.baseline)
        self.assertEqual(hashlib.sha256(before).hexdigest(), overlay.NGINX_BASELINE_SHA256)


if __name__ == '__main__':
    unittest.main()
