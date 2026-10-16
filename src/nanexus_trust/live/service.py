"""Logical mapping and bounded request-driven lifecycle; no concrete transport."""

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import time
from typing import Protocol

from ..contracts import CameraId, ObservationTime, RenditionId, SigningStatus, decimal_seconds, identifier
from .core import LiveObserver, LiveResult
from .policy import LiveSigningPolicy


@dataclass(frozen=True)
class Binding:
    camera: CameraId
    rendition: RenditionId
    source_id: str
    revision: str
    policy: LiveSigningPolicy | None

    def __post_init__(self):
        if not isinstance(self.camera, CameraId) or not isinstance(self.rendition, RenditionId):
            raise ValueError("logical camera/rendition required")
        identifier(self.source_id)
        identifier(self.revision)
        if self.policy is not None and not isinstance(self.policy, LiveSigningPolicy):
            raise ValueError("configured Live policy required")

    @property
    def identity(self):
        data = {"camera": self.camera.value, "rendition": self.rendition.value,
                "source": self.source_id, "revision": self.revision}
        return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class ObservationEvent:
    kind: str  # begin, bytes, disconnect, eof
    at: ObservationTime
    data: bytes = b""

    def __post_init__(self):
        if self.kind not in ("begin", "bytes", "disconnect", "eof"):
            raise ValueError("unsupported lifecycle event")
        if not isinstance(self.at, ObservationTime) or type(self.data) is not bytes:
            raise ValueError("explicit receipt time and immutable byte input required")


class ObservationSource(Protocol):
    """Deployment owns nonblocking, bounded buffering and actual receipt timestamps."""

    def next_event(self, now: str) -> ObservationEvent | None: ...

    def close(self) -> None: ...


def system_clock():
    ns = time.monotonic_ns()
    return f"{ns // 1000000000}.{ns % 1000000000:09d}", datetime.now(timezone.utc).isoformat()


@dataclass
class _Entry:
    binding: Binding
    source: ObservationSource | None
    observer: LiveObserver
    stopped: bool = False


class LiveService:
    MAX_BINDINGS = 16
    MAX_EVENTS_PER_QUERY = 256

    def __init__(self, bindings: tuple[Binding, ...], source_factory, *, clock=system_clock):
        self._entries = {}
        self._factory = source_factory
        self._clock = clock
        self._closed = False
        self._bindings = ()
        self.configure(bindings)

    def configure(self, bindings: tuple[Binding, ...]):
        if type(bindings) is not tuple or len(bindings) > self.MAX_BINDINGS or not all(isinstance(b, Binding) for b in bindings):
            raise ValueError("at most sixteen explicit mappings required")
        for key, entry in list(self._entries.items()):
            matches = [b for b in bindings if (b.camera.value, b.rendition.value) == key]
            if len(matches) != 1 or matches[0] != entry.binding:
                self._release(entry)
                del self._entries[key]
        self._bindings = bindings

    @staticmethod
    def _release(entry):
        entry.stopped = True
        source, entry.source = entry.source, None
        if source is not None:
            source.close()

    @staticmethod
    def _unknown(camera, rendition, stamp, reason):
        return LiveResult(camera, rendition, SigningStatus.UNKNOWN, reason, stamp,
                          None, None, None, None, None, False, False)

    def get_status(self, camera: str, rendition: str) -> LiveResult:
        now, wall = self._clock()
        # Clock validation also applies to requests without a mapping/session.
        at = ObservationTime("unestablished", now, wall)
        try:
            key = CameraId(camera).value, RenditionId(rendition).value
        except ValueError:
            return self._unknown(None, None, wall, "invalid_request")
        if self._closed:
            return self._unknown(*key, wall, "service_closed")
        matches = [b for b in self._bindings if (b.camera.value, b.rendition.value) == key]
        if len(matches) != 1:
            return self._unknown(*key, wall, "mapping_unavailable")
        binding = matches[0]
        if key not in self._entries:
            self._entries[key] = _Entry(binding, None, LiveObserver(binding.policy))
            try:
                self._entries[key].source = self._factory(binding)
            except OSError:
                self._entries[key].stopped = True
                return self._unknown(*key, wall, "observer_unavailable")
        entry = self._entries[key]
        if entry.stopped:
            return self._unknown(*key, wall, "observation_stopped")
        try:
            for _ in range(self.MAX_EVENTS_PER_QUERY):
                event = entry.source.next_event(now)
                if event is None:
                    break
                if not isinstance(event, ObservationEvent) or decimal_seconds(event.at.monotonic_seconds) > decimal_seconds(now):
                    raise ValueError("invalid observation event time")
                if event.kind == "begin":
                    entry.observer.begin(event.at)
                elif event.kind == "bytes":
                    entry.observer.feed(event.data, event.at)
                elif event.kind in ("disconnect", "eof"):
                    entry.observer.end(event.at, "observation_discontinuity" if event.kind == "disconnect" else "observer_unavailable")
                else:
                    raise ValueError("unsupported lifecycle event")
            else:
                self._release(entry)
                entry.stopped = True
                return self._unknown(*key, wall, "adapter_backlog")
            session = entry.observer.session_id or "unestablished"
            return entry.observer.snapshot(ObservationTime(session, now, wall), camera=camera,
                                           rendition=rendition, mapping_id=binding.identity)
        except (OSError, ValueError):
            self._release(entry)
            entry.stopped = True
            return self._unknown(*key, wall, "observer_unavailable")

    def stop(self, camera: str, rendition: str):
        key = CameraId(camera).value, RenditionId(rendition).value
        if key in self._entries:
            self._release(self._entries[key])
            self._entries[key].stopped = True

    def close(self):
        self._closed = True
        for entry in self._entries.values():
            self._release(entry)
        self._entries.clear()
