"""Pinned read-only SQLite inventory and owned whole-file staging (POSIX)."""

from contextlib import closing
from dataclasses import replace
from decimal import Decimal
import hashlib
import os
from pathlib import Path
import sqlite3
import stat
import tempfile
import time

from ..contracts import CameraId, Interval, decimal_seconds, identifier
from ..historical.models import Availability, HistoricalError, Inventory, RetainedDescriptor
from .metadata import FrozenFrigateSource

# Frigate v0.18.0 migrations 003,006,012,018,023,035 and models.Recordings.
RECORDING_COLUMNS = {
    'id': 'VARCHAR(30)', 'camera': 'VARCHAR(20)', 'path': 'VARCHAR(255)',
    'start_time': 'DATETIME', 'end_time': 'DATETIME', 'duration': 'REAL',
    'motion': 'INTEGER', 'objects': 'INTEGER', 'dBFS': 'INTEGER',
    'segment_size': 'REAL', 'regions': 'INTEGER', 'motion_heatmap': 'TEXT',
}


def private_directory(path):
    """Operator-owned private roots; no symlink ancestors or shared writable dirs."""
    path = Path(path).absolute()
    if '..' in path.parts:
        raise ValueError('normalized storage root required')
    for parent in (*reversed(path.parents), path):
        if parent.is_symlink():
            raise ValueError('symlink storage root')
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.stat().st_uid != os.getuid() or path.stat().st_mode & 0o077:
        raise ValueError('storage root must be owned and mode 0700')
    return path


def _seconds(value):
    if type(value) not in (int, float):
        raise ValueError('numeric Frigate timestamps required')
    text = format(Decimal(str(value)), 'f')
    decimal_seconds(text)
    return text


class FrigateRetainedSource:
    """One request-scoped inventory. No immutable=1, backup-copy or DB write path.

    DB must be the real shared database (including live WAL/SHM), not an unmanaged
    copy. WAL without readable sidecars is rejected. Retention is not frozen by
    the SQL transaction: all READY files are private copies, never original paths.
    """

    def __init__(self, database, media_root, scratch_root, requested: Interval, *,
                 version, recording_mount='/media/frigate/recordings', settle_seconds=1.0):
        if version != '0.18.0':
            raise ValueError('only Frigate 0.18.0 is supported')
        if not isinstance(requested, Interval) or not 0 <= settle_seconds <= 60:
            raise ValueError('explicit range and bounded stability interval required')
        self.database = Path(database).absolute()
        self.paths = FrozenFrigateSource((), Path(media_root), recording_mount)
        self.requested = requested
        self.settle_ns = int(settle_seconds * 1e9)
        self._temporary = tempfile.TemporaryDirectory(prefix='retained-', dir=private_directory(scratch_root))
        self.root = Path(self._temporary.name)
        self._inventory = None
        self._camera = None
        self._staged = {}
        self._closed = False

    def _rows(self, camera, limits):
        path = self.database
        try:
            for parent in (*reversed(path.parents), path):
                if parent.is_symlink():
                    raise OSError('symlink database')
            if not path.is_file():
                raise OSError('database unavailable')
            before = path.stat()
            # Check before sqlite opens: even mode=ro may create missing WAL/SHM
            # on a writable test/deployment directory. A main-only WAL copy is
            # not a supported snapshot and must never be silently opened.
            with path.open('rb') as header:
                wal_header = header.read(20)[18:20] == b'\x02\x02'
            if wal_header:
                for suffix in ('-wal', '-shm'):
                    sidecar = Path(str(path) + suffix)
                    if sidecar.is_symlink() or not sidecar.is_file():
                        raise OSError('live WAL sidecars required')
                    with sidecar.open('rb') as stream:
                        stream.read(1)
            with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=0.2)) as db:
                db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 8192)
                db.execute('PRAGMA query_only=ON')
                deadline = time.monotonic() + min(limits.timeout_seconds, 5)
                db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
                db.execute('BEGIN')
                mode = db.execute('PRAGMA journal_mode').fetchone()[0]
                if mode == 'wal':
                    for suffix in ('-wal', '-shm'):
                        sidecar = Path(str(path) + suffix)
                        if sidecar.is_symlink() or not sidecar.is_file():
                            raise OSError('live WAL sidecars required')
                        with sidecar.open('rb') as stream:
                            stream.read(1)
                columns = db.execute('PRAGMA table_info(recordings)').fetchall()
                if {r[1]: r[2].upper() for r in columns} != RECORDING_COLUMNS:
                    raise HistoricalError('UNSUPPORTED_FRIGATE_SCHEMA')
                if next(r for r in columns if r[1] == 'id')[5] != 1:
                    raise HistoricalError('UNSUPPORTED_FRIGATE_SCHEMA')
                # Exact projection: reject incompatible type/schema, not partial parsing.
                rows = db.execute('SELECT id,camera,start_time,end_time,path FROM recordings '
                                  'WHERE camera=? AND end_time>? '
                                  'ORDER BY start_time,end_time,id LIMIT ?',
                                  (camera.value, self.requested.start, limits.max_rows + 1)).fetchall()
                after = path.stat()
                if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
                    raise OSError('database replaced')
                db.rollback()
            if len(rows) > limits.max_rows:
                raise HistoricalError('RESOURCE_LIMIT')
            return rows
        except (OSError, sqlite3.Error) as exc:
            raise HistoricalError('METADATA_UNAVAILABLE') from exc

    def _open(self, relative):
        # Resolve each component using dirfd and O_NOFOLLOW, including configured
        # root ancestors. This rejects directory swaps/symlink races, not just lstat.
        root = self.paths.media_root.absolute()
        fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
        try:
            for part in (*root.parts[1:], *Path(relative).parts[:-1]):
                new = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd); fd = new
            return os.open(Path(relative).name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        finally:
            os.close(fd)

    @staticmethod
    def _identity(info):
        return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_nlink

    def _copy(self, source, output, maximum):
        """Bounded copy seam; fault tests mutate originals here."""
        total = 0
        digest = hashlib.sha256()
        while True:
            data = os.read(source, min(65536, maximum - total + 1))
            if not data:
                return total, digest.hexdigest()
            total += len(data)
            if total > maximum:
                raise HistoricalError('RESOURCE_LIMIT')
            output.write(data); digest.update(data)

    def _stage(self, descriptor, limits):
        target = self.root / descriptor.recording_id
        try:
            with os.fdopen(self._open(descriptor.relative_path), 'rb', buffering=0) as source:
                before = os.fstat(source.fileno())
                if (not stat.S_ISREG(before.st_mode) or before.st_size <= 0
                        or time.time_ns() - max(before.st_mtime_ns, before.st_ctime_ns) < self.settle_ns):
                    return replace(descriptor, availability=Availability.NOT_READY)
                if before.st_size > limits.max_bytes:
                    return replace(descriptor, availability=Availability.UNAVAILABLE)
                with target.open('xb') as output:
                    size, digest = self._copy(source.fileno(), output, limits.max_bytes)
                after = os.fstat(source.fileno())
                with os.fdopen(self._open(descriptor.relative_path), 'rb', buffering=0) as current:
                    live = os.fstat(current.fileno())
                if (size != before.st_size or self._identity(before) != self._identity(after)
                        or self._identity(before) != self._identity(live)):
                    target.unlink()
                    return replace(descriptor, availability=Availability.NOT_READY)
                self._staged[descriptor.recording_id] = target
                return replace(descriptor, availability=Availability.READY, size=size, sha256=digest)
        except FileNotFoundError:
            target.unlink(missing_ok=True)
            return replace(descriptor, availability=Availability.MISSING)
        except OSError:
            target.unlink(missing_ok=True)
            return replace(descriptor, availability=Availability.UNAVAILABLE)

    def load(self, camera: CameraId, limits):
        if self._closed:
            raise HistoricalError('METADATA_UNAVAILABLE')
        if self._inventory is not None:
            if camera != self._camera:
                raise HistoricalError('METADATA_UNAVAILABLE')
            return self._inventory
        rows = self._rows(camera, limits)
        descriptors, ids, paths = [], set(), set()
        try:
            for rid, cam, start, end, path in rows:
                identifier(rid)
                relative = self.paths._relative(path)
                if rid in ids or relative in paths or CameraId(cam) != camera:
                    raise ValueError('ambiguous recording')
                ids.add(rid); paths.add(relative)
                descriptors.append(RetainedDescriptor(rid, camera, Interval(_seconds(start), _seconds(end)),
                                                      relative, Availability.NOT_READY))
        except (TypeError, ValueError) as exc:
            raise HistoricalError('METADATA_UNAVAILABLE') from exc
        descriptors.sort(key=lambda d: (decimal_seconds(d.interval.start), decimal_seconds(d.interval.end), d.recording_id))
        # Keep every requested overlap and only bounded forward expansion candidates.
        overlapping = [i for i, d in enumerate(descriptors) if d.interval.overlaps(self.requested)]
        selected = [] if not overlapping else descriptors[overlapping[0]:overlapping[-1] + 2 + limits.max_expansions]
        if overlapping and overlapping[-1] - overlapping[0] + 1 > limits.max_files:
            raise HistoricalError('RESOURCE_LIMIT')
        # One un-staged lookahead row preserves the core's distinction between
        # true MEDIA_EXHAUSTED and a resource-limited expansion.
        initial_count = 0 if not overlapping else overlapping[-1] - overlapping[0] + 1
        stage_count = min(initial_count + limits.max_expansions, limits.max_files)
        selected = selected[:stage_count + 1]
        staged, total = [], 0
        for index, descriptor in enumerate(selected):
            if index >= stage_count:
                staged.append(descriptor)
                continue
            remaining = limits.max_inventory_bytes - total
            if remaining <= 0:
                raise HistoricalError('RESOURCE_LIMIT')
            current = self._stage(descriptor, replace(limits, max_bytes=min(limits.max_bytes, remaining)))
            total += current.size
            if total > limits.max_inventory_bytes:
                raise HistoricalError('RESOURCE_LIMIT')
            staged.append(current)
        self._camera = camera
        self._inventory = Inventory(tuple(staged), True)
        return self._inventory

    def read(self, descriptor, limits):
        if self._closed or descriptor.availability is not Availability.READY:
            raise HistoricalError('MEDIA_NOT_READY')
        target = self._staged.get(descriptor.recording_id)
        if target is None:
            raise HistoricalError('MEDIA_UNAVAILABLE')
        try:
            data = FrozenFrigateSource._read(target, limits.max_bytes)
            if len(data) != descriptor.size or hashlib.sha256(data).hexdigest() != descriptor.sha256:
                raise HistoricalError('MEDIA_CHANGED')
            return data
        except OSError as exc:
            raise HistoricalError('MEDIA_UNAVAILABLE') from exc

    def close(self):
        self._closed = True
        self._temporary.cleanup()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
