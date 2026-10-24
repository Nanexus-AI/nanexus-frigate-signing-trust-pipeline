"""Bounded H.264 avc1 MP4 reader for one stable admitted file.

Packet ticks are internal file-local facts, never Historical coordinates. No
avcC injection, decoding, repair, cross-file scheduling or extractor reset.
"""
from dataclasses import dataclass
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
import selectors
import resource
import signal
import math
import subprocess
import tempfile
import time


class MediaFailure(RuntimeError):
    pass


@dataclass(frozen=True)
class ReaderLimits:
    max_bytes: int = 16 * 1024 * 1024
    max_packets: int = 4096
    max_nals: int = 8192
    max_nal_bytes: int = 1024 * 1024
    max_probe_bytes: int = 1024 * 1024
    timeout_seconds: float = 5

    def __post_init__(self):
        for name, ceiling in (('max_bytes', 32 * 1024 * 1024), ('max_packets', 8192),
                              ('max_nals', 16384), ('max_nal_bytes', 1024 * 1024),
                              ('max_probe_bytes', 1024 * 1024)):
            value = getattr(self, name)
            if type(value) is not int or not 0 < value <= ceiling:
                raise ValueError('reader admission bound')
        if not 0 < self.timeout_seconds <= 30:
            raise ValueError('probe deadline bound')


@dataclass(frozen=True)
class Nal:
    ordinal: int
    token: str
    packet: int
    nal_in_packet: int
    length_offset: int
    payload_offset: int
    payload_bytes: int
    payload_sha256: str
    nal_type: int
    annex_b: bytes
    pts: int | None
    dts: int | None
    duration: int | None
    time_base: Fraction

    @property
    def local_pts(self):
        return None if self.pts is None else self.pts * self.time_base


class SourceLedger:
    """Bounded active mappings. B retirement plus dependencies permit release.

    Caller supplies still-needed source tokens explicitly. Never interpret
    retirement as a historical-time frontier or as positive signing evidence.
    """
    def __init__(self, max_active=1024):
        if type(max_active) is not int or not 0 < max_active <= 8192:
            raise ValueError('ledger bound')
        self.max_active, self.records = max_active, {}
        self._next, self._retired = 0, -1

    def add(self, nal):
        if nal.ordinal != self._next or len(self.records) >= self.max_active:
            raise MediaFailure('ledger-order-or-cap')
        # Compressed payload is owned by Feed's caller, not retained in ledger.
        self.records[nal.ordinal] = (nal.token, nal.packet, nal.payload_offset,
                                     nal.payload_bytes, nal.payload_sha256,
                                     nal.pts, nal.dts, nal.duration, nal.time_base)
        self._next += 1

    def retire(self, prefix, needed_tokens=()):
        if (type(prefix) is not int or prefix < self._retired or prefix >= self._next
                or len(needed_tokens) > self.max_active):
            raise MediaFailure('invalid-retirement')
        needed = set(needed_tokens)
        if not needed <= {r[0] for r in self.records.values()}:
            raise MediaFailure('unknown-dependency')
        self._retired = prefix
        for ordinal in list(self.records):
            if ordinal <= prefix and self.records[ordinal][0] not in needed:
                del self.records[ordinal]


def _check(check, deadline):
    check()  # Trusted caller rechecks cancellation/authority, raises on failure.
    if time.monotonic() >= deadline:
        raise MediaFailure('reader-deadline')


def _probe(executable, path, limits, check, deadline, *, pass_fds=()):
    command = [str(executable), '-v', 'error', '-show_streams', '-show_packets',
               '-show_entries',
               'stream=index,codec_name,codec_type,codec_tag_string,nal_length_size,time_base:'
               'packet=stream_index,pos,size,pts,dts,duration,flags', '-of', 'json', str(path)]
    data = [bytearray(), bytearray()]
    # prlimit execs the probe in the owned session: limits apply before probe
    # startup, with no Python preexec_fn in a threaded Companion.
    command = ['/usr/bin/prlimit', '--as=536870912',
               '--cpu='+str(max(1, math.ceil(limits.timeout_seconds))), '--'] + command
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               stdin=subprocess.DEVNULL, close_fds=True, pass_fds=pass_fds,
                               start_new_session=True)
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ, 0)
            selector.register(process.stderr, selectors.EVENT_READ, 1)
            while selector.get_map():
                _check(check, deadline)
                for key, _ in selector.select(.02):
                    chunk = os.read(key.fileobj.fileno(), 16384)
                    if not chunk:
                        selector.unregister(key.fileobj)
                    else:
                        data[key.data].extend(chunk)
                        if sum(map(len, data)) > limits.max_probe_bytes:
                            raise MediaFailure('probe-output-cap')
            _check(check, deadline)
            if process.wait(timeout=max(.001, deadline - time.monotonic())):
                raise MediaFailure('probe-failed')
        try:
            return json.loads(data[0])
        except ValueError as exc:
            raise MediaFailure('probe-operational') from exc
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MediaFailure('probe-operational') from exc
    finally:
        try:
            if process.poll() is None:
                try: os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError: pass
            try: process.wait(timeout=1)
            except subprocess.TimeoutExpired as exc: raise MediaFailure('probe-reap-failure') from exc
        finally:
            process.stdout.close()
            process.stderr.close()


def _integer(value, *, optional=False):
    if value is None and optional:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise MediaFailure('invalid-packet-integer')
    try:
        n = int(value)
    except ValueError as exc:
        raise MediaFailure('invalid-packet-integer') from exc
    if str(n) != str(value) or not -(2**63 - 1) <= n <= 2**63 - 1:
        raise MediaFailure('invalid-packet-integer')
    return n


def _nals(file, probe, identity, limits, check, deadline):
    streams, packets = probe.get('streams'), probe.get('packets')
    if not isinstance(streams, list) or len(streams) != 1:
        raise MediaFailure('single-video-stream-required')
    stream = streams[0]
    if (stream.get('codec_name'), stream.get('codec_type'), stream.get('codec_tag_string'),
            stream.get('nal_length_size')) != ('h264', 'video', 'avc1', '4'):
        raise MediaFailure('unsupported-media-profile')
    try:
        tb = Fraction(stream['time_base'])
    except (KeyError, ValueError, ZeroDivisionError, TypeError) as exc:
        raise MediaFailure('invalid-time-base') from exc
    if tb <= 0 or max(abs(tb.numerator), tb.denominator) > 2**63 - 1:
        raise MediaFailure('invalid-time-base')
    if not isinstance(packets, list) or not 0 < len(packets) <= limits.max_packets:
        raise MediaFailure('packet-cap-or-empty')
    index = _integer(stream.get('index'))
    size = os.fstat(file.fileno()).st_size
    ordinal, previous_end, previous_dts = 0, 0, None
    configured = set()
    for pi, packet in enumerate(packets):
        _check(check, deadline)
        pos, length = _integer(packet.get('pos')), _integer(packet.get('size'))
        if (_integer(packet.get('stream_index')) != index or pos < previous_end
                or length <= 0 or length > limits.max_bytes or pos + length > size):
            raise MediaFailure('packet-extent-or-order')
        previous_end = pos + length
        pts, dts, duration = (_integer(packet.get(k), optional=True) for k in ('pts', 'dts', 'duration'))
        if duration is not None and duration < 0:
            raise MediaFailure('invalid-duration')
        if (pts is not None and dts is not None and pts != dts
                or dts is not None and previous_dts is not None and dts < previous_dts):
            raise MediaFailure('unsupported-reorder')
        if dts is not None: previous_dts = dts
        file.seek(pos)
        at, ni = pos, 0
        while at < pos + length:
            _check(check, deadline)
            if ordinal >= limits.max_nals or at + 4 > pos + length:
                raise MediaFailure('nal-cap-or-truncated-length')
            raw = file.read(4)
            if len(raw) != 4: raise MediaFailure('truncated-length')
            count = int.from_bytes(raw, 'big')
            if not 0 < count <= limits.max_nal_bytes or at + 4 + count > pos + length:
                raise MediaFailure('nal-extent')
            payload = file.read(count)
            if len(payload) != count or payload[0] & 0x80:
                raise MediaFailure('invalid-nal')
            kind = payload[0] & 31
            if kind not in (1, 5, 6, 7, 8, 9):
                raise MediaFailure('unsupported-nal-type')
            if kind in (7, 8): configured.add(kind)
            if kind in (1, 5) and configured != {7, 8}:
                raise MediaFailure('inband-configuration-required')
            token = f'{identity}:p{pi}:n{ni}:o{at + 4}:l{count}'
            if len(token.encode()) > 256: raise MediaFailure('token-cap')
            yield Nal(ordinal, token, pi, ni, at, at + 4, count,
                      hashlib.sha256(payload).hexdigest(), kind, b'\0\0\0\1' + payload,
                      pts, dts, duration, tb)
            at += 4 + count
            ordinal, ni = ordinal + 1, ni + 1
    if not configured == {7, 8}:
        raise MediaFailure('inband-configuration-required')


def read_nals(handle, read, check, *, ffprobe, scratch_root, limits=ReaderLimits()):
    """Yield one file's NALs. `read(offset,count)` must enforce A authority.

    `check()` must raise on revocation/cancellation. The consumer must close this
    generator on early exit (e.g. contextlib.closing), allowing owned cleanup.
    No trust configuration, validator lifecycle or Historical placement occurs.
    """
    deadline = time.monotonic() + limits.timeout_seconds
    _check(check, deadline)
    if not 0 < handle.actual_bytes <= limits.max_bytes:
        raise MediaFailure('media-byte-cap')
    if len(handle.sha256) != 64 or any(c not in '0123456789abcdef' for c in handle.sha256):
        raise MediaFailure('invalid-stable-digest')
    identity = hashlib.sha256((handle.item_id + ':' + handle.generation + ':' + handle.sha256).encode()).hexdigest()
    with tempfile.TemporaryDirectory(prefix='hv2-reader-', dir=scratch_root) as temp:
        path = Path(temp) / 'input.mp4'
        digest, offset = hashlib.sha256(), 0
        with path.open('xb') as out:
            while offset < handle.actual_bytes:
                _check(check, deadline)
                amount = min(65536, handle.actual_bytes - offset)
                chunk = read(offset, amount)
                if not isinstance(chunk, bytes) or not 0 < len(chunk) <= amount:
                    raise MediaFailure('short-or-oversized-read')
                out.write(chunk)
                digest.update(chunk)
                offset += len(chunk)
        if digest.hexdigest() != handle.sha256:
            raise MediaFailure('stable-digest-mismatch')
        try:
            probe = _probe(ffprobe, path, limits, check, deadline)
        except OSError as exc:
            raise MediaFailure('probe-unavailable') from exc
        with path.open('rb') as file:
            yield from _nals(file, probe, identity, limits, check, deadline)
