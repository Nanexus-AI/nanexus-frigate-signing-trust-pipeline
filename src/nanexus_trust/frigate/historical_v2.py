"""Exact-profile offline Historical V2 foundation; no runtime/store discovery.

The trusted adapter supplies native authority and captured-snapshot provenance.
This implementation does not qualify historical origins or continuous file runs.
All failures are operational exceptions, never signing evidence.
"""
from dataclasses import dataclass
from decimal import Decimal
import hashlib
import math
import os
from pathlib import Path, PurePosixPath
import sqlite3
import stat
import tempfile
import time
from typing import Callable

from ..historical_v2.contracts import Camera, Interval, Quality
from ..historical_v2.provider import Capabilities, InventoryPage, Item, StableHandle

PROFILE = 'frigate-0.18.0-77a66e7-h264-mp4-offline-v1'
PIN = '77a66e75c61862b048a07c1295877f4b31343504'
COORDINATE = 'host-metadata-unix-seconds'
_COLUMNS = {
    'id': 'VARCHAR(30)', 'camera': 'VARCHAR(20)', 'path': 'VARCHAR(255)',
    'start_time': 'DATETIME', 'end_time': 'DATETIME', 'duration': 'REAL',
    'motion': 'INTEGER', 'objects': 'INTEGER', 'dBFS': 'INTEGER',
    'segment_size': 'REAL', 'regions': 'INTEGER', 'motion_heatmap': 'TEXT',
}

PIN_0182 = 'cfa08c56b23d23280c3d0a218e5a43182c97445b'
PROFILE_0182 = 'frigate-0.18.2-cfa08c5-h264-mp4-offline-v1'
_PROFILES = {PROFILE: (PIN, _COLUMNS),
             PROFILE_0182: (PIN_0182, _COLUMNS)}


class ProviderFailure(RuntimeError):
    """Adapter-local operational category, not a cryptographic verdict."""
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class Snapshot:
    path: Path
    sha256: str
    cut_id: str
    source_pin: str
    self_contained: bool
    complete: bool

    def __post_init__(self):
        if (self.source_pin not in (PIN, PIN_0182) or not self.cut_id or len(self.cut_id) > 256
                or len(self.sha256) != 64
                or any(c not in '0123456789abcdef' for c in self.sha256)):
            raise ProviderFailure('profile-or-provenance')
        if type(self.self_contained) is not bool or type(self.complete) is not bool:
            raise ProviderFailure('snapshot-provenance')


def _identity(st):
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns, st.st_nlink)


def _open(path):
    """Open an absolute POSIX path with no symlink component, including roots."""
    path = Path(path).absolute()
    if '..' in path.parts:
        raise ProviderFailure('unsafe-path')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for i, part in enumerate(path.parts[1:]):
            flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
            if i < len(path.parts) - 2:
                flags |= os.O_DIRECTORY
            child = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = child
        result, fd = fd, -1
        return result
    finally:
        if fd >= 0:
            os.close(fd)


def _seconds(value):
    if type(value) not in (float, int) or not Decimal(str(value)).is_finite():
        raise ProviderFailure('invalid-metadata')
    return format(Decimal(str(value)), 'f')


class FrigateHistoricalProvider:
    """Request-scoped, one-stage-item provider implementing A's protocol.

    native_authorize is mandatory trusted native integration, not browser input.
    It must check current native camera access AND binding generation. Snapshot
    provenance is likewise trusted adapter input, not a request parameter.
    Use as a context manager to clean staging even after a revoked operation.
    """
    inventory_page = staticmethod(InventoryPage)
    max_inventory_items = 128

    def __init__(self, camera: Camera, snapshot: Snapshot, media_root, scratch_root,
                 *, native_authorize: Callable[[Camera], bool], profile=PROFILE):
        if not callable(native_authorize):
            raise ProviderFailure('native-authority-required')
        if profile not in _PROFILES or snapshot.source_pin != _PROFILES[profile][0]:
            raise ProviderFailure('profile-or-provenance')
        self.profile = profile
        self.camera, self.snapshot = camera, snapshot
        self._authorize = native_authorize
        self.media_root = Path(media_root).absolute()
        self._items = {}
        self._handles = {}
        self._closed = False
        self._guard(camera)
        root = Path(scratch_root).absolute()
        fd = _open(root)
        try:
            st = os.fstat(fd)
            if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077:
                raise ProviderFailure('private-scratch-required')
        finally:
            os.close(fd)
        self._temporary = tempfile.TemporaryDirectory(prefix='hv2-', dir=root)
        self._root = Path(self._temporary.name)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        if not self._closed:
            self._handles.clear()
            self._items.clear()
            self._temporary.cleanup()
            self._closed = True

    def _guard(self, camera, bounds=None, cancel=None):
        if self._closed or camera != self.camera:
            raise ProviderFailure('camera-or-binding')
        if self._authorize(camera) is not True:
            raise ProviderFailure('access-denied-or-revoked')
        if cancel is not None and cancel.cancelled():
            raise ProviderFailure('cancelled')
        if bounds is not None:
            deadline = bounds.deadline_monotonic.numerator / bounds.deadline_monotonic.denominator
            if time.monotonic() >= deadline:
                raise ProviderFailure('deadline')

    def capabilities(self, camera):
        self._guard(camera)
        return Capabilities(camera, self.profile, True, True, True, False, True, 'h264', 'mp4-avc1')

    def discover(self, request, cancel):
        self._guard(request.camera, request.bounds, cancel)
        if request.coordinate != COORDINATE or request.cursor is not None:
            raise ProviderFailure('unsupported-coordinate-or-cursor')
        if self._handles:
            raise ProviderFailure('release-before-discovery')
        self._items.clear()
        b = request.bounds
        if b.max_items > self.max_inventory_items or b.max_context_items > self.max_inventory_items or b.max_items + b.max_context_items > self.max_inventory_items:
            raise ProviderFailure('inventory-output-cap')
        snap = self.snapshot
        if not snap.self_contained:
            raise ProviderFailure('self-contained-snapshot-required')
        fd = -1
        db = None
        steps = 0
        def tick():
            nonlocal steps
            steps += 1
            self._guard(request.camera, b, cancel)
            return int(steps > b.max_work_units)
        try:
            fd = _open(snap.path)
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode) or not 0 < st.st_size <= 16 * 1024 * 1024:
                raise ProviderFailure('snapshot-size-or-type')
            digest, snapshot_bytes = hashlib.sha256(), 0
            while True:
                self._guard(request.camera, b, cancel)
                chunk = os.read(fd, 65536)
                if not chunk:
                    break
                snapshot_bytes += len(chunk)
                steps += 1
                if snapshot_bytes > 16 * 1024 * 1024 or steps > b.max_work_units:
                    raise ProviderFailure('snapshot-work-cap')
                digest.update(chunk)
            if digest.hexdigest() != snap.sha256:
                raise ProviderFailure('snapshot-digest')
            # A trusted self-contained captured cut only. The held fd pins inode;
            # immutable is never used to open a live Frigate database here.
            db = sqlite3.connect(f'file:/proc/self/fd/{fd}?mode=ro&immutable=1', uri=True, timeout=0)
            db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 8192)
            db.set_progress_handler(tick, 1)
            db.execute('PRAGMA query_only=ON')
            db.execute('BEGIN')
            # SQLite REAL endpoints cannot represent every A decimal boundary.
            # Use outward index bounds, then exact stored-value decimal tests;
            # rounded query parameters must never manufacture empty coverage.
            def compare(value, boundary):
                left, right = Decimal(_seconds(value)), Decimal(boundary)
                return (left > right) - (left < right)
            db.create_function('hv2_cmp', 2, compare, deterministic=True)
            columns = db.execute('PRAGMA table_info(recordings)').fetchall()
            if ({r[1]: r[2].upper() for r in columns} != _PROFILES[self.profile][1]
                    or [r[1] for r in columns if r[5]] != ['id']):
                raise ProviderFailure('schema')
            indices = db.execute('PRAGMA index_list(recordings)').fetchall()
            if not any([r[2] for r in db.execute(f'PRAGMA index_info("{i[1]}")')] ==
                       ['camera', 'start_time', 'end_time'] for i in indices
                       if i[1] == 'recordings_camera_start_time_end_time'):
                raise ProviderFailure('schema-index')
            projection = 'SELECT id,camera,path,start_time,end_time,duration FROM recordings '
            scope = (self.camera.native_key,
                     math.nextafter(float(request.requested.end), math.inf),
                     math.nextafter(float(request.requested.start), -math.inf),
                     request.requested.end, request.requested.start)
            rows = db.execute(projection + 'WHERE camera=? AND start_time<? AND end_time>? '
                              'AND hv2_cmp(start_time,?)<0 AND hv2_cmp(end_time,?)>0 '
                              'ORDER BY start_time,end_time,id LIMIT ?', (*scope, b.max_items + 1)).fetchall()
            overflow = len(rows) > b.max_items
            rows = rows[:b.max_items]
            context = []
            # At most one predecessor and one successor; never a proof of a run.
            if b.max_context_items:
                pre = db.execute(projection + 'WHERE camera=? AND hv2_cmp(end_time,?)<=0 '
                                 'ORDER BY end_time DESC,start_time DESC,id DESC LIMIT 1',
                                 (self.camera.native_key, request.requested.start)).fetchall()
                post = db.execute(projection + 'WHERE camera=? AND hv2_cmp(start_time,?)>=0 '
                                  'ORDER BY start_time,end_time,id LIMIT 1',
                                  (self.camera.native_key, request.requested.end)).fetchall()
                context = [('pre', r) for r in pre] + [('post', r) for r in post]
                overflow |= len(context) > b.max_context_items
                context = context[:b.max_context_items]
            entries = [(None, r) for r in rows] + context
            items, predecessor, successor = [], [], []
            for role, row in entries:
                self._guard(request.camera, b, cancel)
                rid, camera, path, start, end, duration = row
                if camera != self.camera.native_key or type(duration) not in (int, float) or duration <= 0:
                    raise ProviderFailure('invalid-metadata')
                interval = Interval(_seconds(start), _seconds(end))
                if type(path) is not str or len(path.encode()) > 4096:
                    raise ProviderFailure('unsafe-path')
                native = PurePosixPath(path)
                prefix = PurePosixPath('/media/frigate/recordings')
                if '..' in native.parts or not native.is_relative_to(prefix) or native == prefix:
                    raise ProviderFailure('unsafe-path')
                relative = native.relative_to(prefix)
                gen = hashlib.sha256(repr((snap.cut_id, row)).encode()).hexdigest()
                item = Item(rid, gen, self.camera, interval, COORDINATE, None, False,
                            'h264', 'mp4-avc1', Quality.UNKNOWN, None, None)
                if rid in self._items:
                    raise ProviderFailure('duplicate-item')
                self._items[rid] = (item, relative, role)
                items.append(item)
                if role == 'pre': predecessor.append(rid)
                if role == 'post': successor.append(rid)
            if _identity(os.fstat(fd)) != _identity(st):
                raise ProviderFailure('snapshot-changed')
            self._guard(request.camera, b, cancel)
            # No timed/composition capability follows from metadata ordering.
            ordered = sorted(items, key=lambda i: (Decimal(i.metadata_interval.start), i.item_id))
            ambiguous = set()
            for i, left in enumerate(ordered):
                for right in ordered[i + 1:]:
                    if Decimal(right.metadata_interval.start) < Decimal(left.metadata_interval.end):
                        ambiguous.update((left.item_id, right.item_id))
            return self.inventory_page(snap.sha256, snap.cut_id, self.profile, self.camera, request.requested,
                                 Quality.INCOMPLETE if overflow else Quality.SATISFIED if snap.complete else Quality.UNKNOWN,
                                 Quality.SATISFIED, tuple(items), tuple(predecessor), tuple(successor),
                                 tuple(sorted(ambiguous)), None, True)
        except (OSError, sqlite3.Error, ValueError) as exc:
            self._items.clear()
            # SQLite swallows callback exceptions; restore cancellation/authority
            # category rather than interpreting interruption as empty coverage.
            self._guard(request.camera, b, cancel)
            raise ProviderFailure('inventory-work-cap' if steps > b.max_work_units else 'inventory-operational') from exc
        except BaseException:
            self._items.clear()
            raise
        finally:
            if db is not None: db.close()
            if fd >= 0: os.close(fd)

    def acquire(self, item, bounds, cancel):
        self._guard(item.camera, bounds, cancel)
        known = self._items.get(item.item_id)
        if known is None or known[0] != item:
            raise ProviderFailure('unknown-item-or-generation')
        if self._handles:
            raise ProviderFailure('one-staged-item-cap')
        limit = min(bounds.max_item_bytes, 32 * 1024 * 1024)
        if known[2] is not None:
            limit = min(limit, bounds.max_context_bytes)
        fd, dest = -1, self._root / 'media'
        try:
            source = self.media_root / known[1]
            fd = _open(source)
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= limit:
                raise ProviderFailure('media-size-or-type')
            digest, total, work = hashlib.sha256(), 0, 0
            with dest.open('xb') as out:
                while True:
                    self._guard(item.camera, bounds, cancel)
                    work += 1
                    if work > bounds.max_work_units: raise ProviderFailure('copy-work-cap')
                    data = os.read(fd, min(65536, limit - total + 1))
                    if not data: break
                    total += len(data)
                    if total > limit: raise ProviderFailure('media-byte-cap')
                    out.write(data)
                    digest.update(data)
            current = _open(source)
            try:
                if (_identity(before) != _identity(os.fstat(fd))
                        or _identity(before) != _identity(os.fstat(current)) or total != before.st_size):
                    raise ProviderFailure('source-changed')
            finally:
                os.close(current)
            self._guard(item.camera, bounds, cancel)
            handle = StableHandle(item.camera, item.item_id, item.generation,
                                  os.urandom(16).hex(), total, digest.hexdigest(), Quality.SATISFIED,
                                  'provider-owned-stage')
            self._handles[handle.handle_id] = (handle, dest, _identity(dest.stat()), bounds)
            return handle
        except BaseException as exc:
            dest.unlink(missing_ok=True)
            if isinstance(exc, OSError): raise ProviderFailure('source-unavailable-or-unsafe') from exc
            raise
        finally:
            if fd >= 0: os.close(fd)

    def read(self, handle, offset, max_bytes, cancel):
        self._guard(handle.camera, cancel=cancel)
        record = self._handles.get(handle.handle_id)
        if record is None or record[0] != handle:
            raise ProviderFailure('unknown-handle')
        _, path, identity, bounds = record
        self._guard(handle.camera, bounds, cancel)
        if (type(offset) is not int or not 0 <= offset <= handle.actual_bytes
                or type(max_bytes) is not int or not 0 < max_bytes <= min(bounds.max_item_bytes, 65536)):
            raise ProviderFailure('read-bounds')
        try:
            fd = _open(path)
            try:
                if _identity(os.fstat(fd)) != identity: raise ProviderFailure('stage-changed')
                result = os.pread(fd, min(max_bytes, handle.actual_bytes - offset), offset)
                if _identity(os.fstat(fd)) != identity: raise ProviderFailure('stage-changed')
            finally: os.close(fd)
            self._guard(handle.camera, bounds, cancel)
            return result
        except OSError as exc:
            raise ProviderFailure('stage-unavailable') from exc

    def release(self, handle):
        # Revocation must never prevent cleanup; forged handles cannot delete.
        record = self._handles.get(handle.handle_id)
        if record is not None and record[0] == handle:
            record[1].unlink(missing_ok=True)
            del self._handles[handle.handle_id]
