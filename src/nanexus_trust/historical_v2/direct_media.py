"""Experimental Linux direct-original input. No snapshots, retries or media writes.

Private adapter over the pinned Provider's registered paths and authority. It
never issues an A StableHandle or claims the staged profile's immutability.
"""
from contextlib import contextmanager, closing
from dataclasses import dataclass
import hashlib
import os
import stat
import time

from ..frigate.historical_v2 import _open, _identity, ProviderFailure
from .media import ReaderLimits, MediaFailure, _check, _probe, _nals


@dataclass(frozen=True)
class DirectInput:
    item_id: str
    generation: str
    actual_bytes: int
    sha256: str


@contextmanager
def direct_input(provider, item, bounds, cancel, check, *, ffprobe,
                 limits=ReaderLimits()):
    """Own one O_RDONLY descriptor until parsing and post-read checks complete.

    No original cleanup/locking or stability recovery. A successful return means
    checks detected no change, not that the source was an atomic snapshot.
    Caller must finish this context before publishing/feeding verification.
    """
    deadline = time.monotonic() + limits.timeout_seconds
    fd = -1
    reader = None
    try:
        provider._guard(item.camera, bounds, cancel)
        known = provider._items.get(item.item_id)
        if known is None or known[0] != item or provider._handles:
            raise ProviderFailure('unknown-item-or-generation')
        path = provider.media_root / known[1]
        fd = _open(path)
        before = os.fstat(fd)
        limit = min(bounds.max_item_bytes, limits.max_bytes)
        if known[2] is not None:
            limit = min(limit, bounds.max_context_bytes)
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= limit:
            raise ProviderFailure('media-size-or-type')
        identity = _identity(before)

        def guard():
            _check(check, deadline)
            provider._guard(item.camera, bounds, cancel)
            if _identity(os.fstat(fd)) != identity:
                raise ProviderFailure('source-changed')

        def path_check():
            guard()
            current = _open(path)
            try:
                if _identity(os.fstat(current)) != identity:
                    raise ProviderFailure('source-changed')
            finally:
                os.close(current)

        def digest():
            value = hashlib.sha256()
            offset = work = 0
            while offset < before.st_size:
                guard()
                work += 1
                if work > bounds.max_work_units:
                    raise ProviderFailure('read-work-cap')
                chunk = os.pread(fd, min(65536, before.st_size - offset), offset)
                if not chunk:
                    raise ProviderFailure('source-changed')
                offset += len(chunk)
                value.update(chunk)
            guard()
            return value.hexdigest()

        initial = digest()
        path_check()
        handle = DirectInput(item.item_id, item.generation, before.st_size, initial)
        token_identity = hashlib.sha256((item.item_id + ':' + item.generation + ':' + initial).encode()).hexdigest()
        # /proc/self/fd reopens the admitted inode in the child. Only this FD is
        # inherited through prlimit; the child never receives an arbitrary path.
        probe = _probe(ffprobe, f'/proc/self/fd/{fd}', limits, guard, deadline,
                       pass_fds=(fd,))
        path_check()
        # Duplicate lifetime belongs to the parser; original stays owned here.
        with os.fdopen(os.dup(fd), 'rb') as file:
            reader = _nals(file, probe, token_identity, limits, guard, deadline)
            with closing(reader):
                yield handle, reader
        path_check()
        if digest() != initial:
            raise ProviderFailure('source-changed')
        path_check()
    except OSError as exc:
        raise ProviderFailure('source-unavailable-or-unsafe') from exc
    finally:
        if fd >= 0:
            os.close(fd)
