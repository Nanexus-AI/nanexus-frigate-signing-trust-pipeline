"""Removable nginx and static-asset overlay for one Frigate baseline.

Rendering returns a new configuration. It does not edit the caller's text or
the upstream image. Rollback is serving the pinned unmodified image, whose
nginx configuration remains the baseline hash below.
"""
import hashlib
from pathlib import Path

FRIGATE_VERSION = '0.18.2-cfa08c5'
UPSTREAM_COMMIT = 'cfa08c56b23d23280c3d0a218e5a43182c97445b'
NGINX_BASELINE_SHA256 = '563a6e4b5219b7d3a3b9d6992ebf1f5e1ac27b5be570be903a26fe5697d9a7fd'
ASSET_ALIAS = '/nanexus-navigation/'
UI_DIRECTORY = Path('ui/nanexus')
UI_SHA256 = {
    'navigation.js': 'fd6ecdc738bb72fc4d6029361ac6424f7f3b3ad479fda2a24ff080a1c11451d9',
    'navigation-live.css': 'c24c246f23ee999d6bad22306833a14a4b086c3ec287c43660f1c2f47b6301e2',
    'historical.js': 'b23512a112527b2180687d42b7886d983edd78e284539ff2ef8210acafecdd0d',
    'navigation.css': '997c3e01dc2ad98b56e2ac15099c647c4891f93c48efe1a7a884e457ca7e3b22',
}
MAP_ANCHOR = '    map_hash_bucket_size 256;\n\n    include mime.types;\n'
MAP_INSERT = (
    '    map_hash_bucket_size 256;\n'
    '\n'
    '    map $cookie_frigate_token $nanexus_cookie_markup { "" ""; default \'<link rel="stylesheet" href="/__nanexus/navigation.css"><link rel="stylesheet" href="/__nanexus/navigation-live.css"><script defer src="/__nanexus/historical.js"></script><script defer src="/__nanexus/navigation.js"></script>\'; }\n'
    '    map $uri $nanexus_markup { /login ""; /login.html ""; default $nanexus_cookie_markup; }\n'
    '\n'
    '    include mime.types;\n'
)
LOCATION_ANCHOR = '        }\n\n        location / {\n            # do not require auth for static assets\n'
BODY_ANCHOR = "            sub_filter '<body>' '<body><script>window.baseUrl=\"$http_x_ingress_path/\";</script>';\n"
BODY_INSERT = "            sub_filter '<body>' '<body><script>window.baseUrl=\"$http_x_ingress_path/\";</script>$nanexus_markup';\n"
ROUTES = (
    'location = /__nanexus/window/verify',
    'location = /__nanexus/window/cancel',
    'location = /__nanexus/window/status',
    'location = /__nanexus/window/progress',
    'location = /__nanexus/window/result',
    'location = /__nanexus/window/release',
    'location = /__nanexus/session',
    'location ^~ /__nanexus/',
)


class OverlayBaselineError(ValueError):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


def _locations():
    path = Path(__file__).with_name('navigation_locations.nginx')
    text = path.read_text()
    if not text.startswith('        location = /__nanexus/window/verify {') or not text.endswith('        }\n'):
        raise OverlayBaselineError('location-fragment')
    return text


def _once(text, old, new, reason):
    if text.count(old) != 1:
        raise OverlayBaselineError(reason)
    return text.replace(old, new, 1)


def render_navigation_overlay(baseline, frigate_version):
    """Return nginx text with the accepted Nanexus routes added.

    ``baseline`` must be the unmodified nginx configuration of
    ``FRIGATE_VERSION``. Any other version or file is refused.
    """
    if frigate_version != FRIGATE_VERSION or not isinstance(baseline, str):
        raise OverlayBaselineError('frigate-version')
    if hashlib.sha256(baseline.encode()).hexdigest() != NGINX_BASELINE_SHA256:
        raise OverlayBaselineError('nginx-baseline')
    if '__nanexus' in baseline or 'nanexus_markup' in baseline:
        raise OverlayBaselineError('already-present')
    locations = _locations()
    rendered = _once(baseline, MAP_ANCHOR, MAP_INSERT, 'map-anchor')
    rendered = _once(
        rendered, LOCATION_ANCHOR,
        '        }\n\n\n\n' + locations + '\n        location / {\n            # do not require auth for static assets\n',
        'location-anchor')
    rendered = _once(rendered, BODY_ANCHOR, BODY_INSERT, 'body-anchor')
    for route in ROUTES:
        if rendered.count(route) != 1:
            raise OverlayBaselineError('duplicate-route')
    if 'worker_processes auto;' not in rendered or 'worker_processes 4;' in rendered:
        raise OverlayBaselineError('unrelated-edit')
    return rendered


def navigation_assets(pipeline_root):
    """Read the four committed UI files. Their bytes are the asset authority."""
    root = Path(pipeline_root)
    found = {}
    for name, digest in UI_SHA256.items():
        path = root / UI_DIRECTORY / name
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise OverlayBaselineError('ui-asset') from exc
        if hashlib.sha256(data).hexdigest() != digest:
            raise OverlayBaselineError('ui-asset')
        found[name] = path
    return found
