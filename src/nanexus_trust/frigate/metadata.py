"""Translate a caller-owned coherent, frozen Frigate row snapshot to descriptors."""

import hashlib
from pathlib import Path, PurePosixPath
import re

from ..contracts import CameraId, Interval, decimal_seconds, identifier
from ..historical.models import Availability, HistoricalError, Inventory, RetainedDescriptor


class FrozenFrigateSource:
    """Rows use camera/id/start_time/end_time/path and explicit closed readiness.

    Time endpoints are decimal strings. JSON loaders must preserve decimal precision
    before supplying rows. No filenames, live SQLite/WAL or filesystem enumeration
    supply metadata. The caller attests coherence and closure; M1-D must establish it.
    """

    def __init__(self, rows, media_root: Path, recording_mount="/media/frigate/recordings",
                 *, coherent=True, complete=True):
        if type(rows) is not tuple or type(coherent) is not bool or type(complete) is not bool:
            raise ValueError("immutable row sequence and explicit snapshot assertions required")
        # Copy input records so later caller dictionary mutation cannot change the snapshot.
        self.rows = tuple(dict(row) for row in rows)
        self.media_root = Path(media_root)
        if ".." in self.media_root.parts:
            raise ValueError("normalized retained root required")
        self.mount = PurePosixPath(recording_mount)
        if not self.mount.is_absolute() or ".." in self.mount.parts:
            raise ValueError("absolute normalized recording mount required")
        self.coherent = coherent
        self.complete = complete

    def _relative(self, value):
        if type(value) is not str or "\\" in value or "\x00" in value:
            raise HistoricalError("UNSAFE_MEDIA_PATH")
        parts = value.split("/")
        if any(p in (".", "..") for p in parts) or "//" in value:
            raise HistoricalError("UNSAFE_MEDIA_PATH")
        try:
            relative = PurePosixPath(value).relative_to(self.mount)
        except ValueError as exc:
            raise HistoricalError("UNSAFE_MEDIA_PATH") from exc
        if not relative.parts or any(not re.fullmatch(r"[A-Za-z0-9_.-]+", p) for p in relative.parts):
            raise HistoricalError("UNSAFE_MEDIA_PATH")
        return relative.as_posix()

    def _path(self, relative):
        # Reject symlinks in configured root as well as every media path component.
        root = self.media_root.absolute()
        for path in (*reversed(root.parents), root):
            if path.is_symlink():
                raise HistoricalError("UNSAFE_MEDIA_PATH")
        target = root
        for part in PurePosixPath(relative).parts:
            if part in (".", "..") or not re.fullmatch(r"[A-Za-z0-9_.-]+", part):
                raise HistoricalError("UNSAFE_MEDIA_PATH")
            target = target / part
            if target.is_symlink():
                raise HistoricalError("UNSAFE_MEDIA_PATH")
        if not target.is_relative_to(root):
            raise HistoricalError("UNSAFE_MEDIA_PATH")
        return target

    def load(self, camera, limits):
        if not self.coherent:
            raise HistoricalError("METADATA_UNAVAILABLE")
        if len(self.rows) > limits.max_rows:
            raise HistoricalError("RESOURCE_LIMIT")
        descriptors = []
        total_bytes = 0
        ids, paths = set(), set()
        try:
            for row in self.rows:
                if CameraId(row["camera"]) != camera:
                    continue
                rid = identifier(row["id"])
                interval = Interval(row["start_time"], row["end_time"])
                relative = self._relative(row["path"])
                if rid in ids or relative in paths or type(row["closed"]) is not bool:
                    raise HistoricalError("METADATA_UNAVAILABLE")
                ids.add(rid); paths.add(relative)
                target = self._path(relative)
                if not target.is_file():
                    state, size, digest = Availability.MISSING, 0, None
                elif not row["closed"]:
                    state, size, digest = Availability.NOT_READY, 0, None
                else:
                    size = target.stat().st_size
                    total_bytes += size
                    if total_bytes > limits.max_inventory_bytes:
                        raise HistoricalError("RESOURCE_LIMIT")
                    if size > limits.max_bytes:
                        state, digest = Availability.UNAVAILABLE, None
                    elif size == 0:
                        state, digest = Availability.NOT_READY, None
                    else:
                        digest = hashlib.sha256(self._read(target, limits.max_bytes)).hexdigest()
                        state = Availability.READY
                descriptors.append(RetainedDescriptor(rid, camera, interval, relative, state, size, digest))
        except (OSError, KeyError, ValueError, TypeError) as exc:
            raise HistoricalError("METADATA_UNAVAILABLE") from exc
        descriptors.sort(key=lambda d: (decimal_seconds(d.interval.start), decimal_seconds(d.interval.end), d.recording_id))
        return Inventory(tuple(descriptors), self.complete)

    @staticmethod
    def _read(path, maximum):
        with path.open("rb") as stream:
            data = stream.read(maximum + 1)
        if len(data) > maximum:
            raise HistoricalError("RESOURCE_LIMIT")
        return data

    def read(self, descriptor, limits):
        if descriptor.availability is not Availability.READY:
            raise HistoricalError("MEDIA_NOT_READY")
        try:
            target = self._path(descriptor.relative_path)
            if not target.is_file():
                raise HistoricalError("MEDIA_UNAVAILABLE")
            data = self._read(target, limits.max_bytes)
            if len(data) != descriptor.size or hashlib.sha256(data).hexdigest() != descriptor.sha256:
                raise HistoricalError("MEDIA_CHANGED")
            return data
        except OSError as exc:
            raise HistoricalError("MEDIA_UNAVAILABLE") from exc
