"""Synthetic accepted authority, actual loopback HTTP and actual A composition."""
import http.client
import json
import sqlite3
from datetime import datetime, timezone
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
import test_historical_v2_prototype as old
import test_historical_v2_windows as fixtures
from nanexus_trust.historical_v2.prototype import DirectCapturedProfile,PrototypeFailure
from nanexus_trust.historical_v2.windows import WindowCoordinator
from nanexus_trust.historical_v2 import contracts as a,wire
from nanexus_trust.companion.window_http import WindowHTTP,Denied,catalog_recording_dates,handler,finding,install

BRIDGE=old.ROOT/'.local/runtime/hv2-ui3b-f2'
if not BRIDGE.is_dir():raise unittest.SkipTest('accepted local UI-3B bridge required')
sys.path.insert(0,str(BRIDGE))
from bridge import Server,Denied as NativeDenied
from operations import Access,Handler,Operations

class Auth:
    def __init__(self):self.valid={'alice','bob'};self.allowed={'alice':['front'],'bob':['front']}
    def profile(self,cookie):
        who=cookie.split('=',1)[1].split('.')[0]
        if who not in self.valid:raise NativeDenied(401)
        return {'username':who,'role':'viewer','allowed_cameras':self.allowed[who]},None

class HTTPTests(old.Harness):
    def profile(self):return DirectCapturedProfile(**vars(super().profile()))
    def setUp(self):
        super().setUp();self.coordinator.close();self.row('r0',0,10);self.row('r1',10,20);self.row('r2',20,30)
        self.coordinator=WindowCoordinator(self.profile());self.auth=Auth()
        self.access=Access(self.auth,lambda:(200,self.service.camera_bindings()))
        self.ops=WindowHTTP(self.access,self.coordinator,self.service._hv2_camera)
        self.addCleanup(self.ops.close);self.data={'camera':'front','start':'1','end':'29'}
        self.enter=threading.Event();self.gate=threading.Event();self.mode=None;self.visited=[]
        self.patch=patch.object(self.coordinator,'_verify_file',side_effect=self.verify);self.patch.start();self.addCleanup(self.patch.stop)
    def verify(self,provider,item,bounds,cancel,request,camera,rid,permission,event,deadline,check,entry):
        check();self.visited.append(item.item_id)
        if item.item_id=='r1' or (self.mode=='first' and item.item_id=='r0'):
            self.enter.set()
            while not self.gate.wait(.005):check()
            if self.mode=='fail':raise RuntimeError('synthetic operational failure')
        check();fixtures.synthetic(entry,item,request,negative=self.mode=='negative')
    def cookie(self,who='alice'):return 'frigate_token='+who+'.session.signature'
    def submit(self):
        value,_=self.ops.submit(self.cookie(),self.data);self.assertTrue(self.enter.wait(2));return value['request_id']
    def finish(self):
        self.gate.set();self.ops.slot['thread'].join(3);self.assertFalse(self.ops.slot['running'])
    def denied(self,code,fn,*args):
        try:fn(*args)
        except (Denied,NativeDenied,PrototypeFailure) as e:self.assertEqual(getattr(e,'status',None),code)
        else:self.fail('admitted forbidden action')
    def test_pending_prefix_bounded_completion_release(self):
        rid=self.submit();progress,_=self.ops.observe(self.cookie(),rid)
        self.assertEqual([f['state'] for f in progress['files']],['COMPLETED','PENDING','PENDING'])
        self.assertEqual(progress['files'][0]['finding'],'AMBER');self.assertLess(len(json.dumps(progress)),256<<10)
        self.assertNotIn('query_coverage',progress)
        self.denied(409,self.ops.submit,self.cookie(),self.data)
        self.finish();result,_=self.ops.observe(self.cookie(),rid,True)
        self.assertEqual(result['state'],'COMPLETE');self.assertTrue(result['evidence_available'])
        self.assertEqual([f['finding'] for f in result['files']],['AMBER']*3)
        self.assertEqual(self.ops.observe(self.cookie(),rid,True)[0],result)
        self.ops.release(self.cookie(),rid);self.assertIsNone(self.coordinator._record)
        self.assertTrue(self.coordinator._slot.acquire(False));self.coordinator._slot.release()
        self.ops.submit(self.cookie(),self.data);self.finish()
    def test_isolation_every_endpoint_and_session_revocation(self):
        rid=self.submit()
        for method in (self.ops.observe,self.ops.cancel,self.ops.release):self.denied(403,method,self.cookie('bob'),rid)
        self.auth.allowed['alice']=[];self.denied(403,self.ops.observe,self.cookie(),rid)
        self.auth.allowed['alice']=['front'];self.auth.valid.remove('alice');self.denied(401,self.ops.observe,self.cookie(),rid)
        self.finish();self.assertNotEqual(self.ops.slot['state'],'COMPLETE')
    def test_cancel_retains_prefix_and_pending(self):
        rid=self.submit();self.ops.cancel(self.cookie(),rid);self.finish()
        result,_=self.ops.observe(self.cookie(),rid,True)
        self.assertEqual(result['state'],'CANCELLED');self.assertEqual([f['state'] for f in result['files']],['COMPLETED','PENDING','PENDING'])
        self.assertEqual(self.visited,['r0','r1']);self.ops.release(self.cookie(),rid)
    def test_failure_prefix_red_and_reuse(self):
        self.mode='negative';rid=self.submit();self.assertEqual(self.ops.observe(self.cookie(),rid)[0]['files'][0]['finding'],'RED')
        self.mode='fail';self.finish();result,_=self.ops.observe(self.cookie(),rid,True)
        self.assertNotEqual(result['state'],'COMPLETE');self.assertEqual(result['files'][0]['finding'],'RED')
        self.assertEqual(result['files'][2]['state'],'PENDING');self.assertEqual(len(self.visited),2)
        self.ops.release(self.cookie(),rid)
    def test_shutdown_cancels_owned_worker_and_frees_slot(self):
        self.submit();thread=self.ops.slot['thread'];self.ops.close()
        self.assertFalse(thread.is_alive());self.assertIsNone(self.ops.slot)
        self.assertIsNone(self.coordinator._active);self.assertTrue(self.coordinator._done.is_set())
    def test_initial_plan_all_pending_before_first_completion(self):
        self.mode='first';rid=self.submit();value,_=self.ops.observe(self.cookie(),rid)
        self.assertEqual([f['state'] for f in value['files']],['PENDING']*3)
        self.finish()
    def test_31_unique_blocks_across_overlap(self):
        self.ops.close()
        for i in range(3,31):self.row('r'+str(i),i*10,(i+1)*10)
        self.coordinator=WindowCoordinator(self.profile());self.ops=WindowHTTP(self.access,self.coordinator,self.service._hv2_camera)
        self.addCleanup(self.ops.close);self.patch.stop();self.patch=patch.object(self.coordinator,'_verify_file',side_effect=self.verify);self.patch.start();self.addCleanup(self.patch.stop)
        self.data['end']='310';rid=self.submit();self.finish();value,_=self.ops.observe(self.cookie(),rid,True)
        self.assertEqual(len(value['files']),31);self.assertEqual(len(set(self.visited)),31)
        self.assertEqual([f['position'] for f in value['files']],list(range(31)))
    def test_captured_182_classification_without_media(self):
        path=old.ROOT/'.local/governance/ai-dlc/evidence/m1-hv2-d-planning/hr6b-artifacts/files.json'
        if not path.is_file():self.skipTest('preserved H-R6B diagnostics unavailable')
        files=json.loads(path.read_text());self.assertEqual(len(files),182)
        for f in files:
            self.assertEqual(finding({},dict(finalized_counts=f['counts'],unresolved=f['unresolved'],unprocessed=0,missing_evidence=False)),'AMBER')
    def test_strict_green_requires_complete_typed_scope(self):
        from dataclasses import replace
        from nanexus_trust.historical_v2 import contracts as a,wire,wire
        import test_historical_v2_composition as compositions
        f=compositions.CompositionTests();f.setUp();page=f.call().page
        row=dict(finalized_counts={'VERIFIED_SIGNING':1},unresolved=0,unprocessed=0,missing_evidence=False)
        entry={'result':{'page':json.loads(wire.dumps(page)),'objects':[]}}
        self.assertEqual(finding(entry,row),'AMBER')
        complete=replace(page,lifecycle=a.Lifecycle.COMPLETE,progress=replace(page.progress,phase=a.Lifecycle.COMPLETE,finalized=page.progress.scanned),unresolved_refs=(),unprocessed_refs=(),output_complete=True)
        entry['result']['page']=json.loads(wire.dumps(complete));self.assertEqual(finding(entry,row),'GREEN')
        self.assertEqual(finding(entry,row|{'missing_evidence':True}),'AMBER')
    def test_video_scope_rule_and_structural_failure(self):
        from test_historical_v2_video_signing import emit, mutate, finding as scoped_finding
        from nanexus_trust.historical_v2.video_signing import EvidenceError
        entry,row,_,_=emit()
        self.assertEqual(scoped_finding(entry,row),'GREEN')
        self.assertEqual(row['finalized_counts']['INDETERMINATE'],2)
        exceptional,exceptional_row,_,_=emit(exceptional=True)
        self.assertEqual(scoped_finding(exceptional,exceptional_row),'AMBER')
        with self.assertRaises(EvidenceError):
            finding(mutate(entry,'trust',lambda t:t.update(sha256='0'*64)),row)

    def test_expiry_and_bad_input(self):
        self.denied(413,self.ops.submit,self.cookie(),self.data|{'end':'1802'})
        self.denied(403,self.ops.submit,self.cookie(),self.data|{'camera':'unknown'})
        rid=self.submit();self.finish();self.ops.slot['expires']=0
        self.denied(404,self.ops.observe,self.cookie(),rid);self.assertIsNone(self.coordinator._record)
    def test_http_security_prompt_submit_and_poll(self):
        server=Server(('127.0.0.1',0),Handler);server.expected_host='127.0.0.1:'+str(server.server_port);server.expected_origin='https://test.invalid';server.window_operations=self.ops
        del server.window_operations;self.coordinator._observer=None
        self.ops=install(server,self.access,self.service,self.coordinator)
        self.addCleanup(self.ops.close)
        server.operations=Operations(self.access,lambda *args:(500,{}));server.bridge=self.access
        thread=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.01},daemon=True);thread.start()
        def request(method,path,body=None,who='alice',csrf=True):
            connection=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=2)
            headers={'Host':server.expected_host,'Cookie':self.cookie(who)}
            if body is not None:headers.update({'Content-Type':'application/json','Origin':server.expected_origin,'X-Nanexus-Request':'1' if csrf else '0'})
            connection.request(method,path,json.dumps(body) if body is not None else None,headers);response=connection.getresponse();code=response.status;raw=response.read();connection.close();return code,json.loads(raw)
        try:
            now=time.monotonic();code,value=request('POST','/window/verify',self.data);self.assertEqual(code,202);self.assertLess(time.monotonic()-now,1)
            rid=value['request_id'];self.assertTrue(self.enter.wait(2))
            self.assertEqual(request('GET','/window/progress?request_id='+rid)[0],200)
            self.assertEqual(request('GET','/window/result?request_id='+rid,who='bob')[0],403)
            self.assertEqual(request('POST','/window/cancel',{'request_id':rid},csrf=False)[0],403)
            self.assertEqual(request('GET','/window/result?request_id='+rid)[0],409)
            self.finish();self.assertEqual(request('GET','/window/result?request_id='+rid)[1]['state'],'COMPLETE')
            self.assertEqual(request('POST','/window/release',{'request_id':rid})[0],200)
        finally:server.shutdown();server.server_close();thread.join(1)

    def test_out_of_order_batch_generation_and_duplicate(self):
        self.mode='first';rid=self.submit();record=self.ops.slot
        # Existing real authority/thread lifecycle is paused before any final result.
        request=a.Request('synthetic',record['admitted'],a.Interval('1','29'),'host-metadata-unix-seconds')
        entries=[]
        for i in (2,0,1):
            generation=record['generations']['r'+str(i)];item=type('Item',(),{'item_id':'r'+str(i),'generation':generation})()
            entry={'recording':item.item_id,'generation':generation,'metadata_interval':{'start':str(i*10),'end':str((i+1)*10)}}
            fixtures.synthetic(entry,item,request);bundle=fixtures.r.Bundle();bundle.add_file(entry,request,'synthetic');entries.append((entry,bundle.files[0]))
        entry,row=entries[0]
        with self.assertRaises(ValueError):self.ops._publish('file',entry|{'generation':'wrong'},row)
        self.ops._publish('file',entry,row)
        self.assertEqual([f['state'] for f in record['files']],['PENDING','PENDING','COMPLETED'])
        with self.assertRaises(ValueError):self.ops._publish('file',entry,row)
        for entry,row in entries[1:]:self.ops._publish('file',entry,row)
        self.assertEqual([f['position'] for f in record['files']],[0,1,2])
        self.assertTrue(all(f['state']=='COMPLETED' for f in record['files']))
        self.ops.cancel(self.cookie(),rid);self.finish()
        self.assertNotEqual(record['state'],'COMPLETE')

    def test_independent_profile_and_public_schema_unchanged(self):
        rid=self.submit();self.finish()
        summary=self.coordinator.summary('alice',self.ops.slot['admitted'],rid,lambda:None)
        self.assertEqual(summary['profile'],'direct-independent-window-experimental-v1')
        value,_=self.ops.observe(self.cookie(),rid)
        self.assertEqual(value['schema'],'nanexus-hv2-window-progress/1')
        self.assertNotIn('generations',value)
        self.assertTrue(all('generation' not in f for f in value['files']))

    def test_rolling_coordinator_to_companion_delayed_batch(self):
        self._rolling_coordinator_to_companion()

    def test_candidate_coordinator_to_companion_without_upstream_reference(self):
        self._rolling_coordinator_to_companion(candidate=True)

    def _rolling_coordinator_to_companion(self,candidate=False):
        from contextlib import contextmanager
        from dataclasses import asdict,replace
        from types import SimpleNamespace
        from nanexus_trust.historical_v2.continuity_windows import RollingWindowCoordinator
        from nanexus_trust.historical_v2.continuity import Original,Plan,Rolling
        from nanexus_trust.historical_v2.direct_media import DirectInput
        from nanexus_trust.historical_v2.composition import canonical
        from nanexus_trust.historical_v2.worker_protocol import sha
        from test_historical_v2_continuity import setup,feed,finish
        self.ops.close();self.patch.stop();loaded={};selections=[]
        def resolve(request,owner,items,snapshot,trust):
            self.assertEqual(owner,'alice');selections.append([i.item_id for i in items]);originals=[];combined=b''
            for i,item in enumerate(items):
                n=setup(1,7)[3][0][1][0];n.token=item.item_id+':p0:n0';data=n.annex_b;combined+=data
                original=Original(item.item_id,item.generation,sha(b'original'),1,sha(data));originals.append(original)
                loaded[item.item_id]=(DirectInput(item.item_id,item.generation,100,original.sha256),(n,))
            proof=canonical({'schema':'qualified-source-occurrence-chain/1','originals':[asdict(o) for o in originals],'source_annex_b_sha256':sha(combined),'source_nal_start':0})
            if candidate:
                sources=[]
                for item in items:
                    receipt,nals=loaded[item.item_id]
                    facts=[{'token':n.token,'packet':n.packet,'nal':n.nal_in_packet,'length_offset':n.length_offset,'payload_offset':n.payload_offset,'payload_bytes':n.payload_bytes,'payload_sha256':n.payload_sha256,'pts':n.pts,'dts':n.dts,'duration':n.duration,'time_base':str(n.time_base)} for n in nals]
                    sources.append({'path':item.item_id+'.mp4','start':item.metadata_interval.start,'end':item.metadata_interval.end,'occurrences_sha256':sha(canonical(facts))})
                proof=canonical({'schema':'candidate-rolling-input/1','policy':'catalog-start-id-packet-nal/1','originals':[asdict(o) for o in originals],'candidate_annex_b_sha256':sha(combined),
                    'binding':{'request_sha256':sha(wire.dumps(request)),'owner_sha256':sha(owner.encode()),'camera':request.camera.native_key,'binding_generation':request.camera.binding_generation,'snapshot_sha256':snapshot.sha256,'provider_profile':self.profile().provider_profile},'sources':sources})
            return Plan(tuple(originals),trust,proof,sha(proof))
        coordinator=RollingWindowCoordinator(self.profile(),resolve)
        self.coordinator=coordinator;self.ops=WindowHTTP(self.access,coordinator,self.service._hv2_camera);self.addCleanup(self.ops.close)
        @contextmanager
        def source(provider,item,*args):
            receipt,nals=loaded[item.item_id];yield receipt,iter(nals)
        def native(exe,plan,request,owner,attempt,trust,load,publish,check,**kwargs):
            ledger=Rolling(plan,request,replace(plan.attempt(request,owner,attempt),trust_bytes=len(trust)),publish)
            for i,original in enumerate(plan.originals):
                if i==2:
                    self.enter.set()
                    while not self.gate.wait(.005):check()
                receipt,nals,metadata=load(i,check);ledger.register(original,receipt,nals,metadata)
                statuses=('P','PP','...')[i]
                ledger.accept(feed(ledger,statuses,'S'*len(statuses)));ledger.commit_source(i)
            ledger.accept(finish(ledger));ledger.finish()
            return SimpleNamespace(state='EXITED_SUCCESSFULLY',error=None,pid=None,reaped=True,consumed=3)
        with patch.object(coordinator,'_input',source),patch('nanexus_trust.historical_v2.continuity_windows.run_context',side_effect=native) as worker:
            rid=self.submit();pending,_=self.ops.observe(self.cookie(),rid)
            self.assertEqual([f['state'] for f in pending['files']],['PENDING']*3)
            self.finish();final,_=self.ops.observe(self.cookie(),rid,True)
            self.assertEqual(final['state'],'COMPLETE');self.assertEqual(worker.call_count,1)
            self.assertEqual([f['finding'] for f in final['files']],['AMBER']*3)
            self.assertTrue(all(f['state']=='COMPLETED' for f in final['files']))
            self.assertEqual(selections,[['r0','r1','r2']])
            summary=coordinator.summary('alice',self.ops.slot['admitted'],rid,lambda:None)
            self.assertEqual(summary['profile'],coordinator.execution_profile)
            self.assertEqual(summary['continuity'],'UNKNOWN');self.assertEqual(summary['origin'],'HOLD')
            self.ops.release(self.cookie(),rid);self.assertIsNone(coordinator._record)
    def test_catalog_gaps_stay_beside_unchanged_files(self):
        rid=self.submit();progress,_=self.ops.observe(self.cookie(),rid)
        self.assertEqual(progress['schema'],'nanexus-hv2-window-progress/1')
        self.assertEqual(progress['coverage'],'UNKNOWN')
        self.assertNotIn('query_coverage',progress)
        self.assertEqual(progress['catalog_gaps_state'],'available')
        self.assertEqual(progress['catalog_gaps'],[])
        self.assertEqual([f['recording'] for f in progress['files']],['r0','r1','r2'])
        self.assertTrue(all(f.get('type')!='GAP' and 'recording' in f and 'finding' in f for f in progress['files']))
        self.finish();result,_=self.ops.observe(self.cookie(),rid,True)
        self.assertEqual(result['catalog_gaps'],progress['catalog_gaps'])
        self.assertEqual(result['catalog_gaps_state'],'available')
        self.assertEqual([f['finding'] for f in result['files']],['AMBER']*3)
        self.assertEqual(result['coverage'],'UNKNOWN')
        self.assertNotIn('query_coverage',result)
        self.ops.release(self.cookie(),rid)
    def test_catalog_gap_failure_does_not_publish_empty_coverage(self):
        with patch('nanexus_trust.companion.window_http.project_catalog_gaps',side_effect=RuntimeError('projection')):
            rid=self.submit();self.finish();result,_=self.ops.observe(self.cookie(),rid,True)
        self.assertIsNone(result['catalog_gaps']);self.assertEqual(result['catalog_gaps_state'],'unavailable')
        self.assertEqual([f['finding'] for f in result['files']],['AMBER']*3)
        self.assertEqual(result['coverage'],'UNKNOWN')
        self.assertTrue(all('finding' in f and 'recording' in f for f in result['files']))
        self.ops.release(self.cookie(),rid)

    def await_slot(self,expected):
        for _ in range(100):
            if self.ops.slot is expected:return
            time.sleep(.02)
        self.fail('slot did not reach the expected identity')

    def reset_gate(self):
        self.enter.clear();self.gate.clear();self.visited.clear();self.mode=None

    def test_completion_stays_readable_then_releases_without_browser(self):
        import nanexus_trust.companion.window_http as wh
        with patch.object(wh,'CLEANUP_GRACE',.25):
            rid=self.submit();self.finish();record=self.ops.slot
            result,_=self.ops.observe(self.cookie(),rid,True)
            self.assertEqual(result['state'],'COMPLETE');self.assertTrue(result['evidence_available'])
            self.assertIs(self.ops.slot,record)
            self.await_slot(None)
            self.denied(404,self.ops.observe,self.cookie(),rid)
            again,_=self.ops.release(self.cookie(),rid)
            self.assertEqual(again,dict(request_id=rid,released=True))
            self.reset_gate();new=self.submit()
            self.assertNotEqual(new,rid);self.assertTrue(self.ops.slot['running'])
            self.finish()

    def test_cancel_without_release_frees_the_slot(self):
        import nanexus_trust.companion.window_http as wh
        with patch.object(wh,'CLEANUP_GRACE',.25):
            rid=self.submit();self.ops.cancel(self.cookie(),rid);self.finish()
            result,_=self.ops.observe(self.cookie(),rid,True)
            self.assertEqual(result['state'],'CANCELLED')
            self.await_slot(None)
            self.reset_gate();self.assertNotEqual(self.submit(),rid);self.finish()

    def test_failure_and_deadline_release_without_browser(self):
        import nanexus_trust.companion.window_http as wh
        with patch.object(wh,'CLEANUP_GRACE',.25):
            self.mode='fail';rid=self.submit();self.finish()
            result,_=self.ops.observe(self.cookie(),rid,True)
            self.assertEqual(result['state'],'EXECUTION_FAILED')
            self.await_slot(None)
            self.reset_gate()
            with patch.object(self.coordinator,'run',return_value={'status':'LIMIT','reason':'Operation exceeded its delivery deadline.'}):
                value,_=self.ops.submit(self.cookie(),self.data)
            limited=value['request_id'];self.ops.slot['thread'].join(2)
            self.assertFalse(self.ops.slot['thread'].is_alive())
            self.assertEqual(self.ops.observe(self.cookie(),limited,True)[0]['state'],'LIMIT')
            self.await_slot(None)

    def test_repeated_release_and_stale_cleanup_keep_the_newer_job(self):
        import nanexus_trust.companion.window_http as wh
        with patch.object(wh,'CLEANUP_GRACE',5):
            rid=self.submit();self.finish();old=self.ops.slot
            first,_=self.ops.release(self.cookie(),rid)
            second,_=self.ops.release(self.cookie(),rid)
            self.assertTrue(first['released'] and second['released']);self.assertIsNone(self.ops.slot)
            self.assertFalse(self.ops._cleanup(old))
            self.reset_gate();new=self.submit()
            self.assertTrue(self.ops.slot['running']);self.assertEqual(self.ops.slot['id'],new)
            self.assertFalse(self.ops._cleanup(old))
            self.assertEqual(len([t for t in threading.enumerate() if t.name=='historical-window-http' and t.is_alive()]),1)
            try:self.ops.submit(self.cookie(),self.data)
            except Denied as e:self.assertEqual((e.status,e.running),(409,True))
            else:self.fail('a second worker was admitted')
            self.assertEqual(self.ops.slot['id'],new)
            self.finish();self.assertFalse(self.ops.slot['running'])
            try:self.ops.submit(self.cookie(),self.data)
            except Denied as e:self.assertEqual((e.status,e.running),(409,False))
            else:self.fail('grace slot was treated as free')
            self.assertEqual(self.ops.slot['id'],new)
            self.ops.release(self.cookie(),rid)
            self.assertEqual(self.ops.slot['id'],new);self.assertFalse(self.ops.slot['running'])

    def test_verify_conflict_reports_running_on_the_wire(self):
        server=Server(('127.0.0.1',0),Handler);server.expected_host='127.0.0.1:'+str(server.server_port);server.expected_origin='https://test.invalid'
        self.coordinator._observer=None
        self.ops=install(server,self.access,self.service,self.coordinator);self.addCleanup(self.ops.close)
        server.operations=Operations(self.access,lambda *args:(500,{}));server.bridge=self.access
        thread=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.01},daemon=True);thread.start()
        def request(body):
            connection=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=2)
            connection.request('POST','/window/verify',json.dumps(body),{'Host':server.expected_host,'Cookie':self.cookie(),'Content-Type':'application/json','Origin':server.expected_origin,'X-Nanexus-Request':'1'})
            response=connection.getresponse();code=response.status;raw=response.read();connection.close();return code,json.loads(raw)
        try:
            code,value=request(self.data);self.assertEqual(code,202);self.assertTrue(self.enter.wait(2))
            code,busy=request(self.data);self.assertEqual((code,busy),(409,{'error':'window_operation_unavailable','running':True}))
            self.assertEqual(len([t for t in threading.enumerate() if t.name=='historical-window-http' and t.is_alive()]),1)
            self.finish();code,finishing=request(self.data)
            self.assertEqual((code,finishing),(409,{'error':'window_operation_unavailable','running':False}))
            self.assertEqual(len([t for t in threading.enumerate() if t.name=='historical-window-http' and t.is_alive()]),0)
        finally:server.shutdown();server.server_close();thread.join(1)

class CatalogDayTests(unittest.TestCase):
    def test_local_month_marks_only_dates_with_recordings(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'catalog.sqlite'
            db=sqlite3.connect(path)
            db.execute('CREATE TABLE recordings(id TEXT, camera TEXT, start_time REAL, end_time REAL)')
            # 2026-10-24 15:04:53Z through 15:19:53Z is 11:04–11:19 at UTC-4.
            db.execute('INSERT INTO recordings VALUES (?,?,?,?)',('a','hv2_corpus_signed',1792854293.765,1792855193.765))
            db.commit();db.close()
            marked=catalog_recording_dates(path,'hv2_corpus_signed','2026-10','UTC')
            self.assertEqual(marked['dates'],['2026-10-24'])
            self.assertEqual(marked['zone'],'UTC')
            self.assertTrue(marked['complete'])
            self.assertEqual(marked['meaning'],'catalog_recordings_present')
            # 2026-10-01 03:30Z is still September 30 in America/New_York.
            early=datetime(2026,10,1,3,30,tzinfo=timezone.utc).timestamp()
            db=sqlite3.connect(path)
            db.execute('INSERT INTO recordings VALUES (?,?,?,?)',('b','hv2_corpus_signed',early,early+60))
            db.commit();db.close()
            utc=catalog_recording_dates(path,'hv2_corpus_signed','2026-10','UTC')
            york=catalog_recording_dates(path,'hv2_corpus_signed','2026-10','America/New_York')
            self.assertIn('2026-10-01', utc['dates'])
            self.assertNotIn('2026-10-01', york['dates'])
            september=catalog_recording_dates(path,'hv2_corpus_signed','2026-09','America/New_York')
            self.assertIn('2026-09-30', september['dates'])

if __name__=='__main__':unittest.main()
