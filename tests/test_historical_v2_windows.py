"""Synthetic windows and actual A composition; no archived media or native runs."""
import base64
import copy
from dataclasses import replace
import hashlib
import json
import threading
import time
import unittest
from unittest.mock import patch

from nanexus_trust.historical_v2 import windows as w, window_results as r, composition as c, wire
from nanexus_trust.historical_v2.prototype import DirectCapturedProfile, DirectApiProfile, PrototypeFailure
from nanexus_trust.frigate.historical_v2 import ProviderFailure
from nanexus_trust.historical_v2.worker_updates import token_hex
import test_historical_v2_composition as compositions
import test_historical_v2_prototype as old
import test_historical_v2_api as api_tests


def synthetic(entry,item,request,negative=False):
    f=compositions.CompositionTests(); f.setUp()
    f.req=request
    f.sources=[dict(source,token=item.item_id+':'+str(i)) for i,source in enumerate(f.sources)]
    f.spec=replace(f.spec,request=request.request_id,camera=request.camera.native_key,binding=request.camera.binding_generation,
                   source=item.item_id,generation=item.generation,attempt='attempt-'+item.item_id)
    f.spec=replace(f.spec,run=c.run_identity(request,f.spec,f.sources))
    for u in f.updates:
        u.update(run_id=f.spec.run,attempt=f.spec.attempt)
        for i in u['items']:i['source_token_hex']=token_hex(f.sources[i['received_ordinal']]['token'])
    if negative:
        first=f.updates[1]['items'][0];first.update(state=1,raw_status='N');first['axes']['authenticity']=2
    composed=f.call();c.validate(composed,request,f.spec,f.sources,f.raws())
    raw=wire.dumps(composed.page)
    entry.update(status='RESULT',input={'nal_occurrences':2,'annex_b_bytes':10},execution={'state':'EXITED_SUCCESSFULLY','synthetic':True},
                 result={'page':json.loads(raw),'sha256':c.sha(raw),'size':len(raw),
                  'objects':[{'id':key,'sha256':c.sha(value),'size':len(value),'encoding':'base64','data':base64.b64encode(value).decode()} for key,value in composed.objects],
                  'source_ledger':[{'token':source['token']} for source in f.sources],'original_file_sha256':'a'*64})


class WindowTests(old.Harness):
    def setUp(self):
        super().setUp();self.coordinator.close()
        self.now=[100.0]
        self.coordinator=w.WindowCoordinator(self.profile(),clock=lambda:self.now[0])
        self.addCleanup(self.coordinator.close)
        self.cam=self.service._hv2_camera(self.camera,self.guard)
        self.visited=[]
    def profile(self):return DirectCapturedProfile(**vars(super().profile()))
    def stub(self,provider,item,bounds,cancel,request,camera,rid,permission,event,deadline,check,entry):
        check();self.visited.append(item.item_id);synthetic(entry,item,request)
    def run_plan(self,n):
        import sqlite3
        with sqlite3.connect(self.database) as db:db.execute('DELETE FROM recordings')
        for i in reversed(range(n)):self.row('r'+str(i).zfill(3),i*10,(i+1)*10)
        self.coordinator.profile=self.profile()
        with patch.object(self.coordinator,'_verify_file',side_effect=self.stub):
            return self.coordinator.run('alice',self.cam,'1',str(max(2,n*10)),old.RID,lambda:None)
    def test_plans_order_overlap_and_final_partial(self):
        self.measurements=[]
        for n in (0,1,29,30,31,59,60,180):
            with self.subTest(n=n):
                # Fresh disposable catalog per plan; no replayed media/evidence.
                self.do_plan(n)
        (old.ROOT/'.local/governance/ai-dlc/evidence/m1-hv2-d-planning/hr4-artifacts/window-matrix.json').write_text(json.dumps(self.measurements,indent=2))
    def do_plan(self,n):
        import sqlite3
        with sqlite3.connect(self.database) as db:db.execute('DELETE FROM recordings')
        self.visited.clear();started=time.monotonic();result=self.run_plan(n)
        self.measurements.append(dict(files=n,seconds=time.monotonic()-started,windows=len(result.get('windows',[])),attempts=len(self.visited),retained_evidence_bytes=result.get('retained_bytes',0),summary_bytes=len(c.canonical(result))))
        expected=['r'+str(i).zfill(3) for i in range(n)]
        self.assertEqual(self.visited,expected)
        self.assertEqual(result['status'],'RESULT' if n else 'NO_MATCHING_RECORDING')
        if not n:
            self.assertEqual(result['reason'],'No recordings were selected in this bounded catalog observation.')
        self.assertEqual([row['recording'] for row in result['files']],expected)
        self.assertEqual([row['members'] for row in result.get('windows',[])],[[expected[i] for i in indices] for indices in w.windows(n)])
        if n:
            self.assertEqual(result['input']['nal_occurrences'],n*2)
            self.assertEqual(sum(sum(row['finalized_counts'].values()) for row in result['files']),n)
            self.assertEqual(sum(row['unresolved'] for row in result['files']),n)
        self.assertEqual(result['coverage'],'UNKNOWN');self.assertEqual(result['continuity'],'UNKNOWN')
        if n==180:self.assertEqual(len(result['windows']),7)
        self.coordinator.release('alice',self.cam,old.RID,lambda:None)
        self.assertEqual(list(self.scratch.iterdir()),[])
    def test_complete_evidence_reconstruct_counts_and_integrity(self):
        result=self.run_plan(1);row=result['files'][0];ref=row['evidence']
        def fetch(identity):
            offset=0;data=b''
            while True:
                part=self.coordinator.evidence('alice',self.cam,old.RID,identity,offset,97,lambda:None)
                raw=base64.b64decode(part['data']);self.assertEqual(c.sha(raw),part['chunk_sha256'])
                self.assertEqual(part['offset'],offset);data+=raw;offset+=len(raw)
                if part['end']:
                    self.assertEqual(len(data),part['size']);self.assertEqual(c.sha(data),part['sha256']);return data
        metadata=json.loads(fetch(ref['id']));page=wire.loads(fetch(metadata['file']['result']['page_ref']))
        for descriptor in metadata['file']['result']['objects']:
            self.assertEqual(c.sha(fetch(descriptor['id'])),descriptor['sha256'])
        self.assertEqual(len(page.observations),sum(row['finalized_counts'].values()))
        for offset,count in ((-1,1),(ref['size']+1,1),(0,0),(0,(1<<20)+1),(True,1)):
            with self.assertRaises(PrototypeFailure):self.coordinator.evidence('alice',self.cam,old.RID,ref['id'],offset,count,lambda:None)
        with self.assertRaises(PrototypeFailure):self.coordinator.evidence('alice',self.cam,old.RID,'orphan',0,1,lambda:None)
    def test_owner_binding_permission_ttl_release_slot_reuse(self):
        result=self.run_plan(1);ref=result['files'][0]['evidence']['id']
        for method,args in ((self.coordinator.summary,()),(self.coordinator.evidence,(ref,0,1)),(self.coordinator.release,())):
            with self.assertRaisesRegex(PrototypeFailure,'UNAUTHORIZED'):method('mallory',self.cam,old.RID,*args,lambda:None)
            with self.assertRaisesRegex(PrototypeFailure,'UNAUTHORIZED'):method('alice',replace(self.cam,binding_generation='changed'),old.RID,*args,lambda:None)
            def revoked():raise PrototypeFailure('UNAUTHORIZED')
            with self.assertRaises(PrototypeFailure):method('alice',self.cam,old.RID,*args,revoked)
        self.now[0]+=599
        self.assertEqual(self.coordinator.summary('alice',self.cam,old.RID,lambda:None)['status'],'RESULT')
        with self.assertRaisesRegex(PrototypeFailure,'BUSY'):self.coordinator.run('alice',self.cam,'1','2','b'*32,lambda:None)
        self.now[0]+=1
        with self.assertRaises(PrototypeFailure):self.coordinator.summary('alice',self.cam,old.RID,lambda:None)
        with patch.object(self.coordinator,'_verify_file',side_effect=self.stub):self.coordinator.run('alice',self.cam,'1','2','b'*32,lambda:None)
        self.coordinator.release('alice',self.cam,'b'*32,lambda:None)
        self.assertIsNone(self.coordinator._record)
    def test_handoff_failure_no_skip_or_false_success(self):
        original=self.stub
        def fail(*args):
            if args[1].item_id=='r030':raise ProviderFailure('source-changed')
            original(*args)
        with patch.object(self,'stub',side_effect=fail):result=self.run_plan(60)
        self.assertEqual(result['status'],'SOURCE_UNAVAILABLE')
        self.assertEqual(self.visited,['r'+str(i).zfill(3) for i in range(30)])
        self.assertEqual(len(result['files']),30)
        self.assertIn('stopped',result['reason'])
    def test_cancellation_during_handoff_cleanup(self):
        original=self.stub
        def cancel(*args):
            if args[1].item_id=='r030':
                self.coordinator.cancel_owned('alice',self.cam,old.RID,lambda:None)
                args[-2]()
            original(*args)
        with patch.object(self,'stub',side_effect=cancel):result=self.run_plan(60)
        self.assertEqual(result['status'],'CANCELLED');self.assertEqual(len(result['files']),30)
        self.assertIsNone(self.coordinator._active);self.assertTrue(self.coordinator._done.is_set())
    def test_caps_fail_closed(self):
        with patch.object(r,'FILE_CAP',1):result=self.run_plan(1)
        self.assertEqual(result['status'],'LIMIT');self.assertEqual(result['files'],[])
        self.coordinator.release('alice',self.cam,old.RID,lambda:None)
        with patch.object(r,'STORE_CAP',1):result=self.run_plan(1)
        self.assertEqual(result['status'],'LIMIT');self.assertEqual(result['files'],[])
        self.coordinator.release('alice',self.cam,old.RID,lambda:None)
        with patch.object(w,'MAX_NALS',1):result=self.run_plan(1)
        self.assertEqual(result['status'],'LIMIT')
        self.coordinator.release('alice',self.cam,old.RID,lambda:None)
        with patch.object(r,'SUMMARY_CAP',600):result=self.run_plan(1)
        self.assertEqual(result['status'],'LIMIT');self.assertEqual(result['files'],[])
    def test_corrupt_evidence_not_stored(self):
        original=self.stub
        def corrupt(*args):
            original(*args);args[-1]['result']['objects'][0]['sha256']='0'*64
        with patch.object(self,'stub',side_effect=corrupt):result=self.run_plan(1)
        self.assertEqual(result['status'],'EXECUTION_FAILED');self.assertEqual(result['files'],[])
        self.assertEqual(self.coordinator._record['bundle'].bytes,0)
    def test_autonomous_expiry_frees_memory(self):
        self.coordinator._clock=time.monotonic
        with patch.object(w,'TTL',.03):result=self.run_plan(1)
        record=self.coordinator._record
        for _ in range(30):
            if self.coordinator._record is None:break
            time.sleep(.01)
        self.assertIsNone(self.coordinator._record)
        self.assertEqual(record['bundle'].bytes,0);self.assertIsNone(record['summary'])
        record['timer'].join(1)
        self.assertFalse(record['timer'].is_alive())

    def test_real_existing_grant_facade_owner_camera_and_session(self):
        import sys
        sys.path.insert(0,str(old.ROOT/'.local/runtime/hv2-ui3b-f2'))
        from operations import Access
        from bridge import Denied
        class Auth:
            valid=True
            def profile(auth,cookie):
                if not auth.valid:raise Denied(401)
                return {'username':cookie.split('=',1)[1].split('.')[0],'role':'viewer','allowed_cameras':['front']},None
        auth=Auth();access=Access(auth,lambda:(200,self.service.camera_bindings()))
        ops=w.WindowOperations(access,self.coordinator,self.service._hv2_camera)
        self.row()
        with patch.object(self.coordinator,'_verify_file',side_effect=self.stub):
            result,_=ops.verify('frigate_token=alice.session.signature','front','11','12')
        rid=result['request_id'];ref=result['files'][0]['evidence']['id']
        self.assertEqual(ops.summary('frigate_token=alice.session.signature','front',rid)[0],result)
        self.assertTrue(ops.evidence('frigate_token=alice.session.signature','front',rid,ref,0,1)[0]['data'])
        for method,args in ((ops.summary,()),(ops.evidence,(ref,0,1)),(ops.release,()),(ops.cancel,())):
            with self.assertRaisesRegex(PrototypeFailure,'UNAUTHORIZED'):
                method('frigate_token=mallory.session.signature','front',rid,*args)
        with self.assertRaises(Denied):ops.summary('frigate_token=alice.session.signature','other',rid)
        auth.valid=False
        with self.assertRaises(Denied):ops.evidence('frigate_token=alice.session.signature','front',rid,ref,0,1)
        auth.valid=True;ops.release('frigate_token=alice.session.signature','front',rid)
        self.assertIsNone(self.coordinator._record)

    def test_running_cancel_is_owner_bound_and_thread_exits(self):
        self.row();ready=threading.Event();results=[]
        def blocked(*args):
            ready.set()
            while True:args[-2]();time.sleep(.001)
        with patch.object(self.coordinator,'_verify_file',side_effect=blocked):
            thread=threading.Thread(target=lambda:results.append(self.coordinator.run('alice',self.cam,'11','12',old.RID,lambda:None)))
            thread.start()
            try:
                self.assertTrue(ready.wait(1))
                self.assertEqual(self.coordinator.summary('alice',self.cam,old.RID,lambda:None)['state'],'RUNNING')
                with self.assertRaises(PrototypeFailure):self.coordinator.cancel_owned('mallory',self.cam,old.RID,lambda:None)
                self.coordinator.cancel_owned('alice',self.cam,old.RID,lambda:None)
            finally:thread.join(2)
        self.assertFalse(thread.is_alive());self.assertEqual(results[0]['status'],'CANCELLED')
        self.coordinator.release('alice',self.cam,old.RID,lambda:None)

    def test_memory_failure_returns_bounded_error_and_releases_slot(self):
        with patch.object(self,'stub',side_effect=MemoryError):result=self.run_plan(1)
        self.assertEqual(result['status'],'LIMIT');self.assertIsNone(self.coordinator._record)

    def test_private_pages_keep_accepted_inventory_shapes(self):
        from nanexus_trust.historical_v2.window_inventory import window_inventory
        from nanexus_trust.historical_v2.provider import Item
        from nanexus_trust.historical_v2 import contracts as a
        items=tuple(Item(str(i),'g',self.cam,a.Interval(str(i),str(i+1)), 'host-metadata-unix-seconds',None,False,'h264','mp4-avc1',a.Quality.UNKNOWN,None,None) for i in range(180))
        plan=window_inventory('inventory','cut','window-test',self.cam,a.Interval('0','180'),a.Quality.SATISFIED,a.Quality.SATISFIED,items,(),(),tuple(str(i) for i in range(180)),None,True)
        self.assertEqual([len(page.items) for page in plan.pages],[128,52])
        for page in plan.pages:self.assertEqual(wire.loads(wire.dumps(page)),page)
        self.assertEqual(plan.items,items);self.assertEqual(len(plan.ambiguous_refs),180)

    def test_missing_duplicate_and_extra_objects_fail_closed(self):
        original=self.stub
        for kind in ('missing','duplicate','extra'):
            def corrupt(*args):
                original(*args);objects=args[-1]['result']['objects']
                if kind=='missing':objects.pop()
                elif kind=='duplicate':objects.append(dict(objects[0]))
                else:objects.append(dict(objects[0],id='orphan'))
            with patch.object(self,'stub',side_effect=corrupt):result=self.run_plan(1)
            self.assertEqual(result['status'],'EXECUTION_FAILED');self.assertFalse(result['files'])
            self.coordinator.release('alice',self.cam,old.RID,lambda:None)

    def test_byte_limit_and_new_summary_is_not_a_wire_result(self):
        with patch.object(w,'MAX_BYTES',1):result=self.run_plan(1)
        self.assertEqual(result['status'],'LIMIT');self.assertEqual(result['files'],[])
        with self.assertRaises(ValueError):wire.loads(c.canonical(result))

    def test_257_records_refused_before_any_file_attempt(self):
        for i in range(257):self.row('r'+str(i).zfill(3),i*5,(i+1)*5)
        with patch.object(self.coordinator,'_verify_file',side_effect=AssertionError('overflow must not verify')):
            result=self.coordinator.run('alice',self.cam,'1','1800',old.RID,lambda:None)
        self.assertEqual(result['status'],'LIMIT');self.assertFalse(result['files'])

    def test_full_chunk_bound_and_hash(self):
        bundle=r.Bundle();payload=b'x'*((1<<20)+1);bundle._objects['unit-bytes']=payload
        first=bundle.chunk('unit-bytes',0,1<<20);last=bundle.chunk('unit-bytes',1<<20,1<<20)
        self.assertEqual(first['bytes'],1<<20);self.assertFalse(first['end']);self.assertTrue(last['end'])
        self.assertEqual(base64.b64decode(first['data'])+base64.b64decode(last['data']),payload)
        self.assertEqual(first['sha256'],hashlib.sha256(payload).hexdigest())

    def test_unexpected_required_failure_is_bounded_and_no_recovery(self):
        with patch.object(self,'stub',side_effect=KeyError('private-path-must-not-leak')):result=self.run_plan(1)
        self.assertEqual(result['status'],'EXECUTION_FAILED');self.assertIsNone(self.coordinator._record)
        self.assertNotIn('private-path',json.dumps(result))

    def test_core_and_operation_deadlines_do_not_claim_success(self):
        with patch.object(self.coordinator,'core_seconds',0):result=self.run_plan(1)
        self.assertEqual(result['status'],'LIMIT');self.assertFalse(self.visited)
        self.coordinator.release('alice',self.cam,old.RID,lambda:None)
        original=self.stub
        def late(*args):original(*args);self.now[0]+=616
        with patch.object(self,'stub',side_effect=late):result=self.run_plan(1)
        self.assertEqual(result['status'],'LIMIT');self.assertIn('deadline',result['reason'])

    def test_negative_counts_preserve_genuine_synthetic_raw_n(self):
        def negative(*args):synthetic(args[-1],args[1],args[4],negative=True)
        with patch.object(self,'stub',side_effect=negative):result=self.run_plan(1)
        counts=result['files'][0]['finalized_counts']
        self.assertEqual(counts.get('INTEGRITY_NEGATIVE'),1);self.assertFalse(counts.get('VERIFIED_SIGNING'))

    def test_summary_includes_continuation_observations(self):
        def many(*args):
            item=args[1];request=args[4];entry=args[-1]
            fixture=compositions.CompositionTests();fixture.setUp()
            sources=[dict(token=item.item_id+':'+str(i),bytes=5,sha256='a'*64) for i in range(130)]
            spec=replace(fixture.spec,request=request.request_id,camera=request.camera.native_key,binding=request.camera.binding_generation,
                         source=item.item_id,generation=item.generation,nal_count=130,source_bytes=650)
            spec=replace(spec,run=c.run_identity(request,spec,sources))
            updates=[]
            for n in range(130):
                final=n==129
                items=[dict(fixture.updates[1]['items'][0],received_ordinal=i,source_token_hex=token_hex(sources[i]['token'])) for i in range(130)] if final else []
                updates.append(dict(fixture.updates[0],run_id=spec.run,attempt=spec.attempt,consumed=n+1,generating_feed_ordinal=n,
                                    fresh_report=final,report_generation=1 if final else 0,items=items,retired_prefix_ordinal=129 if final else None))
            updates.append(dict(updates[-1],phase='finish',fresh_report=False,items=[],stop=4))
            raws=tuple(c.canonical(u) for u in updates)
            composed=c.compose(request,spec,sources,raws);c.validate(composed,request,spec,sources,raws)
            page=wire.dumps(composed.page)
            entry.update(status='RESULT',input={'nal_occurrences':130,'annex_b_bytes':650},execution={'synthetic':True},
                         result={'page':json.loads(page),'sha256':c.sha(page),'size':len(page),'source_ledger':sources,'original_file_sha256':'a'*64,
                          'objects':[dict(id=k,sha256=c.sha(v),size=len(v),encoding='base64',data=base64.b64encode(v).decode()) for k,v in composed.objects]})
        with patch.object(self,'stub',side_effect=many):result=self.run_plan(1)
        self.assertEqual(result['status'],'RESULT');self.assertEqual(result['files'][0]['finalized_counts']['VERIFIED_SIGNING'],130)
        ref=result['files'][0]['evidence']['id']
        def fetch(identity):return base64.b64decode(self.coordinator.evidence('alice',self.cam,old.RID,identity,0,1<<20,lambda:None)['data'])
        metadata=json.loads(fetch(ref));page=wire.loads(fetch(metadata['file']['result']['page_ref']))
        self.assertEqual(len(page.observations),128)
        self.assertEqual(len(json.loads(fetch(page.next_cursor))['observations']),2)

    def test_request_bound_default_and_window_limit(self):
        with self.assertRaises(PrototypeFailure):self.coordinator.run('alice',self.cam,'0','1801',old.RID,lambda:None)
        self.assertIsNone(self.coordinator._record)
        with self.assertRaises(ValueError):list(w.windows(257))
        with self.assertRaises(TypeError):w.WindowCoordinator(old.Harness.profile(self))


class WindowApiTests(api_tests.ApiHarness):
    def test_large_bounded_api_observation_and_legacy_limit(self):
        for i in range(180):self.row(f'{i*10}.0-aaaaaa',i*10,(i+1)*10)
        self.coordinator.close()
        self.coordinator=w.WindowCoordinator(DirectApiProfile(**vars(self.coordinator.profile)))
        self.addCleanup(self.coordinator.close)
        camera=self.service._hv2_camera(self.camera,self.guard)
        visits=[]
        def stub(provider,item,bounds,cancel,request,*args):
            visits.append(item.item_id);synthetic(args[-1],item,request)
        with self.http(),patch.object(self.coordinator,'_verify_file',side_effect=stub):
            result=self.coordinator.run('alice',camera,'1','1800',old.RID,lambda:None)
        self.assertEqual(result['status'],'RESULT');self.assertEqual(len(visits),180)
        self.assertEqual(len(result['windows']),7)
        self.assertEqual(len(self.calls),2)
        self.assertIn('window-experimental',result['catalog']['capability'])
