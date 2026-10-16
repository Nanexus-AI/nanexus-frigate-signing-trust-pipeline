"""Deterministic integration foundation fixtures; no Frigate/runtime/network."""

from contextlib import contextmanager
from dataclasses import replace
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile

from nanexus_trust.companion import (CompanionService, FDObservationSource, HistoricalProfile,
                                     JobStore, LiveScheduler, PlaybackMapping)
from nanexus_trust.contracts import CameraId, Interval, RenditionId
from nanexus_trust.evidence import Component, recognize_bundle
from nanexus_trust.frigate.retained import FrigateRetainedSource
from nanexus_trust.historical.models import Availability, HistoricalError, Limits
from nanexus_trust.historical.service import HistoricalService
from nanexus_trust.live import Binding
from test_historical import observation, SequenceExecutor
from test_live import MEDIA, SIGNING, PREFIX, POLICY

# Pinned Recordings DDL assembled from v0.18.0 migrations, no Peewee dependency.
DDL = '''CREATE TABLE recordings (
 id VARCHAR(30) NOT NULL PRIMARY KEY, camera VARCHAR(20) NOT NULL,
 path VARCHAR(255) NOT NULL, start_time DATETIME NOT NULL,
 end_time DATETIME NOT NULL, duration REAL NOT NULL,
 motion INTEGER, objects INTEGER, dBFS INTEGER, segment_size REAL,
 regions INTEGER, motion_heatmap TEXT)'''


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.media = self.root / 'media'; self.media.mkdir()
        self.dbpath = self.root / 'frigate.db'
        self.db = sqlite3.connect(self.dbpath)
        self.db.execute(DDL); self.db.commit()
        self.addCleanup(self.db.close)

    def row(self, rid='r1', start=10, end=20, path=None, data=b'whole-signed-media'):
        path = path or '/media/frigate/recordings/' + rid + '.mp4'
        if data is not None:
            (self.media / (rid + '.mp4')).write_bytes(data)
        self.db.execute('INSERT INTO recordings (id,camera,path,start_time,end_time,duration) VALUES (?,?,?,?,?,?)',
                        (rid, 'front', path, start, end, end-start))
        self.db.commit()

    def source(self, **kwargs):
        source = FrigateRetainedSource(self.dbpath, self.media, self.root / 'scratch', Interval('11','19'),
                                       version='0.18.0', settle_seconds=0, **kwargs)
        self.addCleanup(source.close)
        return source

    def load(self, source=None, **kwargs):
        return (source or self.source()).load(CameraId('front'), Limits(**kwargs))


class MetadataTests(Fixture):
    def test_normal_coherent_ordered_and_camera_filtered(self):
        self.row('r2',20,30); self.row('r1')
        self.db.execute("INSERT INTO recordings VALUES ('other','back','/other',10,20,10,NULL,NULL,NULL,NULL,NULL,NULL)")
        self.db.commit()
        source = self.source(); inventory = self.load(source)
        self.assertTrue(inventory.metadata_complete)
        self.assertEqual([r.recording_id for r in inventory.descriptors], ['r1','r2'])
        self.assertEqual(inventory.descriptors[0].interval, Interval('10','20'))
        self.assertTrue(all(r.availability is Availability.READY for r in inventory.descriptors))
        self.assertEqual(source.read(inventory.descriptors[0], Limits()), b'whole-signed-media')

    def test_wal_committed_rows_and_no_checkpoint(self):
        self.db.execute('PRAGMA journal_mode=WAL'); self.db.execute('PRAGMA wal_autocheckpoint=0')
        self.db.execute('PRAGMA wal_checkpoint(TRUNCATE)')  # Fixture writer only.
        self.row()
        before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in (self.dbpath, Path(str(self.dbpath)+'-wal'))}
        self.assertEqual(len(self.load().descriptors),1)
        after = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in (self.dbpath, Path(str(self.dbpath)+'-wal'))}
        self.assertEqual(before, after)

    def test_main_only_wal_snapshot_is_rejected(self):
        self.db.execute('PRAGMA journal_mode=WAL'); self.db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        self.row()
        snapshot = self.root/'incomplete.db'; shutil.copyfile(self.dbpath,snapshot)
        source = self.source(); source.database=snapshot
        with self.assertRaises(HistoricalError) as caught: self.load(source)
        self.assertEqual(caught.exception.reason,'METADATA_UNAVAILABLE')
        self.assertFalse(Path(str(snapshot)+'-wal').exists())
        self.assertFalse(Path(str(snapshot)+'-shm').exists())

    def test_active_wal_on_readonly_directory(self):
        shared=self.root/'shared'; shared.mkdir()
        path=shared/'frigate.db'
        writer=sqlite3.connect(path)
        try:
            writer.execute(DDL); writer.commit()
            writer.execute('PRAGMA journal_mode=WAL')
            writer.execute('PRAGMA wal_autocheckpoint=0')
            writer.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            writer.execute("INSERT INTO recordings VALUES ('r1','front','/media/frigate/recordings/r1.mp4',10,20,10,NULL,NULL,NULL,NULL,NULL,NULL)")
            writer.commit()
            (self.media/'r1.mp4').write_bytes(b'whole-file')
            for file in shared.iterdir(): file.chmod(0o444)
            shared.chmod(0o555)
            source=self.source(); source.database=path
            self.assertEqual(len(self.load(source).descriptors),1)
        finally:
            shared.chmod(0o700)
            for file in shared.iterdir(): file.chmod(0o600)
            writer.close()

    def test_unreadable_db_permissions(self):
        if os.geteuid()==0: self.skipTest('permission test needs unprivileged user')
        self.dbpath.chmod(0)
        try:
            with self.assertRaises(HistoricalError): self.load()
        finally: self.dbpath.chmod(0o600)

    def test_uncommitted_rows_not_visible(self):
        self.row(); self.db.execute("UPDATE recordings SET end_time=30 WHERE id='r1'")
        self.assertEqual(self.load().descriptors[0].interval.end,'20')
        self.db.rollback()

    def test_missing_database(self):
        source=self.source(); source.database=self.root/'missing.db'
        with self.assertRaises(HistoricalError): self.load(source)
        self.assertFalse(source.database.exists())

    def test_unsupported_schema(self):
        self.db.execute('ALTER TABLE recordings ADD COLUMN future TEXT'); self.db.commit()
        with self.assertRaises(HistoricalError) as caught: self.load()
        self.assertEqual(caught.exception.reason,'UNSUPPORTED_FRIGATE_SCHEMA')

    def test_incomplete_schema(self):
        self.db.execute('DROP TABLE recordings'); self.db.commit()
        with self.assertRaises(HistoricalError): self.load()

    def test_wrong_version(self):
        with self.assertRaises(ValueError):
            FrigateRetainedSource(self.dbpath,self.media,self.root/'s',Interval('1','2'),version='0.19.0')

    def test_readonly_open_failure(self):
        with patch('nanexus_trust.frigate.retained.sqlite3.connect',side_effect=sqlite3.OperationalError('denied')):
            with self.assertRaises(HistoricalError) as caught: self.load()
        self.assertEqual(caught.exception.reason,'METADATA_UNAVAILABLE')

    def test_sql_connection_is_readonly(self):
        original=sqlite3.connect
        def connect(database_uri, **kw):
            self.assertIn('?mode=ro',database_uri); self.assertTrue(kw['uri'])
            db=original(database_uri,**kw)
            with self.assertRaises(sqlite3.OperationalError): db.execute('DELETE FROM recordings')
            db.rollback()
            return db
        with patch('nanexus_trust.frigate.retained.sqlite3.connect',side_effect=connect): self.load()

    def test_bounded_rows_and_forward_candidates(self):
        self.row('r1'); self.row('r2',20,30); self.row('r3',30,40)
        inventory=self.load(max_expansions=1)
        self.assertEqual(sum(d.availability is Availability.READY for d in inventory.descriptors),2)
        self.assertEqual(len(inventory.descriptors),3)  # Un-staged lookahead preserves resource exhaustion.
        with self.assertRaises(HistoricalError): self.load(max_rows=1)

    def test_context_limit_is_not_media_exhaustion(self):
        self.row('r1'); self.row('r2',20,30)
        service=HistoricalService(self.source(),SequenceExecutor([observation('PARTIAL',1,1)]),
                                  Component('fixture','1'),self.root/'evidence','trust',limits=Limits(max_expansions=0))
        operation=service.verify('front','11','19')
        self.assertEqual(operation.result.reason,'RESOURCE_LIMIT')
        self.assertEqual(operation.result.verification.value,'UNKNOWN')

    def test_duplicate_paths_fail(self):
        self.row('r1'); self.row('r2',20,30,path='/media/frigate/recordings/r1.mp4')
        with self.assertRaises(HistoricalError): self.load()


class StagingTests(Fixture):
    def test_whole_file_copy_survives_retention_after_staging(self):
        self.row(); source=self.source(); descriptor=self.load(source).descriptors[0]
        (self.media/'r1.mp4').unlink()
        self.assertEqual(source.read(descriptor,Limits()),b'whole-signed-media')
        root=source.root; source.close(); self.assertFalse(root.exists())
        self.assertTrue(self.dbpath.exists())

    def test_disappeared_after_metadata_snapshot(self):
        self.row(); source=self.source(); original=source._rows
        def rows(*args):
            result=original(*args); (self.media/'r1.mp4').unlink(); return result
        source._rows=rows
        self.assertIs(self.load(source).descriptors[0].availability, Availability.MISSING)

    def test_changes_during_copy(self):
        self.row(); source=self.source(); original=source._copy
        def copy(*args):
            value=original(*args); (self.media/'r1.mp4').write_bytes(b'changed'); return value
        source._copy=copy
        self.assertIs(self.load(source).descriptors[0].availability,Availability.NOT_READY)
        self.assertEqual(list(source.root.iterdir()),[])

    def test_deletion_during_copy(self):
        self.row(); source=self.source(); original=source._copy
        def copy(*args):
            value=original(*args); (self.media/'r1.mp4').unlink(); return value
        source._copy=copy
        self.assertIs(self.load(source).descriptors[0].availability,Availability.MISSING)

    def test_recent_file_not_ready(self):
        self.row(); source=self.source(); source.settle_ns=60*10**9
        self.assertIs(self.load(source).descriptors[0].availability,Availability.NOT_READY)

    def test_empty_file_not_ready(self):
        self.row(data=b'')
        self.assertIs(self.load().descriptors[0].availability,Availability.NOT_READY)

    def test_path_traversal_and_outside_root(self):
        for path in ('/media/frigate/recordings/../secret','/etc/secret', '/media/frigate/recordings//r1.mp4'):
            with self.subTest(path=path):
                self.db.execute('DELETE FROM recordings'); self.db.commit(); self.row(path=path)
                with self.assertRaises(HistoricalError): self.load()

    def test_symlink_file_and_directory_escape(self):
        self.row(data=None); secret=self.root/'secret'; secret.write_bytes(b'private')
        (self.media/'r1.mp4').symlink_to(secret)
        self.assertIs(self.load().descriptors[0].availability,Availability.UNAVAILABLE)
        (self.media/'r1.mp4').unlink(); (self.media/'nested').symlink_to(self.root,target_is_directory=True)
        self.db.execute("UPDATE recordings SET path='/media/frigate/recordings/nested/secret'"); self.db.commit()
        self.assertIs(self.load().descriptors[0].availability,Availability.UNAVAILABLE)

    def test_oversized_media(self):
        self.row(data=b'x'*20)
        self.assertIs(self.load(max_bytes=10).descriptors[0].availability,Availability.UNAVAILABLE)

    def test_changed_owned_copy_rejected(self):
        self.row(); source=self.source(); d=self.load(source).descriptors[0]
        source._staged[d.recording_id].write_bytes(b'corrupt')
        with self.assertRaises(HistoricalError): source.read(d,Limits())

    def test_readiness_failures_never_invalid(self):
        self.row(data=None)
        service=HistoricalService(self.source(),SequenceExecutor([]),Component('fixture','1'),self.root/'evidence','trust')
        self.assertEqual(service.verify('front','11','19').result.verification.value,'UNKNOWN')


class JobsTests(Fixture):
    def factory(self, verdict='VALID'):
        self.executions=0
        @contextmanager
        def factory(camera,start,end,evidence):
            self.executions+=1
            source=self.source()
            pending=1 if verdict=='PARTIAL' else 0
            yield HistoricalService(source,SequenceExecutor([observation(verdict,pending,pending)]),
                                    Component('fixture','1'),evidence,'trust')
            source.close()
        return factory

    def store(self,factory=None,**kwargs):
        store=JobStore(self.root/'store',factory or self.factory(),**kwargs)
        self.addCleanup(lambda: None if store._closed else store.close())
        return store

    def test_all_completed_verdicts_and_portable_download(self):
        self.row()
        for verdict in ('VALID','PARTIAL','INVALID','UNKNOWN'):
            with self.subTest(verdict=verdict):
                store=JobStore(self.root/verdict,self.factory(verdict))
                try:
                    job=store.submit('front','11','19'); self.assertEqual(store.get(job)['state'],'queued')
                    self.assertTrue(store.run_next()); result=store.get(job)
                    self.assertEqual(result['state'],'completed')
                    self.assertEqual(result['result']['verification'],verdict)
                    summary=store.evidence_summary(result['evidence_id'])
                    self.assertEqual(summary['recognition'],'SUPPORTED')
                    data=store.download(result['evidence_id'])
                    with tempfile.TemporaryDirectory() as relocated:
                        with zipfile.ZipFile(io.BytesIO(data)) as archive: archive.extractall(relocated)
                        self.assertEqual(recognize_bundle(Path(relocated)).status.value,'SUPPORTED')
                finally: store.close()

    def test_accept_persisted_before_identity_and_running_visible(self):
        self.row(); factory=self.factory()
        @contextmanager
        def checking(*args):
            self.assertEqual(store.get(job)['state'],'running')
            with factory(*args) as service: yield service
        store=self.store(checking); job=store.submit('front','11','19')
        with sqlite3.connect(self.root/'store/jobs.sqlite') as read:
            self.assertEqual(read.execute('SELECT state FROM jobs').fetchone()[0],'queued')
        store.run_next(); self.assertEqual(store.get(job)['state'],'completed')

    def test_restart_completed_no_backend_execution(self):
        self.row(); store=self.store(); job=store.submit('front','11','19'); store.run_next()
        evidence=store.get(job)['evidence_id']; store.close()
        reopened=self.store(lambda *args: self.fail('must not reverify'))
        self.assertEqual(reopened.get(job)['state'],'completed')
        self.assertEqual(reopened.evidence_summary(evidence)['recognition'],'SUPPORTED')
        reopened.download(evidence); self.assertFalse(reopened.run_next())

    def test_restart_interrupts_accepted_queued_running(self):
        store=self.store(); jobs=[store.submit('front','11','19') for _ in range(3)]
        store.db.execute("UPDATE jobs SET state='accepted' WHERE id=?",(jobs[0],))
        store.db.execute("UPDATE jobs SET state='running' WHERE id=?",(jobs[1],)); store.db.commit(); store.close()
        reopened=self.store(lambda *a: self.fail('silent resume'))
        self.assertTrue(all(reopened.get(job)['state']=='interrupted' for job in jobs))
        self.assertFalse(reopened.run_next())
        new=reopened.submit('front','11','19'); self.assertNotIn(new,jobs)

    def test_worker_queries_do_not_wait_for_verification(self):
        self.row(); factory=self.factory(); entered=threading.Event(); release=threading.Event()
        @contextmanager
        def controlled(*args):
            entered.set()
            if not release.wait(1): raise RuntimeError('fixture gate timeout')
            with factory(*args) as service: yield service
        store=self.store(controlled); job=store.submit('front','11','19'); store.start()
        try:
            self.assertTrue(entered.wait(1))
            self.assertEqual(store.get(job)['state'],'running')
        finally:
            release.set()
            store.close()
        reopened=self.store(lambda *a: self.fail('no replay'))
        self.assertEqual(reopened.get(job)['state'],'completed')

    def test_persistence_failure_not_completed(self):
        self.row(); store=self.store(); job=store.submit('front','11','19')
        with patch.object(Path,'rename',side_effect=OSError('disk full')): store.run_next()
        result=store.get(job); self.assertEqual(result['state'],'failed'); self.assertIsNone(result['evidence_id'])

    def test_completion_transaction_failure_not_completed(self):
        self.row(); store=self.store(); job=store.submit('front','11','19')
        store.db.execute("CREATE TRIGGER reject_complete BEFORE UPDATE OF state ON jobs WHEN new.state='completed' BEGIN SELECT RAISE(ABORT,'disk fault'); END")
        store.db.commit(); store.run_next()
        self.assertEqual(store.get(job)['state'],'failed')
        self.assertIsNone(store.get(job)['evidence_id'])

    def test_missing_receipt_not_completed(self):
        self.row(); factory=self.factory()
        @contextmanager
        def broken(*args):
            with factory(*args) as service:
                original=service.verify
                def verify(*a):
                    result=original(*a)
                    (service.evidence_root/result.result.request.request_id/'receipt.json').unlink()
                    return result
                service.verify=verify
                yield service
        store=self.store(broken); job=store.submit('front','11','19'); store.run_next()
        self.assertEqual(store.get(job)['state'],'failed')

    def test_opaque_ids_and_queue_bounds(self):
        store=self.store(max_jobs=2,max_queued=1); store.submit('front','11','19')
        with self.assertRaises(ValueError): store.submit('front','11','19')
        for bad in ('../../secret','/etc/passwd','not-an-id'):
            with self.assertRaises(ValueError): store.get(bad)
            with self.assertRaises(ValueError): store.download(bad)
        with self.assertRaises(ValueError): store.submit('front','20','10')

    def test_single_store_owner(self):
        self.store()
        with self.assertRaises(ValueError): JobStore(self.root/'store',self.factory())


class LiveIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.time='0'; self.sources=[]; self.writers=[]
        self.binding=Binding(CameraId('front'),RenditionId('main'),'configured-source','revision-1',POLICY)
        self.mapping=PlaybackMapping('front','main-stream','single',self.binding)
        self.live=LiveScheduler((self.mapping,),self.factory,clock=self.clock)
        self.addCleanup(self.live.close)
        self.addCleanup(self.close_writers)

    def clock(self): return self.time,'2026-01-01T00:00:00Z'
    def factory(self,binding):
        read,write=os.pipe(); self.writers.append(write)
        source=FDObservationSource(read,clock=self.clock); self.sources.append(source); return source
    def close_writers(self):
        for fd in self.writers:
            try: os.close(fd)
            except OSError: pass
    def query(self): return self.live.query('front','main-stream','single')
    def active(self):
        self.query(); os.write(self.writers[-1],PREFIX+MEDIA+SIGNING)
        return self.query()

    def test_exact_mapping_and_missing(self):
        self.assertEqual(self.active()['status'],'ACTIVE')
        self.assertEqual(self.live.query('front','other','grid')['status'],'UNKNOWN')

    def test_ambiguous_playback_and_logical_mapping(self):
        self.active(); self.live.configure((self.mapping,self.mapping))
        self.assertEqual(self.query()['status'],'UNKNOWN')
        other=replace(self.mapping,stream='other',binding=replace(self.binding,source_id='conflicting'))
        self.live.configure((self.mapping,other)); self.assertEqual(self.query()['status'],'UNKNOWN')

    def test_same_binding_can_serve_multiple_explicit_contexts(self):
        grid=replace(self.mapping,context='grid')
        self.live.configure((self.mapping,grid))
        self.assertEqual(self.active()['status'],'ACTIVE')
        self.assertEqual(self.live.query('front','main-stream','grid')['status'],'UNKNOWN')

    def test_revision_and_source_replacement_reset(self):
        old=self.active()['session_id']; source=self.sources[-1]
        updated=replace(self.mapping,binding=replace(self.binding,revision='revision-2',source_id='replacement'))
        self.live.configure((updated,)); result=self.query()
        self.assertEqual(result['status'],'UNKNOWN'); self.assertNotEqual(result['session_id'],old)
        self.assertTrue(source.closed); self.assertIsNone(source.next_event('0'))

    def test_selected_rendition_change_does_not_borrow_status(self):
        secondary=replace(self.mapping,stream='sub-stream',context='grid',binding=replace(self.binding,rendition=RenditionId('sub')))
        self.live.configure((self.mapping,secondary)); self.active()
        result=self.live.query('front','sub-stream','grid'); self.assertEqual(result['status'],'UNKNOWN')
        self.assertEqual(self.query()['status'],'UNKNOWN')

    def test_eof_reconnect_and_old_generation_rejected(self):
        old=self.active()['session_id']; source=self.sources[-1]
        os.close(self.writers[-1]); self.writers.pop()
        self.assertEqual(self.query()['status'],'UNKNOWN')
        self.live.tick(); self.live.tick(); result=self.query()
        self.assertNotEqual(result['session_id'],old); self.assertEqual(result['status'],'UNKNOWN')
        self.assertTrue(source.closed); self.assertIsNone(source.next_event('0'))

    def test_explicit_reconnect_reset_and_shutdown(self):
        self.active(); self.live.reconnect('front','main'); self.assertEqual(self.query()['status'],'UNKNOWN')
        self.live.start(); self.live.close(); self.assertTrue(all(s.closed for s in self.sources))
        self.assertEqual(self.query()['status'],'UNKNOWN')

    def test_config_context_change_and_freshness_expiry(self):
        self.active(); self.time='5'
        self.assertEqual(self.query()['status'],'UNKNOWN')
        updated=replace(self.mapping,context='grid')
        old_source=self.sources[-1]; self.live.configure((updated,))
        self.assertTrue(old_source.closed)
        self.assertEqual(self.live.query('front','main-stream','grid')['status'],'UNKNOWN')

    def test_bounded_nonblocking_reads(self):
        self.query(); self.assertIsNone(self.sources[-1].next_event('0'))
        os.write(self.writers[-1],b'123456789')
        self.sources[-1].chunk_bytes=4
        self.assertEqual(len(self.sources[-1].next_event('0').data),4)

    def test_source_error(self):
        self.active()
        with patch('nanexus_trust.companion.live.os.read',side_effect=OSError('private diagnostics')):
            with patch('nanexus_trust.companion.live.select.select',return_value=([self.sources[-1].fd],[],[])):
                self.assertEqual(self.query()['status'],'UNKNOWN')


class BoundaryTests(Fixture):
    factory = JobsTests.factory
    store = JobsTests.store
    def boundary(self):
        live=LiveScheduler((),lambda b: None)
        self.addCleanup(live.close)
        return CompanionService(live,self.store(),('front',))

    def test_submit_query_evidence_health_readiness(self):
        self.row(); service=self.boundary()
        self.assertTrue(service.health()['healthy']); self.assertTrue(service.readiness()['ready'])
        self.assertEqual(service.live_status('front','absent','single')['status'],'UNKNOWN')
        job=service.submit('front','11','19')['job_id']; service.jobs.run_next()
        result=service.job(job); self.assertEqual(result['result']['verification'],'VALID')
        self.assertEqual(service.evidence_summary(result['evidence_id'])['recognition'],'SUPPORTED')
        self.assertTrue(service.download(result['evidence_id']).startswith(b'PK'))
        self.assertEqual(service.list_mappings(),[])
        document=json.dumps(result); self.assertNotIn(str(self.root),document)
        self.assertNotIn('rtsp',document)

    def test_unauthorized_camera_and_evidence(self):
        self.row(); service=self.boundary()
        with self.assertRaises(ValueError): service.submit('back','11','19')
        job=service.submit('front','11','19')['job_id']; service.jobs.run_next()
        evidence=service.job(job)['evidence_id']
        service.cameras=frozenset(('back',))
        with self.assertRaises(ValueError): service.job(job)
        with self.assertRaises(ValueError): service.download(evidence)

    def test_real_profile_factory_wiring(self):
        self.row()
        profile=HistoricalProfile(self.dbpath,self.media,self.root/'scratch',SequenceExecutor([observation('UNKNOWN')]),
                                  Component('fixture','1'),'trust')
        with profile.service('front','11','19',self.root/'profile-evidence') as service:
            result=service.verify('front','11','19')
        self.assertEqual(result.result.verification.value,'UNKNOWN')
        self.assertIsNotNone(result.evidence_reference)


if __name__ == '__main__': unittest.main()
