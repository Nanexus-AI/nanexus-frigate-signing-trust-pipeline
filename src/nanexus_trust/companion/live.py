"""Allowlisted playback mapping and serialized bounded Live lifecycle ownership."""

from dataclasses import dataclass
import hashlib
import os
import select
import threading
import uuid

from ..contracts import ObservationTime, identifier
from ..live.service import Binding, LiveService, ObservationEvent, system_clock


@dataclass(frozen=True)
class PlaybackMapping:
    frigate_camera: str
    stream: str
    context: str
    binding: Binding

    def __post_init__(self):
        for value in (self.frigate_camera, self.stream, self.context):
            identifier(value)
        if not isinstance(self.binding, Binding):
            raise ValueError('explicit logical binding required')

    @property
    def key(self):
        return self.frigate_camera, self.stream, self.context


class FDObservationSource:
    """Takes ownership of a compressed Annex-B FD, not a URL or subprocess.

    Deployment factory owns connection setup. This adapter owns nonblocking reads,
    generation stamping and close. No reader threads, stderr pipes or input queue.
    Caller must not retain/use the transferred FD. Reconnect creates a new object.
    """

    def __init__(self, fd, *, clock=system_clock, chunk_bytes=65536):
        if type(chunk_bytes) is not int or not 1 <= chunk_bytes <= 65536:
            raise ValueError('bounded read required')
        self.fd = fd
        self.clock = clock
        self.chunk_bytes = chunk_bytes
        self.session = uuid.uuid4().hex
        self.begun = False
        self.closed = False
        os.set_blocking(fd, False)

    def next_event(self, now):
        if self.closed:
            return None
        _, wall = self.clock()
        # Stamp actual receipt using the scheduler query's monotonic boundary.
        at = ObservationTime(self.session, now, wall)
        if not self.begun:
            self.begun = True
            return ObservationEvent('begin', at)
        try:
            if not select.select([self.fd], [], [], 0)[0]:
                return None
            data = os.read(self.fd, self.chunk_bytes)
        except BlockingIOError:
            return None
        except OSError:
            self.close()
            return ObservationEvent('disconnect', at)
        if not data:
            self.close()
            return ObservationEvent('eof', at)
        return ObservationEvent('bytes', at, data)

    def close(self):
        if not self.closed:
            self.closed = True
            os.close(self.fd)


class LiveScheduler:
    """Single serialized owner; bounded polling, explicit restarts, optional timer.

    Factories are configuration-owned and must return immediately with an owned
    nonblocking source or raise OSError. Network connection setup is outside API
    queries. No arbitrary endpoint or credential enters this interface.
    """

    def __init__(self, mappings, source_factory, *, clock=system_clock, interval=0.1):
        if not 0.01 <= interval <= 10:
            raise ValueError('bounded evaluation interval required')
        self.lock = threading.RLock()
        self.clock = clock
        self.interval = interval
        self.service = LiveService((), source_factory, clock=clock)
        self._stop = threading.Event()
        self._thread = None
        self._closed = False
        self._selected = {}
        self._epochs = {}
        self.mappings = ()
        self.configure(mappings)

    def configure(self, mappings):
        with self.lock:
            if self._closed or type(mappings) is not tuple or len(mappings) > 16:
                raise ValueError('at most sixteen configured mappings required')
            if not all(isinstance(m, PlaybackMapping) for m in mappings):
                raise ValueError('explicit playback mappings required')
            if self.mappings != mappings:
                for m in (*self.mappings, *mappings):
                    self._epochs[(m.binding.camera.value, m.binding.rendition.value)] = uuid.uuid4().hex
            self.mappings = mappings
            self.service.configure(self._bindings())
            self._selected.clear()

    def _bindings(self):
        # Multiple explicit playback contexts may share one identical binding.
        # Conflicting bindings for a logical identity fail closed.
        result, seen = [], set()
        for m in self.mappings:
            key = m.binding.camera.value, m.binding.rendition.value
            peers = [x for x in self.mappings if (x.binding.camera.value, x.binding.rendition.value) == key]
            if (sum(x.key == m.key for x in self.mappings) != 1
                    or any(x.binding != m.binding for x in peers) or key in seen):
                continue
            b = m.binding
            epoch = self._epochs.get(key, '')
            revision = hashlib.sha256((b.revision + epoch).encode()).hexdigest()
            result.append(Binding(b.camera, b.rendition, b.source_id, revision, b.policy))
            seen.add(key)
        return tuple(result)

    def _reset(self, key):
        self._epochs[key] = uuid.uuid4().hex
        self.service.configure(self._bindings())

    def query(self, camera, stream, context):
        for value in (camera, stream, context):
            identifier(value)
        with self.lock:
            matches = [m for m in self.mappings if m.key == (camera, stream, context)]
            if len(matches) != 1:
                return self.service._unknown(None, None, self.clock()[1], 'mapping_unavailable').to_dict()
            m = matches[0]
            key = m.binding.camera.value, m.binding.rendition.value
            selection = stream, context
            if camera in self._selected and self._selected[camera] != selection:
                old = self._selected[camera]
                for previous in self.mappings:
                    if previous.key == (camera, *old):
                        self._reset((previous.binding.camera.value, previous.binding.rendition.value))
                self._reset(key)
            self._selected[camera] = selection
            return self.service.get_status(*key).to_dict()

    def reconnect(self, camera, rendition):
        with self.lock:
            key = camera, rendition
            if key not in [(b.camera.value, b.rendition.value) for b in self._bindings()]:
                raise ValueError('unknown logical binding')
            self._reset(key)

    def tick(self):
        with self.lock:
            if self._closed:
                return
            for b in self._bindings():
                result = self.service.get_status(b.camera.value, b.rendition.value)
                # One restart per tick; new source begins on the next tick/query.
                if result.reason in ('observer_unavailable', 'observation_stopped', 'observation_discontinuity'):
                    self._reset((b.camera.value, b.rendition.value))

    def start(self):
        with self.lock:
            if self._closed:
                raise ValueError('scheduler closed')
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name='live-observer', daemon=True)
                self._thread.start()

    def _run(self):
        while not self._stop.wait(self.interval):
            self.tick()

    def close(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            if self._thread.is_alive():
                raise RuntimeError('nonblocking source contract violated')
        with self.lock:
            self._closed = True
            self.service.close()
