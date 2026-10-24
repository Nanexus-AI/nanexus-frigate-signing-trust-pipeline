"""D2.1 bounded synthetic ext4 publication/recovery; no native execution."""
from contextlib import ExitStack
from dataclasses import replace
import errno,json,os,signal,subprocess,sys,tempfile,threading,unittest
from pathlib import Path
from nanexus_trust.historical_v2 import wire,contracts as a,publication as p
from nanexus_trust.historical_v2.publication_store import PublicationStore
from helpers.historical_v2_publication_faults import fixture,SyntheticAuthority

ROOT=Path(__file__).resolve().parents[1]
ART=Path(os.environ.get('HV2_PUBLICATION_TEST_ROOT',ROOT/'.local/governance/ai-dlc/evidence/m1-hv2-d-planning/d2.1-artifacts'))

class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='store-test-',dir=ART);self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'store';self.auth=SyntheticAuthority()
        self.store=PublicationStore(self.path,self.auth,max_bytes=8<<20);self.addCleanup(self.store.close)
        self.c=fixture();self.pid=self.store.create(self.c)
    def publish(self,key='first',candidate=None):
        try:head=self.store.current(self.pid,'owner');expected=head['pages'] and p.digest((self.path/'publications'/self.pid/'generations'/head['generation']/'manifest.json').read_bytes())
        except p.PublicationError as e:
            if e.code!='UNAVAILABLE':raise
            expected=None
        return self.store.publish(self.pid,key,candidate or self.c,expected_head=expected)
    def error(self,code,fn):
        with self.assertRaises(p.PublicationError) as ctx:fn()
        self.assertEqual(ctx.exception.code,code)
    def test_two_real_a_pages_and_full_closure(self):
        h=self.publish();m=self.store.current(self.pid,'owner');self.assertEqual(m['generation'],h['generation'])
        first=wire.loads(self.store.page(self.pid,'owner'));second=wire.loads(self.store.page(self.pid,'owner',next_cursor=first.next_cursor))
        self.assertEqual((len(first.observations),len(second.observations)),(128,60));self.assertEqual(len({o.observation_id for o in first.observations+second.observations}),188)
        for page in (first,second):
            self.assertEqual(page.lifecycle,a.Lifecycle.INCOMPLETE);self.assertFalse(page.output_complete);self.assertEqual(len(page.unresolved_refs),16)
            self.assertEqual(page.coverage[0].state,a.CoverageState.UNKNOWN);self.assertTrue(all(o.placement.status is a.PlacementStatus.UNPLACED for o in page.observations))
        self.assertIsNone(second.next_cursor);self.assertEqual(m['finalized'],188);self.assertEqual(len(m['pages']),2)
        for d in m['objects']:self.assertEqual(p.digest(self.store.object(self.pid,'owner',d['id'])),d['sha256'])
    def test_idempotency_conflict_and_separate_job_identity(self):
        first=self.publish();self.assertEqual(self.store.publish(self.pid,'first',self.c),first)
        self.error('CONFLICT',lambda:self.store.publish(self.pid,'first',fixture('attempt-2')))
        second=self.store.publish(self.pid,'second',fixture('attempt-2'),expected_head=first['sha256'])
        old=wire.loads(self.store.page(self.pid,'owner',generation=first['generation']));new=wire.loads(self.store.page(self.pid,'owner'))
        self.assertNotEqual(old.job_id,new.job_id);self.assertEqual(self.store.create(fixture('attempt-2')),self.pid);self.assertEqual(second['sequence'],2)
        self.error('CONFLICT',lambda:self.store.publish(self.pid,'stale',fixture('attempt-3'),expected_head=first['sha256']))
    def test_cursor_tamper_cross_generation_and_expiry(self):
        h=self.publish();first=wire.loads(self.store.page(self.pid,'owner'));cursor=first.next_cursor
        self.error('CORRUPT',lambda:self.store.page(self.pid,'owner',next_cursor=cursor[:-1]+('0' if cursor[-1]!='0' else '1')))
        self.error('CORRUPT',lambda:self.store.page(self.pid,'owner',next_cursor=cursor,generation='0'*64))
        self.error('UNAVAILABLE',lambda:self.store.page(self.pid,'owner',next_cursor=p.cursor(self.pid,'0'*64,1)))
        self.error('UNAVAILABLE',lambda:self.store.page(self.pid,'owner',generation='0'*64))
        self.store.publish(self.pid,'second',fixture('attempt-2'),expected_head=h['sha256'])
        old=wire.loads(self.store.page(self.pid,'owner',next_cursor=cursor));self.assertEqual(old.job_id,self.c.result.page.job_id)
    def test_authority_checks_and_revocation_before_commit(self):
        self.publish();self.error('ACCESS_DENIED',lambda:self.store.page(self.pid,'other'))
        self.auth.reads=False;self.error('ACCESS_DENIED',lambda:self.store.current(self.pid,'owner'));self.auth.reads=True
        previous=(self.path/'publications'/self.pid/'control.json').read_bytes()
        def fault(point):
            if point=='validated':self.auth.revoke()
        self.store._fault=fault
        self.error('ACCESS_DENIED',lambda:self.store.publish(self.pid,'second',fixture('attempt-2'),expected_head=json.loads(previous)['head']['sha256']))
        current=json.loads((self.path/'publications'/self.pid/'control.json').read_bytes());self.assertEqual(current['head'],json.loads(previous)['head'])
        self.error('ACCESS_DENIED',lambda:self.store.object(self.pid,'owner','anything'))
    def test_corrupt_manifest_page_object_and_control_no_fallback(self):
        h=self.publish();m=self.store.current(self.pid,'owner');base=self.path/'publications'/self.pid/'generations'/h['generation']
        targets=[base/'manifest.json',base/'pages/0001.json',base/'objects'/(m['objects'][0]['sha256']+'.blob'),self.path/'publications'/self.pid/'control.json']
        for target in targets:
            original=target.read_bytes();target.write_bytes(original[:-1])
            self.error('CORRUPT',lambda:self.store.current(self.pid,'owner'));target.write_bytes(original)
        target=base/'pages/0001.json';value=target.read_bytes();target.unlink();self.error('CORRUPT',lambda:self.store.page(self.pid,'owner'));target.write_bytes(value);target.chmod(0o600)
    def test_enospc_partial_write_and_post_head_unknown(self):
        h=self.publish();old=self.store.page(self.pid,'owner')
        def fault(point):
            if point=='object-partial':raise OSError(errno.ENOSPC,'synthetic IO fault')
        self.store._fault=fault;self.error('IO_FAILURE',lambda:self.store.publish(self.pid,'failed',fixture('attempt-2'),expected_head=h['sha256']))
        self.assertEqual(self.store.page(self.pid,'owner'),old)
        self.store.close();self.store=PublicationStore(self.path,self.auth,max_bytes=8<<20);self.addCleanup(self.store.close)
        self.assertTrue(self.store.recovery);self.error('INTERRUPTED',lambda:self.store.publish(self.pid,'failed',fixture('attempt-2')))
    def test_control_sync_unknown_resolves_idempotently(self):
        h=self.publish()
        def fault(point):
            if point=='head-transition':raise OSError(errno.EIO,'synthetic sync failure')
        self.store._fault=fault;self.error('COMMIT_OUTCOME_UNKNOWN',lambda:self.store.publish(self.pid,'second',fixture('attempt-2'),expected_head=h['sha256']))
        self.store._fault=lambda _:None;resolved=self.store.publish(self.pid,'second',fixture('attempt-2'));self.assertEqual(resolved['sequence'],2)
        self.store.close();self.store=PublicationStore(self.path,self.auth,max_bytes=8<<20);self.addCleanup(self.store.close);self.assertEqual(self.store.current(self.pid,'owner')['generation'],resolved['generation'])
    def test_quota_retention_pins_and_no_gc(self):
        h=self.publish()
        with ExitStack() as stack:
            for _ in range(4):stack.enter_context(self.store.pin(self.pid,'owner'))
            self.error('BUSY',lambda:self.store.current(self.pid,'owner'))
            self.error('BUSY',lambda:self.store.close())
        second=self.store.publish(self.pid,'second',fixture('attempt-2'),expected_head=h['sha256'])
        third=self.store.publish(self.pid,'third',fixture('attempt-3'),expected_head=second['sha256'])
        self.error('QUOTA',lambda:self.store.publish(self.pid,'fourth',fixture('attempt-4'),expected_head=third['sha256']))
        self.assertEqual(len(list((self.path/'publications'/self.pid/'generations').iterdir())),3)
        self.assertEqual(wire.loads(self.store.page(self.pid,'owner',generation=h['generation'])).job_id,self.c.result.page.job_id)
    def test_small_quota_and_exclusive_owner(self):
        self.error('BUSY',lambda:PublicationStore(self.path,self.auth,max_bytes=8<<20))
        self.store.max_bytes=65536;self.error('QUOTA',lambda:self.publish())
        self.error('UNSAFE_PATH',lambda:PublicationStore(str(self.path)+'/../escape',self.auth))
    def test_symlink_hardlink_and_unknown_staging_are_never_followed(self):
        h=self.publish();m=self.store.current(self.pid,'owner');obj=self.path/'publications'/self.pid/'generations'/h['generation']/'objects'/(m['objects'][0]['sha256']+'.blob')
        raw=obj.read_bytes();obj.unlink();outside=Path(self.temp.name)/'harmless';outside.write_bytes(raw);outside.chmod(0o600);obj.symlink_to(outside)
        self.error('CORRUPT',lambda:self.store.current(self.pid,'owner'));self.assertEqual(outside.read_bytes(),raw);obj.unlink();os.link(outside,obj)
        self.error('UNSAFE_PATH',lambda:self.store.current(self.pid,'owner'));obj.unlink();obj.write_bytes(raw);obj.chmod(0o600)
        foreign=self.path/'publications'/self.pid/'staging'/'foreign';foreign.mkdir(mode=0o700);(foreign/'untouched').write_text('foreign')
        self.store.close();self.store=PublicationStore(self.path,self.auth,max_bytes=8<<20);self.addCleanup(self.store.close);self.assertEqual((foreign/'untouched').read_text(),'foreign');self.assertEqual(self.store.current(self.pid,'owner')['generation'],h['generation'])
        self.error('RECOVERY_PENDING',lambda:self.store.publish(self.pid,'blocked',fixture('attempt-2'),expected_head=h['sha256']))
    def test_reader_pin_and_concurrent_head_transition(self):
        first=self.publish();old=wire.loads(self.store.page(self.pid,'owner'));ready=threading.Event();resume=threading.Event();box=[]
        def fault(point):
            if point=='validated':ready.set();self.assertTrue(resume.wait(3))
        self.store._fault=fault
        def writer():
            try:box.append(self.store.publish(self.pid,'second',fixture('attempt-2'),expected_head=first['sha256']))
            except BaseException as e:box.append(e)
        with self.store.pin(self.pid,'owner'):
            t=threading.Thread(target=writer);t.start();self.assertTrue(ready.wait(3))
            self.assertEqual(wire.loads(self.store.page(self.pid,'owner')),old);self.assertTrue(self.store._pins)
            resume.set();t.join(4);self.assertFalse(t.is_alive());self.assertIsInstance(box[0],dict)
            self.assertEqual(wire.loads(self.store.page(self.pid,'owner',generation=first['generation'])),old)
    def test_closed_versions_and_bad_binding_fail_before_publication(self):
        g=p.build(self.pid,1,None,'d'*64,self.c)
        for field,value in [('schema','unknown'),('finalized',True),('all_pages_present',1),('extra',1)]:
            m=json.loads(g.manifest);m[field]=value
            with self.assertRaises(p.PublicationError):p.manifest(p.canonical(m))
        obj=dict(g.objects);obj['composition-base']=obj['composition-base'][:-1]
        with self.assertRaises(p.PublicationError):p.recognize(g.manifest,lambda d:obj[d['id']],lambda d:g.pages[d['ordinal']])
        altered=replace(self.c,worker_binary='not-a-digest')
        with self.assertRaises(p.PublicationError):self.store.publish(self.pid,'bad',altered)
        self.error('UNAVAILABLE',lambda:self.store.current(self.pid,'owner'))

    def test_unsupported_complete_and_explicit_profile_rejection(self):
        page=self.c.result.page
        complete=replace(page,lifecycle=a.Lifecycle.COMPLETE,progress=replace(page.progress,phase=a.Lifecycle.COMPLETE,finalized=page.progress.scanned),unresolved_refs=(),unprocessed_refs=(),output_complete=True)
        candidate=replace(self.c,result=replace(self.c.result,page=complete))
        self.error('UNSUPPORTED',lambda:self.store.publish(self.pid,'complete',candidate))
        self.error('UNSUPPORTED',lambda:self.store.publish(self.pid,'profile',replace(self.c,provider='unknown')))
        wrong=replace(self.c,attempt=replace(self.c.attempt,camera='other'))
        self.error('ACCESS_DENIED',lambda:self.store.create(wrong))
    def test_control_checksum_and_previous_generation_are_independent(self):
        first=self.publish();second=self.store.publish(self.pid,'second',fixture('attempt-2'),expected_head=first['sha256'])
        path=self.path/'publications'/self.pid/'generations'/second['generation']/'manifest.json';path.write_bytes(b'corrupt')
        self.error('CORRUPT',lambda:self.store.current(self.pid,'owner'))
        self.assertEqual(wire.loads(self.store.page(self.pid,'owner',generation=first['generation'])).job_id,self.c.result.page.job_id)
        control=self.path/'publications'/self.pid/'control.json';doc=json.loads(control.read_bytes());doc['revision']+=1;control.write_bytes(p.canonical(doc))
        self.error('CORRUPT',lambda:self.store.current(self.pid,'owner'))
    def test_retrieval_reauthorizes_at_byte_handoff(self):
        from contextlib import contextmanager
        self.publish();original=self.auth.retrieval_guard
        @contextmanager
        def revoked(binding,caller):
            self.auth.revoke()
            with original(binding,caller):yield
        self.auth.retrieval_guard=revoked
        self.error('ACCESS_DENIED',lambda:self.store.page(self.pid,'owner'))
        self.assertFalse(self.store._pins)
    def test_manifest_limits_and_bounded_foreign_recovery(self):
        generation=p.build(self.pid,1,None,'d'*64,self.c)
        for field,value in [('objects',json.loads(generation.manifest)['objects']*65),('pages',json.loads(generation.manifest)['pages']*17)]:
            m=json.loads(generation.manifest);m[field]=value
            with self.assertRaises(p.PublicationError):p.manifest(p.canonical(m))
        outside=Path(self.temp.name)/'sentinel';outside.mkdir(mode=0o700)
        link=Path(self.temp.name)/'alias';link.symlink_to(outside,target_is_directory=True)
        with self.assertRaises((OSError,p.PublicationError)):PublicationStore(link,self.auth)
        self.assertEqual(list(outside.iterdir()),[])
        for i in range(3):self.store.create(fixture(request_id='request-'+str(i)))
        self.error('QUOTA',lambda:self.store.create(fixture(request_id='request-overflow')))

    def test_persisted_idempotency_limit_never_forgets_associations(self):
        first=self.publish()
        with self.store._publication(self.pid) as fd:
            control=self.store._control(fd,self.pid)
            for i in range(15):
                control['attempts'].append({'key':p.digest(str(i).encode()),'input':'a'*64,'generation':p.digest(('orphan-'+str(i)).encode()),'status':'INTERRUPTED','head':None})
            control['revision']+=1
            self.store._replace(fd,'control.json',self.store._control_bytes(control))
        self.error('QUOTA',lambda:self.store.publish(self.pid,'seventeenth',fixture('attempt-2'),expected_head=first['sha256']))
        self.assertEqual(self.store.publish(self.pid,'first',self.c),first)
    def test_revocation_waits_for_the_linearized_head_transition(self):
        import time
        first=self.publish();transition=threading.Event();resume=threading.Event();revoked=threading.Event();box=[]
        def fault(point):
            if point=='head-transition':transition.set();self.assertTrue(resume.wait(3))
        self.store._fault=fault
        def publish():
            try:box.append(self.store.publish(self.pid,'second',fixture('attempt-2'),expected_head=first['sha256']))
            except BaseException as e:box.append(e)
        t=threading.Thread(target=publish);t.start();self.assertTrue(transition.wait(3))
        r=threading.Thread(target=lambda:(self.auth.revoke(),revoked.set()));r.start();time.sleep(.02);self.assertFalse(revoked.is_set())
        resume.set();t.join(4);r.join(2);self.assertFalse(t.is_alive());self.assertFalse(r.is_alive());self.assertIsInstance(box[0],dict);self.assertTrue(revoked.is_set())
        self.error('ACCESS_DENIED',lambda:self.store.current(self.pid,'owner'))
        control=json.loads((self.path/'publications'/self.pid/'control.json').read_bytes());self.assertEqual(control['head']['sequence'],2)
    def test_safe_object_lookup_and_mode_checks(self):
        h=self.publish();outside=Path(self.temp.name)/'sentinel';outside.write_text('unchanged')
        self.error('UNAVAILABLE',lambda:self.store.object(self.pid,'owner','../../sentinel'))
        self.assertEqual(outside.read_text(),'unchanged')
        manifest=self.path/'publications'/self.pid/'generations'/h['generation']/'manifest.json';manifest.chmod(0o644)
        self.error('UNSAFE_PATH',lambda:self.store.current(self.pid,'owner'));manifest.chmod(0o600)

    def test_uncommitted_registry_temp_does_not_hide_committed_evidence(self):
        h=self.publish();self.store.close()
        temp=self.path/'registry.json.next';temp.write_bytes(b'foreign or interrupted metadata');temp.chmod(0o600)
        self.store=PublicationStore(self.path,self.auth,max_bytes=8<<20);self.addCleanup(self.store.close)
        self.assertEqual(self.store.current(self.pid,'owner')['generation'],h['generation']);self.assertEqual(temp.read_bytes(),b'foreign or interrupted metadata')
        self.error('RECOVERY_PENDING',lambda:self.store.publish(self.pid,'blocked',fixture('attempt-2'),expected_head=h['sha256']))

class CrashTests(unittest.TestCase):
    def test_process_kill_commit_boundaries(self):
        phases=('before-staging','object-partial','page0-synced','manifest-written','manifest-synced','generation-renamed','control-synced','head-transition','control-directory-synced')
        for phase in phases:
            with self.subTest(phase=phase),tempfile.TemporaryDirectory(prefix='crash-test-',dir=ART) as temp:
                root=Path(temp)/'store';auth=SyntheticAuthority()
                with PublicationStore(root,auth,max_bytes=8<<20) as store:
                    identity=store.create(fixture());first=store.publish(identity,'first',fixture());before=store.page(identity,'owner')
                args=[sys.executable,'-B',str(ROOT/'tests/helpers/historical_v2_publication_faults.py'),str(root),identity,first['sha256'],phase]
                child=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=8,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
                self.assertEqual(child.returncode,-signal.SIGKILL,child.stderr[-1000:])
                with PublicationStore(root,auth,max_bytes=8<<20) as recovered:
                    current=recovered.current(identity,'owner')
                    if phase in ('head-transition','control-directory-synced'):
                        self.assertEqual(current['sequence'],2);self.assertEqual(recovered.publish(identity,'candidate-2',fixture('attempt-2'))['sequence'],2)
                    else:
                        self.assertEqual(current['sequence'],1);self.assertEqual(recovered.page(identity,'owner'),before)
                        self.error_if_interrupted(recovered,identity)
                    self.assertEqual(wire.loads(recovered.page(identity,'owner',generation=first['generation'])).job_id,fixture().result.page.job_id)
    def error_if_interrupted(self,store,identity):
        with self.assertRaises(p.PublicationError) as e:store.publish(identity,'candidate-2',fixture('attempt-2'))
        self.assertEqual(e.exception.code,'INTERRUPTED')


class RetirementTests(unittest.TestCase):
    def setUp(self):
        from helpers.historical_v2_publication_faults import small_fixture
        self.fixture=small_fixture
        self.temp=tempfile.TemporaryDirectory(prefix='retirement-',dir=ART);self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'store';self.auth=SyntheticAuthority()
        self.store=PublicationStore(self.path,self.auth,max_bytes=8<<20);self.addCleanup(lambda:self.store.close())
        self.c=self.fixture();self.pid=self.store.create(self.c)
        self.first=self.store.publish(self.pid,'first',self.c)
        self.second=self.store.publish(self.pid,'second',self.fixture('small-2'),expected_head=self.first['sha256'])
        self.base=self.path/'publications'/self.pid
        self.target=self.base/'generations'/self.first['generation']
        self.healthy=self.store.page(self.pid,'owner')
    def control(self):
        return json.loads((self.base/'control.json').read_bytes())
    def error(self,code,fn):
        with self.assertRaises(p.PublicationError) as ctx:fn()
        self.assertEqual(ctx.exception.code,code)
    def upgrade(self):
        self.auth.grant_maintenance(self.store.root_id,self.pid)
        return self.store.upgrade(self.pid,self.control()['revision'])
    def retire(self):
        self.auth.grant_maintenance(self.store.root_id,self.pid,self.first['generation'])
        return self.store.retire(self.pid,self.first['generation'],self.control()['revision'])
    def finish(self):
        stats=[]
        for _ in range(12):
            r=self.store.cleanup_step(self.pid);stats.append(r)
            self.assertLessEqual(r['files_unlinked'],16);self.assertLessEqual(r['bytes_verified'],4<<20)
            self.assertLessEqual(r['metadata_checks'],128);self.assertLessEqual(r['directories_removed'],3)
            if r['status']=='DONE':return stats
        self.fail('cleanup failed to progress')
    def test_explicit_upgrade_preserves_immutable_evidence(self):
        before={str(x.relative_to(self.base)):x.read_bytes() for x in (self.base/'generations').rglob('*') if x.is_file()}
        old=self.control();self.error('ACCESS_DENIED',lambda:self.store.upgrade(self.pid,old['revision']))
        self.store.close();self.store=PublicationStore(self.path,self.auth,max_bytes=8<<20)
        self.assertEqual(self.control()['schema'],p.CONTROL_V1)
        self.upgrade();new=self.control()
        self.assertEqual(new['retained'],old['retained']);self.assertEqual(new['attempts'],old['attempts']);self.assertEqual(new['head'],old['head'])
        self.assertEqual(before,{str(x.relative_to(self.base)):x.read_bytes() for x in (self.base/'generations').rglob('*') if x.is_file()})
        self.assertEqual(self.store.upgrade(self.pid,0),new['revision'])
    def test_expiry_cleanup_permanent_identity_and_new_publication(self):
        self.upgrade();self.retire();self.assertTrue(self.target.exists())
        self.error('EXPIRED',lambda:self.store.page(self.pid,'owner',generation=self.first['generation']))
        self.error('EXPIRED',lambda:self.store.page(self.pid,'owner',next_cursor=p.cursor(self.pid,self.first['generation'],1)))
        self.error('UNAVAILABLE',lambda:self.store.page(self.pid,'owner',generation='0'*64))
        from unittest.mock import patch
        with patch.object(p.Candidate,'validate',side_effect=AssertionError('expired input must not recompose')):
            self.error('EXPIRED',lambda:self.store.publish(self.pid,'first',self.c))
        self.error('CONFLICT',lambda:self.store.publish(self.pid,'first',self.fixture('different')))
        self.error('CLEANUP_PENDING',lambda:self.store.publish(self.pid,'third',self.fixture('small-3'),expected_head=self.second['sha256']))
        stats=self.finish();self.assertFalse(self.target.exists());self.assertIsNone(self.control()['maintenance'])
        self.assertEqual(self.store.page(self.pid,'owner'),self.healthy)
        self.assertEqual(self.retire()['status'],'EXPIRED')
        third=self.store.publish(self.pid,'third',self.fixture('small-3'),expected_head=self.second['sha256'])
        self.assertEqual(third['sequence'],3)
        self.store.close();self.store=PublicationStore(self.path,self.auth,max_bytes=8<<20)
        self.error('EXPIRED',lambda:self.store.publish(self.pid,'first',self.c))
    def test_head_revision_pin_and_distinct_authority(self):
        self.auth.grant_maintenance(self.store.root_id,self.pid,self.first['generation'])
        self.error('UNSUPPORTED',lambda:self.retire());self.upgrade()
        self.auth.grant_maintenance(self.store.root_id,self.pid,self.second['generation'])
        self.error('HEAD_PROTECTED',lambda:self.store.retire(self.pid,self.second['generation'],self.control()['revision']))
        self.error('CONFLICT',lambda:self.store.retire(self.pid,self.first['generation'],0))
        with self.store.pin(self.pid,'owner',self.first['generation']):self.error('BUSY',lambda:self.retire())
        self.auth.revoke_maintenance();self.error('ACCESS_DENIED',lambda:self.retire_without_grant())
        self.retire();self.auth.revoke_maintenance();self.error('ACCESS_DENIED',lambda:self.store.cleanup_step(self.pid))
        self.assertTrue((self.target/'manifest.json').exists())
        self.auth.reads=False;self.error('ACCESS_DENIED',lambda:self.store.page(self.pid,'owner',generation=self.first['generation']))
    def retire_without_grant(self):
        return self.store.retire(self.pid,self.first['generation'],self.control()['revision'])
    def test_authority_revocation_before_unlink_and_unknown_retirement(self):
        self.upgrade()
        def fault(point):
            if point=='retire-transition':raise OSError(errno.EIO,'synthetic')
        self.store._fault=fault;self.error('RETIREMENT_OUTCOME_UNKNOWN',lambda:self.retire())
        self.assertTrue(self.target.exists());self.assertEqual(self.control()['attempts'][0]['status'],'EXPIRED')
        def fault(point):
            if point=='cleanup-before-unlink':self.auth.revoke_maintenance()
        self.store._fault=fault;self.error('ACCESS_DENIED',lambda:self.store.cleanup_step(self.pid))
        self.assertTrue((self.target/'manifest.json').exists());self.assertEqual(self.store.page(self.pid,'owner'),self.healthy)
        self.store._fault=lambda _:None;self.auth.grant_maintenance(self.store.root_id,self.pid,self.first['generation']);self.finish()
    def test_foreign_entry_and_replaced_inode_fail_closed(self):
        self.upgrade();self.retire();foreign=self.target/'objects/foreign';foreign.write_bytes(b'foreign');foreign.chmod(0o600)
        self.error('UNSAFE_PATH',lambda:self.store.cleanup_step(self.pid));self.assertEqual(foreign.read_bytes(),b'foreign');foreign.unlink()
        entry=self.control()['maintenance']['plan']['files'][0];f=self.target/'objects'/(entry['sha256']+'.blob')
        held=f.with_name('held');f.rename(held);f.write_bytes(held.read_bytes());f.chmod(0o600);held.unlink()
        self.error('UNSAFE_PATH',lambda:self.store.cleanup_step(self.pid));self.assertIsNotNone(self.control()['maintenance'])
    def test_unlink_before_checkpoint_remains_charged_and_resumes(self):
        self.upgrade();self.retire();before=self.store._usage()[0]
        def fault(point):
            if point=='cleanup-file-unlinked':raise OSError(errno.EIO,'synthetic')
        self.store._fault=fault;self.error('CLEANUP_PENDING',lambda:self.store.cleanup_step(self.pid))
        self.assertEqual(self.control()['maintenance']['next'],0);self.assertGreaterEqual(self.store._usage()[0],before)
        self.store.close();self.store=PublicationStore(self.path,self.auth,max_bytes=8<<20)
        self.assertTrue((self.pid,'CLEANUP_PENDING') in self.store.recovery)
        self.finish();self.assertEqual(self.store.page(self.pid,'owner'),self.healthy)
    def test_closed_schema_and_worst_case_control_size(self):
        import copy
        self.upgrade();self.retire();c=self.control();plan=c['maintenance']['plan'];template=copy.deepcopy(plan['files'][0])
        files=[]
        for i in range(64):files.append(dict(template,sha256=f'{i:064x}',inode=2**63-1,size=1))
        for i in range(16):files.append(dict(template,kind='page',ordinal=i,inode=2**63-1,size=1))
        files.append(plan['files'][-1]);plan['files']=files
        for i in range(14):c['attempts'].append(dict(key=f'{i+100:064x}',input='f'*64,generation=f'{i+1000:064x}',status='EXPIRED',head=dict(self.first,generation=f'{i+1000:064x}'),retired_revision=c['revision']))
        c['maintenance']['digest']=p.digest(p.canonical(plan));raw=self.store._control_bytes(c)
        self.assertLessEqual(len(raw),65536);p.control(p.load(raw),self.store.root_id,self.pid)
        for change in ('extra','unknown','cursor','digest'):
            bad=copy.deepcopy(c)
            if change=='extra':bad['extra']=1
            elif change=='unknown':bad['schema']='nanexus-hv2-control/3'
            elif change=='cursor':bad['maintenance']['next']=999
            else:bad['maintenance']['digest']='0'*64
            bad['checksum']=p.digest(p.canonical({k:v for k,v in bad.items() if k!='checksum'}))
            with self.assertRaises(p.PublicationError):p.control(bad,self.store.root_id,self.pid)
    def test_sigkill_retirement_cleanup_boundaries(self):
        # Each subprocess receives a fresh copy produced by publication, not a
        # copied live store; the original retained generations remain guarded.
        phases=('retire-before-replace','retire-transition','retire-directory-synced','cleanup-expiry-synced','cleanup-file-unlinked','cleanup-before-checkpoint','cleanup-checkpoint-transition','cleanup-manifest-removed','cleanup-generation-removed')
        self.upgrade();self.store.close()
        for phase in phases:
            with self.subTest(phase=phase),tempfile.TemporaryDirectory(prefix='gc-crash-',dir=ART) as temp:
                root=Path(temp)/'store';auth=SyntheticAuthority()
                with PublicationStore(root,auth,max_bytes=8<<20) as store:
                    pid=store.create(self.c);first=store.publish(pid,'first',self.c);second=store.publish(pid,'second',self.fixture('small-2'),expected_head=first['sha256'])
                    auth.grant_maintenance(store.root_id,pid);store.upgrade(pid,4)
                child=subprocess.run([sys.executable,str(ROOT/'tests/helpers/historical_v2_publication_faults.py'),'--gc',str(root),pid,first['generation'],phase],capture_output=True,timeout=12)
                self.assertEqual(child.returncode,-signal.SIGKILL,child.stderr.decode()[-1000:])
                with PublicationStore(root,auth,max_bytes=8<<20) as store:
                    auth.grant_maintenance(store.root_id,pid,first['generation'])
                    self.assertEqual(store.current(pid,'owner')['generation'],second['generation'])
                    c=json.loads((root/'publications'/pid/'control.json').read_bytes())
                    if phase=='retire-before-replace':self.assertEqual(len(c['retained']),2);self.assertTrue((root/'publications'/pid/'generations'/first['generation']/'manifest.json').exists())
                    else:
                        self.error('EXPIRED',lambda:store.page(pid,'owner',generation=first['generation']))
                        for _ in range(12):
                            if store.cleanup_step(pid)['status']=='DONE':break
                        else:self.fail('recovery made no progress')
                        self.assertFalse((root/'publications'/pid/'generations'/first['generation']).exists())

    def test_concurrent_pin_expiry_ordering(self):
        self.upgrade();ready=threading.Event();release=threading.Event();errors=[]
        def reader():
            try:
                with self.store.pin(self.pid,'owner',self.first['generation']):ready.set();release.wait(2)
            except Exception as exc:errors.append(exc)
        thread=threading.Thread(target=reader);thread.start()
        try:
            self.assertTrue(ready.wait(2));self.error('BUSY',lambda:self.retire())
        finally:release.set();thread.join(3)
        self.assertFalse(thread.is_alive());self.assertFalse(errors);self.retire()
        self.error('EXPIRED',lambda:self.store.page(self.pid,'owner',generation=self.first['generation']))
    def test_staging_preserved_and_no_startup_cleanup(self):
        self.upgrade();self.retire();stage=self.base/'staging'/'foreign';stage.mkdir(mode=0o700)
        note=stage/'untouched';note.write_bytes(b'deferred');note.chmod(0o600)
        self.store.close();self.store=PublicationStore(self.path,self.auth,max_bytes=8<<20)
        self.assertTrue(self.target.exists());self.assertEqual(note.read_bytes(),b'deferred')
        self.finish();self.assertEqual(note.read_bytes(),b'deferred')
    def test_symlink_hardlink_and_device_identity_protection(self):
        self.upgrade();self.retire();entry=self.control()['maintenance']['plan']['files'][0]
        f=self.target/'objects'/(entry['sha256']+'.blob');outside=Path(self.temp.name)/'outside';f.rename(outside)
        f.symlink_to(outside);self.error('CLEANUP_PENDING',lambda:self.store.cleanup_step(self.pid));f.unlink()
        os.link(outside,f);self.error('UNSAFE_PATH',lambda:self.store.cleanup_step(self.pid));f.unlink();outside.rename(f)
        olddev=self.store.dev;self.store.dev=-1
        try:self.error('UNSAFE_PATH',lambda:self.store.cleanup_step(self.pid))
        finally:self.store.dev=olddev
        self.finish()
    def test_maximum_plan_cleanup_work_bounds(self):
        # A trusted synthetic persisted plan exercises the physical step limits
        # independently of composer's currently small evidence descriptor set.
        self.upgrade();self.retire();c=self.control();plan=c['maintenance']['plan'];entries=plan['files']
        objects=[e for e in entries if e['kind']=='object'];pages=[e for e in entries if e['kind']=='page']
        for i in range(64-len(objects)):
            raw=(bytes([i+1])*(2<<20)) if i<2 else ('bounded-'+str(i)).encode()
            h=p.digest(raw);f=self.target/'objects'/(h+'.blob');f.write_bytes(raw);f.chmod(0o600)
            objects.append(dict(kind='object',sha256=h,size=len(raw),ordinal=None,inode=f.stat().st_ino))
        for i in range(len(pages),16):
            raw=('synthetic-page-'+str(i)).encode();f=self.target/'pages'/f'{i:04d}.json';f.write_bytes(raw);f.chmod(0o600)
            pages.append(dict(kind='page',sha256=p.digest(raw),size=len(raw),ordinal=i,inode=f.stat().st_ino))
        plan['files']=sorted(objects,key=lambda e:e['sha256'])+pages+[entries[-1]]
        c['maintenance']['digest']=p.digest(p.canonical(plan));(self.base/'control.json').write_bytes(self.store._control_bytes(c))
        stats=self.finish();self.assertGreater(len(stats),1);self.assertEqual(sum(r['files_unlinked'] for r in stats),81)
        self.assertEqual(sum(r['directories_removed'] for r in stats),3);self.assertEqual(self.store.page(self.pid,'owner'),self.healthy)

    def test_revocation_at_retirement_transition_preserves_live_target(self):
        self.upgrade()
        def fault(point):
            if point=='retire-before-replace':self.auth.revoke_maintenance()
        self.store._fault=fault;self.error('ACCESS_DENIED',lambda:self.retire())
        self.assertEqual(len(self.control()['retained']),2);self.assertTrue((self.target/'manifest.json').exists())
        self.store._fault=lambda _:None;self.retire();self.finish()
    def test_v2_interrupted_publish_recovers_without_downgrade(self):
        self.upgrade()
        def fault(point):
            if point=='before-head':raise OSError(errno.EIO,'synthetic')
        self.store._fault=fault
        self.error('IO_FAILURE',lambda:self.store.publish(self.pid,'third',self.fixture('small-3'),expected_head=self.second['sha256']))
        self.store.close();self.store=PublicationStore(self.path,self.auth,max_bytes=8<<20)
        self.assertIn((self.pid,'INTERRUPTED'),self.store.recovery)
        self.assertEqual(self.control()['schema'],p.CONTROL_V2)
        self.assertEqual(self.store.page(self.pid,'owner'),self.healthy)
        self.error('INTERRUPTED',lambda:self.store.publish(self.pid,'third',self.fixture('small-3')))

    def test_four_publication_root_and_registration_backpressure(self):
        for i in range(3):self.store.create(fixture('other-'+str(i),request_id='other-'+str(i),size=4,finalized=2))
        self.upgrade();self.retire()
        self.error('CLEANUP_PENDING',lambda:self.store.create(fixture('extra',request_id='extra',size=4,finalized=2)))
        self.finish();self.assertEqual(self.store.page(self.pid,'owner'),self.healthy)
    def test_maximum_scalar_control_encoding(self):
        import copy
        self.upgrade();self.retire();c=self.control();plan=c['maintenance']['plan'];maximum=2**63-1
        c['revision']=maximum
        # All text fields have fixed-length validated hex encodings. Maximize
        # every variable decimal field, while retaining feasible generation size.
        for i,e in enumerate(c['attempts']):
            e['head']['sequence']=maximum-i
            e['head']['size']=p.MAX_META
            if e['status']=='EXPIRED':e['retired_revision']=maximum
        c['head']=copy.deepcopy(c['attempts'][1]['head']);c['retained']=[copy.deepcopy(c['head'])]
        plan['target']=copy.deepcopy(c['attempts'][0]['head']);plan['protected']=copy.deepcopy(c['retained'])
        template=dict(kind='object',ordinal=None,inode=maximum,size=65536)
        plan['files']=[dict(template,sha256=f'{i:064x}') for i in range(64)]
        plan['files'] += [dict(template,kind='page',ordinal=i,sha256='f'*64) for i in range(16)]
        plan['files'].append(dict(template,kind='manifest',sha256=plan['target']['sha256'],size=p.MAX_META))
        plan['directories']={'generation':maximum,'objects':maximum-1,'pages':maximum-2}
        # Current head must remain greatest retained sequence; expired history
        # is non-owning and is deliberately allowed a separate sequence.
        third=dict(c['head'],generation='e'*64,sequence=maximum)
        c['head']=third;c['retained'].append(third);plan['protected']=copy.deepcopy(c['retained'])
        c['attempts'].append(dict(key='e'*64,input='f'*64,generation='e'*64,status='COMMITTED',head=third))
        for i in range(13):
            c['attempts'].append(dict(key=f'{100+i:064x}',input='f'*64,generation=f'{1000+i:064x}',status='EXPIRED',head=dict(plan['target'],generation=f'{1000+i:064x}'),retired_revision=maximum))
        c['maintenance'].update(next=84,phase='DELETING',digest=p.digest(p.canonical(plan)))
        raw=self.store._control_bytes(c);p.control(p.load(raw),self.store.root_id,self.pid)
        # 80 non-manifest file sizes can each add at most two decimal digits;
        # this deliberately overbounds allocations impossible under 8 MiB total.
        upper_bound=len(raw)+80*2
        self.assertLessEqual(upper_bound,65536)
        (ART/'maximum-control-bytes.json').write_text(json.dumps({'bytes':len(raw),'conservative_upper_bound':upper_bound,'associations':16,'files':81,'protected':2,'limit':65536}))
        bad=copy.deepcopy(plan);bad['files'][64]['size']=wire.MAX_DOCUMENT_BYTES+1
        with self.assertRaises(p.PublicationError):p.deletion_plan(bad)

    def test_permission_error_and_content_mismatch_preserve_pending(self):
        self.upgrade();self.retire()
        def fault(point):
            if point=='cleanup-before-unlink':raise PermissionError(errno.EACCES,'synthetic')
        self.store._fault=fault;self.error('CLEANUP_PENDING',lambda:self.store.cleanup_step(self.pid))
        self.store._fault=lambda _:None;entry=self.control()['maintenance']['plan']['files'][0]
        f=self.target/'objects'/(entry['sha256']+'.blob');raw=f.read_bytes();f.write_bytes(bytes([raw[0]^1])+raw[1:])
        self.error('CORRUPT',lambda:self.store.cleanup_step(self.pid));f.write_bytes(raw)
        self.finish();self.assertEqual(self.store.page(self.pid,'owner'),self.healthy)
    def test_two_other_retained_generations_preserve_all_bytes(self):
        third=self.store.publish(self.pid,'third',self.fixture('small-3'),expected_head=self.second['sha256'])
        protected=[self.base/'generations'/g['generation'] for g in (self.second,third)]
        before={str(f):p.digest(f.read_bytes()) for directory in protected for f in directory.rglob('*') if f.is_file()}
        self.upgrade();self.retire();self.finish()
        self.assertEqual(before,{str(f):p.digest(f.read_bytes()) for directory in protected for f in directory.rglob('*') if f.is_file()})
        self.assertEqual(self.store.current(self.pid,'owner')['generation'],third['generation'])
        self.assertEqual(self.store.page(self.pid,'owner',generation=self.second['generation']),self.healthy)

    def test_closed_store_denies_all_writer_admission(self):
        self.store.close()
        for operation in (lambda:self.store.create(self.c),lambda:self.store.publish(self.pid,'third',self.c),lambda:self.store.upgrade(self.pid,0),lambda:self.store.retire(self.pid,self.first['generation'],0),lambda:self.store.cleanup_step(self.pid)):
            self.error('UNAVAILABLE',operation)
        self.assertFalse(self.store._writer.locked())
