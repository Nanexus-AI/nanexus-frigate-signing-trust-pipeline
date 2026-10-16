"""Durable single-worker jobs and atomically published portable evidence."""

from dataclasses import asdict
import io
import hashlib
import fcntl
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import uuid
import zipfile

from ..contracts import CameraId, EvidenceReference, Interval
from ..evidence import (RecognitionStatus, recognize_bundle, canonical_json,
                        MAX_TOTAL_BYTES, MAX_ARTIFACTS, MAX_MANIFEST_BYTES, MAX_ARTIFACT_BYTES)
from ..frigate.retained import private_directory


def opaque(value):
    if type(value) is not str or len(value) != 32 or any(c not in '0123456789abcdef' for c in value):
        raise ValueError('opaque identity required')
    return value


class JobStore:
    """One process owns this store; do not open it from multiple companions.

    All unfinished jobs are interrupted at open. Execution is explicit run_next()
    or a single optional worker; result reads never run verification.
    """

    def __init__(self, root, service_factory, *, max_jobs=100, max_queued=8):
        if not 1 <= max_queued <= max_jobs <= 1000:
            raise ValueError('bounded job store required')
        self.root = private_directory(root)
        self.evidence = private_directory(self.root / 'evidence')
        self.scratch = private_directory(self.root / 'scratch')
        database = self.root / 'jobs.sqlite'
        if database.is_symlink():
            raise ValueError('unsafe job database')
        owner = self.root / '.owner'
        self._owner = os.open(owner, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(self._owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(self._owner)
            raise ValueError('job store already owned') from None
        self.lock = threading.RLock()
        self.db = None
        try:
            self.db = sqlite3.connect(database, check_same_thread=False)
            os.chmod(database, 0o600)
            self.db.execute('PRAGMA synchronous=FULL')
            self.db.execute('CREATE TABLE IF NOT EXISTS jobs ('
                            'id TEXT PRIMARY KEY,camera TEXT,start TEXT,end TEXT,state TEXT,stage TEXT,'
                            'result TEXT,evidence TEXT,digest TEXT)')
            self.db.execute("UPDATE jobs SET state='interrupted',stage='restart' "
                            "WHERE state IN ('accepted','queued','running')")
            self.db.commit()
        except Exception:
            if self.db is not None:
                self.db.close()
            os.close(self._owner)
            raise
        self.factory = service_factory
        self.max_jobs, self.max_queued = max_jobs, max_queued
        self._busy = False
        self._closed = False
        self._stop = threading.Event()
        self._thread = None

    def submit(self, camera, start, end):
        CameraId(camera); Interval(start, end)
        with self.lock:
            if self._closed:
                raise ValueError('store closed')
            count, pending = self.db.execute("SELECT count(*),sum(state IN ('accepted','queued','running')) FROM jobs").fetchone()
            if count >= self.max_jobs or (pending or 0) >= self.max_queued:
                raise ValueError('job capacity reached')
            job = uuid.uuid4().hex
            # Acceptance is durable before identity can be returned; no implicit retry.
            with self.db:
                self.db.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?)',
                                (job, camera, start, end, 'accepted', 'accepted', None, None, None))
            with self.db:
                self.db.execute("UPDATE jobs SET state='queued',stage='queued' WHERE id=?", (job,))
            return job

    def get(self, job):
        opaque(job)
        with self.lock:
            row = self.db.execute('SELECT * FROM jobs WHERE id=?', (job,)).fetchone()
        if row is None:
            raise KeyError('job unavailable')
        return {'job_id': row[0], 'request': {'camera': row[1], 'start': row[2], 'end': row[3]},
                'state': row[4], 'stage': row[5], 'result': None if row[6] is None else json.loads(row[6]),
                'evidence_id': row[7]}

    def run_next(self):
        with self.lock:
            if self._busy or self._closed:
                return False
            row = self.db.execute("SELECT id,camera,start,end FROM jobs WHERE state='queued' ORDER BY rowid LIMIT 1").fetchone()
            if row is None:
                return False
            job, camera, start, end = row
            with self.db:
                self.db.execute("UPDATE jobs SET state='running',stage='verifying' WHERE id=?", (job,))
            self._busy = True
        try:
            # Factory is a context manager owning request-scoped metadata staging and
            # HistoricalService configured to write only inside this new temp root.
            with tempfile.TemporaryDirectory(prefix='job-', dir=self.scratch) as temp:
                with self.factory(camera, start, end, Path(temp)) as service:
                    operation = service.verify(camera, start, end)
                reference = operation.evidence_reference
                if reference is None:
                    raise OSError('evidence unavailable')
                bundle = Path(temp) / operation.result.request.request_id
                recognition = recognize_bundle(bundle, reference)
                if recognition.status is not RecognitionStatus.SUPPORTED or recognition.result != operation.result:
                    raise OSError('evidence validation failed')
                if (operation.result.request.camera.value != camera
                        or operation.result.request.interval != Interval(start, end)):
                    raise OSError('evidence request mismatch')
                evidence_id = uuid.uuid4().hex
                # Same persistent filesystem: receipt/artifacts become visible together.
                bundle.rename(self.evidence / evidence_id)
                document = json.dumps(asdict(operation.result), separators=(',', ':'), allow_nan=False)
                with self.lock, self.db:
                    self.db.execute("UPDATE jobs SET state='completed',stage='completed',result=?,evidence=?,digest=? WHERE id=?",
                                    (document, evidence_id, reference.manifest_sha256, job))
        except Exception:
            # No raw backend/OS diagnostics (which may contain paths/secrets) escape.
            # Published but unreferenced evidence is preserved; never falsely completed.
            with self.lock, self.db:
                self.db.execute("UPDATE jobs SET state='failed',stage='execution_or_persistence_failed',"
                                "result=NULL,evidence=NULL,digest=NULL WHERE id=?", (job,))
        finally:
            with self.lock:
                self._busy = False
        return True

    def _reference(self, evidence):
        opaque(evidence)
        with self.lock:
            rows = self.db.execute("SELECT digest FROM jobs WHERE state='completed' AND evidence=?", (evidence,)).fetchall()
        if len(rows) != 1:
            raise KeyError('evidence unavailable')
        path = self.evidence / evidence
        if path.is_symlink():
            raise ValueError('unsafe evidence')
        return path, EvidenceReference(rows[0][0])

    def evidence_summary(self, evidence):
        path, reference = self._reference(evidence)
        recognized = recognize_bundle(path, reference)
        return {'evidence_id': evidence, 'recognition': recognized.status.value,
                'reason': recognized.reason, 'manifest_sha256': reference.manifest_sha256,
                'result': None if recognized.result is None else asdict(recognized.result)}

    def download(self, evidence):
        path, reference = self._reference(evidence)
        if recognize_bundle(path, reference).status is not RecognitionStatus.SUPPORTED:
            raise ValueError('unavailable evidence')
        # A validated portable bundle only; no arbitrary filesystem lookup.
        with (path / 'receipt.json').open('rb') as stream:
            receipt_bytes = stream.read(MAX_MANIFEST_BYTES + 1)
        if len(receipt_bytes) > MAX_MANIFEST_BYTES:
            raise ValueError('evidence limit')
        receipt = json.loads(receipt_bytes)
        if hashlib.sha256(canonical_json(receipt['manifest'])).hexdigest() != reference.manifest_sha256:
            raise ValueError('evidence changed')
        records = {a['path']: a for a in receipt['manifest']['artifacts']}
        names = ['receipt.json', *records]
        if len(names) > MAX_ARTIFACTS + 1:
            raise ValueError('evidence limit')
        output = io.BytesIO()
        total = 0
        with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_STORED) as archive:
            for name in names:
                target = path / name
                if any(p.is_symlink() for p in (target, *target.parents)):
                    raise ValueError('unsafe evidence')
                if name == 'receipt.json':
                    data = receipt_bytes
                else:
                    with target.open('rb') as stream:
                        data = stream.read(MAX_ARTIFACT_BYTES + 1)
                    record = records[name]
                    if len(data) != record['size'] or hashlib.sha256(data).hexdigest() != record['sha256']:
                        raise ValueError('evidence changed')
                total += len(data)
                if total > MAX_TOTAL_BYTES + 1024 * 1024:
                    raise ValueError('evidence limit')
                archive.writestr(name, data)
        return output.getvalue()

    def start(self):
        with self.lock:
            if self._closed:
                raise ValueError('store closed')
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name='historical-worker', daemon=True)
                self._thread.start()

    def _run(self):
        while not self._stop.wait(0.1):
            self.run_next()

    def close(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            if self._thread.is_alive():
                raise RuntimeError('bounded executor did not stop')
        with self.lock:
            if self._busy:
                raise RuntimeError('worker still running')
            self._closed = True
            self.db.close()
            os.close(self._owner)
