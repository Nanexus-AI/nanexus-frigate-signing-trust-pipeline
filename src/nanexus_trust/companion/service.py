"""Transport-independent local boundary for later Frigate-native UI adapters."""

from contextlib import contextmanager
from dataclasses import dataclass

from ..contracts import CameraId, Interval, identifier
from ..frigate.retained import FrigateRetainedSource
from ..historical.models import Limits
from ..historical.service import HistoricalService


@dataclass(frozen=True)
class HistoricalProfile:
    database: object
    media_root: object
    scratch_root: object
    executor: object
    backend_component: object
    trust_config_id: str
    version: str = '0.18.0'
    recording_mount: str = '/media/frigate/recordings'
    limits: Limits = Limits()

    def __post_init__(self):
        if self.version != '0.18.0':
            raise ValueError('unsupported Frigate version')
        identifier(self.trust_config_id)

    @contextmanager
    def service(self, camera, start, end, evidence_root):
        with FrigateRetainedSource(self.database, self.media_root, self.scratch_root, Interval(start, end),
                                   version=self.version, recording_mount=self.recording_mount) as source:
            yield HistoricalService(source, self.executor, self.backend_component, evidence_root,
                                    self.trust_config_id, limits=self.limits)


class CompanionService:
    """One trusted local deployment principal with an explicit camera allowlist.

    This is not a network listener. A future HTTP adapter must authenticate the
    browser/proxy, enforce the principal's allowlist and bound body sizes before
    calling these methods. Opaque IDs are references, not bearer authorization.
    """

    def __init__(self, live, jobs, cameras, *, canonical_selectors=(), historical_v2=None):
        if type(cameras) is not tuple or not 1 <= len(cameras) <= 16:
            raise ValueError('bounded camera allowlist required')
        from .cameras import CanonicalSelector
        if (type(canonical_selectors) is not tuple or len(canonical_selectors) > 16
                or not all(isinstance(s, CanonicalSelector) for s in canonical_selectors)):
            raise ValueError('bounded canonical selectors required')
        self.canonical_selectors = canonical_selectors
        self.cameras = frozenset(CameraId(c).value for c in cameras)
        self.live, self.jobs = live, jobs
        self.historical_v2 = historical_v2
        self.closed = False

    def _camera(self, camera):
        if self.closed or CameraId(camera).value not in self.cameras:
            raise ValueError('camera unavailable')

    def list_mappings(self):
        with self.live.lock:
            return [{'frigate_camera': m.frigate_camera, 'stream': m.stream, 'context': m.context,
                     'camera': m.binding.camera.value, 'rendition': m.binding.rendition.value,
                     'revision': m.binding.revision}
                    for m in self.live.mappings if m.binding.camera.value in self.cameras]

    def camera_bindings(self):
        """Bounded configuration metadata only; no status queries or source creation."""
        from .cameras import resolve_camera
        with self.live.lock:
            if self.closed:
                raise ValueError('service closed')
            names = sorted({m.frigate_camera for m in self.live.mappings})
            return {'cameras': [resolve_camera(c, self.live.mappings, self.canonical_selectors)[0]
                                for c in names]}

    def camera_status(self, camera, guard):
        from .cameras import resolve_camera
        from .transport import live_document, unknown
        identifier(camera); identifier(guard)
        with self.live.lock:
            meta, mapping = resolve_camera(camera, self.live.mappings, self.canonical_selectors)
            if self.closed or mapping is None or meta['binding_guard'] != guard:
                return {'camera_id': camera, 'binding_guard': guard, 'result': unknown()}
            self._camera(mapping.binding.camera.value)
            result = live_document(self.live.query(camera, mapping.stream, mapping.context))
            # Runtime epochs are intentionally separate from the stable config guard.
            expected = [b for b in self.live._bindings() if
                        (b.camera, b.rendition) == (mapping.binding.camera, mapping.binding.rendition)]
            if (len(expected) != 1 or result['logical_camera'] != mapping.binding.camera.value
                    or result['rendition'] != mapping.binding.rendition.value
                    or (result['status'] != 'UNKNOWN' and result['mapping_revision'] != expected[0].identity)):
                result = unknown()
            return {'camera_id': camera, 'binding_guard': guard, 'result': result}

    def live_status(self, camera, stream, context):
        with self.live.lock:
            matches = [m for m in self.live.mappings if m.key == (camera, stream, context)]
            for m in matches:
                self._camera(m.binding.camera.value)
            return self.live.query(camera, stream, context)

    def submit(self, camera, start, end):
        self._camera(camera)
        return {'job_id': self.jobs.submit(camera, start, end)}

    def job(self, job_id):
        job = self.jobs.get(job_id)
        self._camera(job['request']['camera'])
        return job

    def _evidence(self, evidence_id):
        summary = self.jobs.evidence_summary(evidence_id)
        # Authorize via durable job association even if bundle becomes unreadable.
        with self.jobs.lock:
            row = self.jobs.db.execute("SELECT camera FROM jobs WHERE evidence=? AND state='completed'", (evidence_id,)).fetchone()
        if row is None:
            raise KeyError('evidence unavailable')
        self._camera(row[0])
        return summary

    def evidence_summary(self, evidence_id):
        return self._evidence(evidence_id)

    def download(self, evidence_id):
        self._evidence(evidence_id)
        return self.jobs.download(evidence_id)

    def _hv2_camera(self, camera, guard):
        from .cameras import resolve_camera
        from ..historical_v2.contracts import Camera
        from ..historical_v2.prototype import PrototypeFailure
        with self.live.lock:
            meta, mapping = resolve_camera(camera, self.live.mappings, self.canonical_selectors)
            if self.closed or mapping is None or meta.get('binding_guard') != guard:
                raise PrototypeFailure('UNAUTHORIZED')
            self._camera(mapping.binding.camera.value)
            return Camera('frigate-captured', camera, guard, camera)

    def hv2_verify(self, camera, guard, start, end, request_id):
        from ..historical_v2.prototype import PrototypeFailure
        if self.historical_v2 is None: raise PrototypeFailure('DISABLED')
        admitted = self._hv2_camera(camera, guard)
        def permission():
            if self._hv2_camera(camera, guard) != admitted: raise PrototypeFailure('UNAUTHORIZED')
        return self.historical_v2.verify(admitted, start, end, request_id, permission)

    def hv2_cancel(self, camera, guard, request_id):
        from ..historical_v2.prototype import PrototypeFailure
        if self.historical_v2 is None: raise PrototypeFailure('DISABLED')
        self._hv2_camera(camera, guard)
        return self.historical_v2.cancel(camera, request_id)

    def health(self):
        return {'healthy': not self.closed}

    def readiness(self):
        # Configuration/storage/worker lifecycle only; never a signing verdict.
        with self.jobs.lock, self.live.lock:
            return {'ready': not self.closed and not self.jobs._closed and not self.live._closed,
                    'boundary': 'local_programmatic', 'frigate_version': '0.18.0'}

    def close(self):
        if self.historical_v2 is not None: self.historical_v2.close()
        self.live.close()
        self.jobs.close()
        self.closed = True
