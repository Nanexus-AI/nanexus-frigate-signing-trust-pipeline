"""Focused checks for the removable Frigate 0.18.2 Live frontend patch."""
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from nanexus_trust.frigate import api_overlay
from nanexus_trust.frigate import live_frontend_overlay as overlay
from nanexus_trust.frigate import navigation_overlay

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'tests/fixtures/frigate-0.18.2-cfa08c5.web-src-main.tsx'
PRODUCT_SOURCE = (
    ROOT / 'src/nanexus_trust/frigate/live_frontend_overlay.py',
    *(ROOT / 'src/nanexus_trust/frigate/live_frontend' / relative
      for relative in overlay.ADDITION_SHA256),
)
FORBIDDEN = (
    '/home/', '.local', '192.168.', 'frigate-ui-admin', 'nanexus-3cam', 'live-token',
    'web/dist',
)
FLYOUT_MARKERS = (
    'Historical Verification',
    'id="nanexus-flyout"',
    '__nanexusHistorical',
    '__nanexusNavigation',
)


def _sha256(text):
    return hashlib.sha256(text.encode()).hexdigest()


class LiveFrontendOverlayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.upstream = FIXTURE.read_text()

    def test_version_and_upstream_commit_are_enforced(self):
        self.assertEqual(overlay.FRIGATE_VERSION, '0.18.2-cfa08c5')
        self.assertEqual(overlay.UPSTREAM_COMMIT, 'cfa08c56b23d23280c3d0a218e5a43182c97445b')
        self.assertEqual(_sha256(self.upstream), overlay.UPSTREAM_MAIN_SHA256)
        for version in ('0.18.0', '0.18.0-77a66e7', '0.18.2', '0.18.2-cfa08c56'):
            with self.assertRaises(overlay.OverlayBaselineError) as caught:
                overlay.render_live_frontend(self.upstream, version, overlay.UPSTREAM_COMMIT)
            self.assertEqual(caught.exception.reason, 'frigate-version')
        with self.assertRaises(overlay.OverlayBaselineError) as caught:
            overlay.render_live_frontend(self.upstream, overlay.FRIGATE_VERSION, overlay.UPSTREAM_COMMIT + 'ff')
        self.assertEqual(caught.exception.reason, 'upstream-commit')
        with self.assertRaises(overlay.OverlayBaselineError) as caught:
            overlay.render_live_frontend(self.upstream + '\n', overlay.FRIGATE_VERSION, overlay.UPSTREAM_COMMIT)
        self.assertEqual(caught.exception.reason, 'upstream-hash')

    def test_render_matches_the_accepted_sources(self):
        first = overlay.render_live_frontend(self.upstream, overlay.FRIGATE_VERSION, overlay.UPSTREAM_COMMIT)
        second = overlay.render_live_frontend(self.upstream, overlay.FRIGATE_VERSION, overlay.UPSTREAM_COMMIT)
        self.assertEqual(first, second)
        self.assertEqual(tuple(first), overlay.APPROVED_PATHS)
        self.assertEqual(_sha256(first[overlay.MAIN_PATH]), overlay.ACCEPTED_MAIN_SHA256)
        self.assertEqual(
            first[overlay.MAIN_PATH].replace(overlay.ENTRY_INSERT, overlay.ENTRY_ANCHOR, 1),
            self.upstream)
        self.assertEqual(first[overlay.MAIN_PATH].count('installNanexusLiveEntry'), 2)
        for relative, digest in overlay.ADDITION_SHA256.items():
            self.assertEqual(_sha256(first[relative]), digest)
            self.assertEqual(
                _sha256((ROOT / 'src/nanexus_trust/frigate/live_frontend' / relative).read_text()),
                digest)

    def test_patch_leaves_every_other_frontend_path_untouched(self):
        rendered = overlay.render_live_frontend(self.upstream, overlay.FRIGATE_VERSION, overlay.UPSTREAM_COMMIT)
        self.assertEqual(set(rendered), set(overlay.APPROVED_PATHS))
        self.assertTrue(set(overlay.UNCHANGED_MANIFESTS).isdisjoint(rendered))
        source = (ROOT / 'src/nanexus_trust/frigate/live_frontend_overlay.py').read_text()
        self.assertIn("UNCHANGED_MANIFESTS = ('web/package.json', 'web/package-lock.json', 'web/vite.config.ts')", source)
        for relative in overlay.ADDITION_SHA256:
            self.assertFalse(relative.startswith('web/package'))
            self.assertNotIn('vite.config', relative)

    def test_duplicate_or_unknown_input_is_rejected(self):
        rendered = overlay.render_live_frontend(self.upstream, overlay.FRIGATE_VERSION, overlay.UPSTREAM_COMMIT)
        with self.assertRaises(overlay.OverlayBaselineError) as caught:
            overlay.render_live_frontend(rendered[overlay.MAIN_PATH], overlay.FRIGATE_VERSION, overlay.UPSTREAM_COMMIT)
        self.assertEqual(caught.exception.reason, 'upstream-hash')
        with patch.object(overlay, 'UPSTREAM_MAIN_SHA256', overlay.ACCEPTED_MAIN_SHA256):
            with self.assertRaises(overlay.OverlayBaselineError) as caught:
                overlay.render_live_frontend(
                    rendered[overlay.MAIN_PATH], overlay.FRIGATE_VERSION, overlay.UPSTREAM_COMMIT)
        self.assertEqual(caught.exception.reason, 'already-present')
        doubled = overlay.ENTRY_ANCHOR + overlay.ENTRY_ANCHOR
        with patch.object(overlay, 'UPSTREAM_MAIN_SHA256', _sha256(doubled)):
            with self.assertRaises(overlay.OverlayBaselineError) as caught:
                overlay.render_live_frontend(doubled, overlay.FRIGATE_VERSION, overlay.UPSTREAM_COMMIT)
        self.assertEqual(caught.exception.reason, 'entry-anchor')
        tampered = dict(overlay.ADDITION_SHA256)
        tampered['web/src/api/live-signing.ts'] = '0' * 64
        with patch.object(overlay, 'ADDITION_SHA256', tampered):
            with self.assertRaises(overlay.OverlayBaselineError) as caught:
                overlay.render_live_frontend(self.upstream, overlay.FRIGATE_VERSION, overlay.UPSTREAM_COMMIT)
        self.assertEqual(caught.exception.reason, 'addition-hash')

    def test_output_stays_outside_the_repository(self):
        rendered = overlay.render_live_frontend(self.upstream, overlay.FRIGATE_VERSION, overlay.UPSTREAM_COMMIT)
        with tempfile.TemporaryDirectory() as temporary:
            destination = overlay.write_live_frontend(rendered, temporary)
            self.assertEqual(destination, Path(temporary).resolve())
            for relative, text in rendered.items():
                self.assertEqual((destination / relative).read_text(), text)
            with self.assertRaises(overlay.OverlayBaselineError) as caught:
                overlay.write_live_frontend(rendered, temporary)
            self.assertEqual(caught.exception.reason, 'output-area')
        with self.assertRaises(overlay.OverlayBaselineError):
            overlay.write_live_frontend(rendered, ROOT)
        with self.assertRaises(overlay.OverlayBaselineError):
            overlay.write_live_frontend(rendered, 'relative-output')

    def test_public_source_excludes_private_runtime_material(self):
        for path in PRODUCT_SOURCE:
            text = path.read_text()
            for needle in FORBIDDEN:
                self.assertNotIn(needle, text, f'{path.name} contains {needle}')
        self.assertNotIn('docker', (ROOT / 'src/nanexus_trust/frigate/live_frontend_overlay.py').read_text())
        rendered = overlay.render_live_frontend(self.upstream, overlay.FRIGATE_VERSION, overlay.UPSTREAM_COMMIT)
        for relative in rendered:
            self.assertNotIn('dist', Path(relative).parts)

    def test_flyout_and_historical_ui_stay_in_the_committed_assets(self):
        self.assertEqual(navigation_overlay.FRIGATE_VERSION, overlay.FRIGATE_VERSION)
        self.assertEqual(navigation_overlay.UPSTREAM_COMMIT, overlay.UPSTREAM_COMMIT)
        self.assertEqual(set(navigation_overlay.UI_SHA256), {
            'navigation.js', 'navigation-live.css', 'historical.js', 'navigation.css',
        })
        for name, digest in navigation_overlay.UI_SHA256.items():
            self.assertEqual(hashlib.sha256((ROOT / 'ui/nanexus' / name).read_bytes()).hexdigest(), digest)
        rendered = overlay.render_live_frontend(self.upstream, overlay.FRIGATE_VERSION, overlay.UPSTREAM_COMMIT)
        for name in navigation_overlay.UI_SHA256:
            self.assertNotIn(name, rendered)
        for relative, text in rendered.items():
            if relative == overlay.MAIN_PATH:
                continue
            for marker in FLYOUT_MARKERS:
                self.assertNotIn(marker, text, relative)
        entry = rendered['web/src/components/signing/nanexus-live-entry.tsx']
        self.assertIn('target.__nanexusLive', entry)
        self.assertIn('createRoot(element)', entry)
        self.assertIn('root.unmount()', entry)
        self.assertIn('window.__nanexusLive', (ROOT / 'ui/nanexus/navigation.js').read_text())
        self.assertNotIn('__nanexusLive', (ROOT / 'ui/nanexus/historical.js').read_text())

    def test_accepted_overlays_remain_independent(self):
        self.assertEqual(api_overlay.FRIGATE_VERSION, '0.18.2-cfa08c5')
        self.assertEqual(api_overlay.UPSTREAM_COMMIT, overlay.UPSTREAM_COMMIT)
        self.assertEqual(
            navigation_overlay.NGINX_BASELINE_SHA256,
            '563a6e4b5219b7d3a3b9d6992ebf1f5e1ac27b5be570be903a26fe5697d9a7fd')


if __name__ == '__main__':
    unittest.main()
