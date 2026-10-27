"""Removable Live Monitor source patch for one frozen Frigate baseline.

Rendering returns new frontend text. It does not edit the caller's string,
the upstream checkout, or a container. The six additions are Nanexus-owned.
``web/src/main.tsx`` is the only upstream file, and the edit is the entry call.

A later build that combines this patch with upstream Frigate web source
redistributes that MIT-licensed frontend. The Frigate copyright notice must
travel with that build. This module does not vendor upstream fonts, icons,
images, logos, or compiled frontend output.
"""
import hashlib
from pathlib import Path

FRIGATE_VERSION = '0.18.2-cfa08c5'
UPSTREAM_COMMIT = 'cfa08c56b23d23280c3d0a218e5a43182c97445b'
MAIN_PATH = 'web/src/main.tsx'
UPSTREAM_MAIN_SHA256 = 'b1e82a785dab0e45f11840e58690cb230255c6724056750aa10da8dbf445aa0f'
ACCEPTED_MAIN_SHA256 = 'fa25c188d6a2afc00966237b6f511fcd026748009c4af2c5efb6c276376d5d64'
ENTRY_ANCHOR = 'import "react-i18next";\n\nReactDOM.createRoot(document.getElementById("root")!).render(\n'
ENTRY_INSERT = (
    'import "react-i18next";\n'
    'import { installNanexusLiveEntry } from "@/components/signing/nanexus-live-entry";\n'
    '\n'
    'installNanexusLiveEntry();\n'
    '\n'
    'ReactDOM.createRoot(document.getElementById("root")!).render(\n'
)
ADDITION_SHA256 = {
    'web/src/api/live-signing.ts': '3ac5a35e8ea9d2fc67467550e0f2af00c622caab103a143626682eb396a2620a',
    'web/src/components/signing/SigningMonitor.tsx': '4543cb7b90057df2d44806c11eb0748df283f8e0bb3f64079136caa63fb9b7ae',
    'web/src/components/signing/SigningStatus.tsx': '3dbc64fac7238ba6d0f45fc0cc4e578dff833d7a1afa7134d0f705ed0c59bc1c',
    'web/src/components/signing/monitor-layout.ts': 'a2af261d093442b08299980139cb8163b7e79b15b2d87c4564d9fa9c2872be9a',
    'web/src/components/signing/monitor-snapshot.ts': 'b25c82e8ec28f3c4c8b515f374dada758355ba0ff3a5dd5f20252eb3c46c73ea',
    'web/src/components/signing/nanexus-live-entry.tsx': '1a5acc2ff744216bd869fa3880bfccc7df3c53f5e20eaffe59482e907479e523',
}
APPROVED_PATHS = (MAIN_PATH, *ADDITION_SHA256)
UNCHANGED_MANIFESTS = ('web/package.json', 'web/package-lock.json', 'web/vite.config.ts')
SOURCE_ROOT = Path(__file__).with_name('live_frontend')


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


def _additions():
    found = {}
    for relative, digest in ADDITION_SHA256.items():
        path = SOURCE_ROOT / relative
        try:
            text = path.read_text()
        except OSError as exc:
            raise OverlayBaselineError('addition-hash') from exc
        if _sha256(text) != digest or not text.endswith('\n'):
            raise OverlayBaselineError('addition-hash')
        found[relative] = text
    return found


def render_live_frontend(main_text, frigate_version, upstream_commit):
    """Return the accepted entry file and the six Live Monitor additions.

    ``main_text`` must be unmodified ``web/src/main.tsx`` from
    ``UPSTREAM_COMMIT`` at ``FRIGATE_VERSION``. Any other version, commit,
    or file is refused.
    """
    if frigate_version != FRIGATE_VERSION:
        raise OverlayBaselineError('frigate-version')
    if upstream_commit != UPSTREAM_COMMIT:
        raise OverlayBaselineError('upstream-commit')
    if _sha256(main_text) != UPSTREAM_MAIN_SHA256:
        raise OverlayBaselineError('upstream-hash')
    if 'installNanexusLiveEntry' in main_text or 'nanexus-live-entry' in main_text:
        raise OverlayBaselineError('already-present')
    rendered_main = _once(main_text, ENTRY_ANCHOR, ENTRY_INSERT, 'entry-anchor')
    if rendered_main.replace(ENTRY_INSERT, ENTRY_ANCHOR, 1) != main_text:
        raise OverlayBaselineError('unrelated-edit')
    if _sha256(rendered_main) != ACCEPTED_MAIN_SHA256:
        raise OverlayBaselineError('render')
    rendered = {MAIN_PATH: rendered_main, **_additions()}
    if tuple(rendered) != APPROVED_PATHS:
        raise OverlayBaselineError('render')
    for manifest in UNCHANGED_MANIFESTS:
        if manifest in rendered:
            raise OverlayBaselineError('manifest')
    return rendered


def write_live_frontend(rendered, output_dir):
    """Write the seven frontend files under ``output_dir``.

    ``output_dir`` must be an existing absolute directory outside this
    repository. Existing destinations are left untouched.
    """
    if set(rendered) != set(APPROVED_PATHS):
        raise OverlayBaselineError('output-area')
    if _sha256(rendered[MAIN_PATH]) != ACCEPTED_MAIN_SHA256:
        raise OverlayBaselineError('output-area')
    for relative, digest in ADDITION_SHA256.items():
        if _sha256(rendered[relative]) != digest:
            raise OverlayBaselineError('output-area')
    root = Path(output_dir)
    if not root.is_absolute() or not root.is_dir():
        raise OverlayBaselineError('output-area')
    resolved = root.resolve()
    repo = Path(__file__).resolve().parents[3]
    if resolved == repo or repo in resolved.parents:
        raise OverlayBaselineError('output-area')
    paths = [resolved / relative for relative in APPROVED_PATHS]
    if any(path.exists() for path in paths):
        raise OverlayBaselineError('output-area')
    for path, relative in zip(paths, APPROVED_PATHS):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rendered[relative])
    return resolved
