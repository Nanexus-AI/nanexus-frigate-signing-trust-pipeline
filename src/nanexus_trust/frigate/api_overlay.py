"""Removable Frigate API overlay for one frozen baseline.

Rendering returns new file text. It does not edit the caller's strings, the
upstream image, or a container. Rollback is starting the pinned image without
the three overlay files.
"""
import hashlib
from pathlib import Path

FRIGATE_VERSION = '0.18.2-cfa08c5'
UPSTREAM_COMMIT = 'cfa08c56b23d23280c3d0a218e5a43182c97445b'
IMAGE_DIGEST = 'sha256:fd60f99ffd4eb6c42360ed4e14953e270586f239309ab3983e55b2e97ffac09a'
UPSTREAM_SHA256 = {
    'auth.py': 'eef3797d34709e33e0f6f659afb64575d2201d08d724246be25353314192b3a6',
    'fastapi_app.py': '344092d5f13ff93d5c2a928c13296efaf9c8ff3f6e48994476213c1d646f3cde',
}
ACCEPTED_SHA256 = {
    'auth.py': '4ce52d075188778395a76b0ba49fb9ff13fcff33c143273544e7095c1f3a62b5',
    'fastapi_app.py': '0a642ce6c3890a13abfd11738495e8964a6a74350ead5e20de3c74955a678d9d',
    'nanexus.py': 'd62bf6f96bfd84d24957082e47f716c8dcde0b1cc78aade8a9b2daaffdd0f79e',
}
AUTH_ANCHOR = '        "/profile",\n        "/profiles",\n'
AUTH_INSERT = (
    '        "/profile",\n'
    '        "/nanexus/live/status",  # Authenticated, route-level camera authorization\n'
    '        "/profiles",\n'
)
IMPORT_ANCHOR = '    motion_search,\n    notification,\n'
IMPORT_INSERT = '    motion_search,\n    nanexus,\n    notification,\n'
ROUTER_ANCHOR = '    app.include_router(auth.router)\n    app.include_router(camera.router)\n'
ROUTER_INSERT = (
    '    app.include_router(auth.router)\n'
    '    app.include_router(nanexus.router)\n'
    '    app.include_router(camera.router)\n'
)
OUTPUT_NAMES = ('auth.py', 'fastapi_app.py', 'nanexus.py')


class OverlayBaselineError(ValueError):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


def _sha256(text):
    if not isinstance(text, str):
        raise OverlayBaselineError('upstream-hash')
    return hashlib.sha256(text.encode()).hexdigest()


def _once(text, old, new, reason):
    if text.count(old) != 1:
        raise OverlayBaselineError(reason)
    return text.replace(old, new, 1)


def _module_text():
    path = Path(__file__).with_name('api_nanexus.py')
    text = path.read_text()
    if _sha256(text) != ACCEPTED_SHA256['nanexus.py']:
        raise OverlayBaselineError('api-module')
    if not text.startswith('"""Version-bound, authenticated exact-playback Live relay.'):
        raise OverlayBaselineError('api-module')
    return text


def render_api_overlay(auth_text, fastapi_text, frigate_version):
    """Return the accepted auth, application, and nanexus modules.

    ``auth_text`` and ``fastapi_text`` must be the unmodified files from
    ``FRIGATE_VERSION``. Any other version or content is refused.
    """
    if frigate_version != FRIGATE_VERSION:
        raise OverlayBaselineError('frigate-version')
    if _sha256(auth_text) != UPSTREAM_SHA256['auth.py'] or _sha256(fastapi_text) != UPSTREAM_SHA256['fastapi_app.py']:
        raise OverlayBaselineError('upstream-hash')
    if 'nanexus' in auth_text or 'nanexus' in fastapi_text:
        raise OverlayBaselineError('already-present')
    module = _module_text()
    auth = _once(auth_text, AUTH_ANCHOR, AUTH_INSERT, 'auth-anchor')
    fastapi = _once(fastapi_text, IMPORT_ANCHOR, IMPORT_INSERT, 'import-anchor')
    fastapi = _once(fastapi, ROUTER_ANCHOR, ROUTER_INSERT, 'router-anchor')
    rendered = {'auth.py': auth, 'fastapi_app.py': fastapi, 'nanexus.py': module}
    for name, digest in ACCEPTED_SHA256.items():
        if _sha256(rendered[name]) != digest or rendered[name].count('nanexus') < 1:
            raise OverlayBaselineError('render')
    if auth.count('"/nanexus/live/status"') != 1 or fastapi.count('nanexus.router') != 1:
        raise OverlayBaselineError('duplicate-route')
    if auth.replace(AUTH_INSERT, AUTH_ANCHOR, 1) != auth_text:
        raise OverlayBaselineError('unrelated-edit')
    restored = fastapi.replace(ROUTER_INSERT, ROUTER_ANCHOR, 1).replace(IMPORT_INSERT, IMPORT_ANCHOR, 1)
    if restored != fastapi_text:
        raise OverlayBaselineError('unrelated-edit')
    return rendered


def write_api_overlay(rendered, output_dir):
    """Write the three rendered files under ``output_dir/frigate/api``.

    ``output_dir`` must be an existing absolute directory outside this
    repository. Existing destinations are left untouched.
    """
    if set(rendered) != set(OUTPUT_NAMES):
        raise OverlayBaselineError('output-area')
    for name, digest in ACCEPTED_SHA256.items():
        if _sha256(rendered[name]) != digest:
            raise OverlayBaselineError('output-area')
    root = Path(output_dir)
    if not root.is_absolute() or not root.is_dir():
        raise OverlayBaselineError('output-area')
    resolved = root.resolve()
    repo = Path(__file__).resolve().parents[3]
    if resolved == repo or repo in resolved.parents:
        raise OverlayBaselineError('output-area')
    destination = resolved / 'frigate' / 'api'
    paths = [destination / name for name in OUTPUT_NAMES]
    if destination.exists() or any(path.exists() for path in paths):
        raise OverlayBaselineError('output-area')
    destination.mkdir(parents=True)
    for path, name in zip(paths, OUTPUT_NAMES):
        path.write_text(rendered[name])
    return destination
