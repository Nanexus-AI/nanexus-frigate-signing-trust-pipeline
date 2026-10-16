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

    def __init__(self, live, jobs, cameras):
        if type(cameras) is not tuple or not 1 <= len(cameras) <= 16:
            raise ValueError('bounded camera allowlist required')
        self.cameras = frozenset(CameraId(c).value for c in cameras)
        self.live, self.jobs = live, jobs
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

    def health(self):
        return {'healthy': not self.closed}

    def readiness(self):
        # Configuration/storage/worker lifecycle only; never a signing verdict.
        with self.jobs.lock, self.live.lock:
            return {'ready': not self.closed and not self.jobs._closed and not self.live._closed,
                    'boundary': 'local_programmatic', 'frigate_version': '0.18.0'}

    def close(self):
        self.live.close()
        self.jobs.close()
        self.closed = True
