"""Dependency-driven product adapter tests: real admission/projection/Bundle, synthetic native updates."""
import json
import unittest
from dataclasses import replace
from types import SimpleNamespace
from nanexus_trust.historical_v2 import wire
from nanexus_trust.historical_v2.composition import canonical
from nanexus_trust.historical_v2.continuity_protocol import sha, ProtocolError
from nanexus_trust.historical_v2.rolling_product import ProductPlan, ProductRolling, CONFIGURATION
from nanexus_trust.historical_v2.window_results import Bundle
from nanexus_trust.companion.window_http import finding
from test_historical_v2_continuity import setup, feed, finish


def fixture(count=3,first_count=1):
    p,q,s,loaded,out,old=setup(min(count,4))
    items=[]
    for index,(receipt,ns,meta) in enumerate(loaded):
        receipt=dict(receipt);item=SimpleNamespace(item_id=receipt['item_id'],generation=receipt['generation'],
            camera=q.camera,coordinate=q.coordinate,metadata_interval=__import__('nanexus_trust.historical_v2.contracts',fromlist=['Interval']).Interval(meta['start'],meta['end']))
        prefix=sha((item.item_id+':'+item.generation+':'+receipt['sha256']).encode())
        values=[]
        for j in range(first_count if index==0 else 1):
            fields=vars(ns[0]).copy();fields.update(packet=j,token=f'{prefix}:p{j}:n0:o4:l{ns[0].payload_bytes}')
            values.append(SimpleNamespace(**fields))
        loaded[index]=(receipt,tuple(values),meta);items.append(item)
    snapshot=SimpleNamespace(sha256='a'*64,cut_id='synthetic',source_pin='pinned')
    plan=ProductPlan(q,'owner',tuple(items),snapshot,sha(b'CA'),tuple(i.item_id+'.mp4' for i in items))
    spec=replace(plan.attempt(q,'owner','attempt'),trust_bytes=2)
    bundle=Bundle()
    def publish(entry):bundle.add_file(entry,q,snapshot.sha256);out.append(entry)
    r=ProductRolling(plan,q,spec,publish)
    return plan,q,loaded,out,r,bundle


def update(r,statuses,types=None,missing=(),key_changed=False):
    u=json.loads(feed(r,statuses,types,missing,key_changed));u['configuration']=CONFIGURATION
    # This product fixture models fully qualified axes, not OK-with-missing-info.
    for item in u['items']:
        if item['state']!=1:item['axes'].update(authenticity=4,combined=2)
    return canonical(u)


def terminal(r):
    u=json.loads(finish(r));u['configuration']=CONFIGURATION
    r.accept(canonical(u));r.finish()


class ProductTests(unittest.TestCase):
    def test_later_file_revision_pagination_and_release(self):
        p,q,loaded,out,r,b=fixture(2,131)
        r.register(p.originals[0],*loaded[0])
        for i in range(131):r.accept(update(r,'.' if i<128 else 'P'*(i-127)))
        r.commit_source(0);self.assertFalse(out)
        r.register(p.originals[1],*loaded[1]);r.accept(update(r,'....'));r.commit_source(1)
        self.assertEqual(len(out),2);self.assertEqual(r.cross_file_retirements,3)
        self.assertEqual(sum(b.files[0]['finalized_counts'].values()),131)
        self.assertIsNotNone(wire.loads(canonical(out[0]['result']['page'])).next_cursor)
        self.assertEqual([finding(e,row,owner='owner',camera=q.camera,request_id=q.request_id) for e,row in zip(out,b.files)],['GREEN','GREEN'])
        self.assertFalse(r.tokens);self.assertFalse(r.admission.last);self.assertFalse(r.admission.raws);self.assertFalse(r.admission.reports)
        for f in r.files:
            for key in ('items','reports','raws','facts'):self.assertFalse(f[key])
        terminal(r);self.assertEqual(len(out),2)
        with self.assertRaises(ValueError):r.finish()
        self.assertLess(b.bytes,1<<20)

    def test_batch_delay_negative_control_missing_and_tail(self):
        p,q,loaded,out,r,b=fixture()
        for index in range(3):
            r.register(p.originals[index],*loaded[index])
            if index<2:r.accept(update(r,'P'*(index+1)))
            else:r.accept(update(r,'NUP','PSC',missing=[dict(left_received=1,right_received=2,report_generation=3)]))
            r.commit_source(index)
        self.assertEqual([e['recording'] for e in out],['file-0'])
        terminal(r)
        self.assertEqual([finding(e,row) for e,row in zip(out,b.files)],['RED','AMBER','AMBER'])
        self.assertEqual(b.files[1]['finalized_counts'],{'INDETERMINATE':1})
        self.assertTrue(b.files[1]['missing_evidence']);self.assertTrue(b.files[2]['missing_evidence'])
        self.assertEqual(b.files[2]['unresolved'],1)
        self.assertFalse(wire.loads(canonical(out[2]['result']['page'])).output_complete)

    def test_wrong_source_binding_and_native_axes_rejected(self):
        for change in ('item_id','generation','sha256'):
            p,q,ls,out,r,b=fixture(1);receipt=dict(ls[0][0]);receipt[change]='0'*64
            with self.subTest(change=change),self.assertRaises(ValueError):r.register(p.originals[0],receipt,*ls[0][1:])
        p,q,ls,out,r,b=fixture(1);r.register(p.originals[0],*ls[0])
        u=json.loads(update(r,'.'));u['items'][0]['axes']['provenance']=0
        with self.assertRaises(ValueError):r.accept(canonical(u))
        self.assertFalse(out)
        with self.assertRaises(ValueError):p.bind_request(q,'other-owner')
        with self.assertRaises(ValueError):p.bind_request(replace(q,request_id='other'),'owner')
        with self.assertRaises(ValueError):r.accept(canonical(dict(u,trust='0'*64)))

    def test_earlier_bundle_immutable_after_failure(self):
        p,q,ls,out,r,b=fixture(2)
        r.register(p.originals[0],*ls[0]);r.accept(update(r,'.'));r.commit_source(0)
        before=dict(b._objects);r.fail()
        with self.assertRaises(ValueError):r.register(p.originals[1],*ls[1])
        self.assertEqual(b._objects,before);self.assertEqual(len(b.files),1)
        identity=b.files[0]['evidence']['id'];self.assertTrue(b.chunk(identity,0,1024)['bytes'])
        b.clear();self.assertFalse(b._objects)

    def test_key_change_does_not_publish_or_revise_prior(self):
        p,q,ls,out,r,b=fixture(2)
        r.register(p.originals[0],*ls[0]);r.accept(update(r,'.'));r.commit_source(0);before=dict(b._objects)
        r.register(p.originals[1],*ls[1])
        with self.assertRaises(ValueError):r.accept(update(r,'U','S',key_changed=True))
        self.assertEqual(b._objects,before);self.assertEqual(len(out),1)

    def test_native_binding_revision_active_and_order_checks(self):
        for mutation in ('source_token_hex','revision','finalized','run_id','configuration'):
            p,q,ls,out,r,b=fixture(1);r.register(p.originals[0],*ls[0]);u=json.loads(update(r,'.'))
            if mutation=='source_token_hex':u['items'][0][mutation]=b'wrong'.hex()
            elif mutation=='revision':u['items'][0][mutation]=2
            elif mutation=='finalized':u['items'][0][mutation]=False
            else:u[mutation]='wrong'
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):r.accept(canonical(u))
            self.assertFalse(out)

    def test_selection_bound_182_without_reading_media(self):
        p,q,ls,out,r,b=fixture(1);base=p.originals[0]
        items=tuple(SimpleNamespace(**(vars(base)|dict(item_id='file-'+str(i)))) for i in range(182))
        items=tuple(sorted(items,key=lambda i:i.item_id))
        snap=SimpleNamespace(sha256='a'*64,cut_id='s',source_pin='p')
        plan=ProductPlan(q,'owner',items,snap,sha(b'CA'),tuple(str(i) for i in range(182)))
        self.assertEqual(plan.attempt(q,'owner','a').files,'182')
        with self.assertRaises(ValueError):ProductPlan(q,'owner',items+(items[0],),snap,sha(b'CA'),tuple(str(i) for i in range(183)))
        with self.assertRaises(ValueError):ProductPlan(q,'owner',items,snap,sha(b'CA'),('same',)*182)


    def test_compact_admission_global_ordinals_beyond_old_context_cap(self):
        from nanexus_trust.historical_v2.continuity_updates import Admission,decode
        p,q,ls,out,r,b=fixture(1)
        ad=Admission(r.spec,('token',),compact=True)
        ad.tokens={4100:'token'};ad.token_count=4101;ad.consumed=4100;ad.generation=4100;ad.retired=4099
        r.admission=ad
        raw=update(r,'.')
        with self.assertRaises(ValueError):decode(raw)  # Old finite reader remains closed.
        self.assertEqual(ad.add(raw)['consumed'],4101)
        self.assertFalse(ad.raws);self.assertFalse(ad.reports)
        u=json.loads(raw);u['consumed']=32769
        with self.assertRaises(ValueError):decode(canonical(u),32768)

    def test_update_cap_failure_keeps_prior_immutable_bundle(self):
        p,q,ls,out,r,b=fixture(2)
        r.register(p.originals[0],*ls[0]);r.accept(update(r,'.'));r.commit_source(0)
        before=dict(b._objects)
        r.register(p.originals[1],*ls[1]);r.admission.total=64<<20
        with self.assertRaises(ValueError):r.accept(update(r,'.'))
        self.assertEqual(b._objects,before);self.assertEqual(len(b.files),1)

# Actual owner-authorized Companion submission/poll/detail/release, with only
# media parsing and native transport scripted. No listener or native process.
import threading
import time
from contextlib import contextmanager
from unittest.mock import patch
import test_historical_v2_prototype as old
import test_historical_v2_window_http as http_tests
from nanexus_trust.historical_v2.continuity_windows import ProductRollingWindowCoordinator
from nanexus_trust.historical_v2.prototype import DirectCapturedProfile
from nanexus_trust.historical_v2.direct_media import DirectInput
from nanexus_trust.historical_v2.windows import WindowOperations
from nanexus_trust.companion.window_http import WindowHTTP,Denied

class ProductCompanionTests(old.Harness):
    def profile(self):return DirectCapturedProfile(**vars(super().profile()))
    def setUp(self):
        super().setUp();self.coordinator.close()
        for i in range(3):self.row('r'+str(i),i*10,(i+1)*10)
        self.worker=self.root/'synthetic-worker';self.worker.write_bytes(b'not an executable; native transport scripted')
        self.coordinator=ProductRollingWindowCoordinator(replace(self.profile(),worker=self.worker))
        self.auth=http_tests.Auth();self.access=http_tests.Access(self.auth,lambda:(200,self.service.camera_bindings()))
        self.ops=WindowHTTP(self.access,self.coordinator,self.service._hv2_camera);self.addCleanup(self.ops.close)
        self.details=WindowOperations(self.access,self.coordinator,self.service._hv2_camera)
        self.entered=threading.Event();self.resume=threading.Event();self.mode='normal';self.native_calls=0
        self.addCleanup(self.resume.set)
        self.hashpatch=patch('nanexus_trust.historical_v2.rolling_product.WORKER_SHA256',sha(self.worker.read_bytes()));self.hashpatch.start();self.addCleanup(self.hashpatch.stop)
        self.inputpatch=patch.object(self.coordinator,'_input',self.source);self.inputpatch.start();self.addCleanup(self.inputpatch.stop)
        self.nativepatch=patch('nanexus_trust.historical_v2.continuity_windows.run_context',self.native);self.nativepatch.start();self.addCleanup(self.nativepatch.stop)
    @contextmanager
    def source(self,provider,item,*unused):
        data=b'\0\0\0\1\x65signed-synthetic'
        receipt=DirectInput(item.item_id,item.generation,100,sha(item.item_id.encode()))
        prefix=sha((item.item_id+':'+item.generation+':'+receipt.sha256).encode())
        n=SimpleNamespace(token=f'{prefix}:p0:n0:o4:l{len(data)-4}',annex_b=data,packet=0,nal_in_packet=0,
            length_offset=0,payload_offset=4,payload_bytes=len(data)-4,payload_sha256=sha(data[4:]),pts=0,dts=0,duration=1,time_base='1/15')
        yield receipt,iter((n,))
    def native(self,executable,plan,request,owner,attempt,trust,load,publish,check,**kwargs):
        self.native_calls+=1;spec=replace(plan.attempt(request,owner,attempt),trust_bytes=len(trust));r=ProductRolling(plan,request,spec,publish)
        try:
            for i,item in enumerate(plan.originals):
                if i==2:
                    self.entered.set()
                    while not self.resume.wait(.005):check()
                    check()
                    if self.mode=='failure':raise RuntimeError('later source unavailable')
                r.register(item,*load(i,check));r.accept(update(r,('P','.P','..')[i]));r.commit_source(i)
            terminal(r)
            return SimpleNamespace(state='EXITED_SUCCESSFULLY',error=None,pid=None,reaped=True,consumed=3,metrics={'active_peak':2})
        except Exception:
            return SimpleNamespace(state='FAILED',error='synthetic stopped',pid=None,reaped=True,consumed=r.admission.consumed,metrics={'active_peak':2})
    def cookie(self,who='alice'):return 'frigate_token='+who+'.session.signature'
    def submit(self):
        result,_=self.ops.submit(self.cookie(),dict(camera='front',start='1',end='29'))
        self.assertTrue(self.entered.wait(2));return result['request_id']
    def finish_request(self):
        self.resume.set();self.ops.slot['thread'].join(3);self.assertFalse(self.ops.slot['running'])
    def test_actual_adapter_incremental_details_owner_and_release(self):
        rid=self.submit();value,_=self.ops.observe(self.cookie(),rid)
        self.assertEqual([f['state'] for f in value['files']],['COMPLETED','PENDING','PENDING'])
        row=self.coordinator._record['bundle'].files[0]
        with self.assertRaises(Exception):self.details.evidence(self.cookie(),'front',rid,row['evidence']['id'],0,1024)
        # Details retain their existing terminal-only delivery contract.
        with self.assertRaises(Exception):self.details.evidence(self.cookie('bob'),'front',rid,row['evidence']['id'],0,1024)
        with self.assertRaises(Denied):self.ops.observe(self.cookie('bob'),rid)
        self.finish_request();value,_=self.ops.observe(self.cookie(),rid,True)
        self.assertEqual(value['state'],'COMPLETE');self.assertEqual(len(value['files']),3);self.assertEqual(self.native_calls,1)
        chunk,_=self.details.evidence(self.cookie(),'front',rid,row['evidence']['id'],0,1024)
        self.assertGreater(chunk['bytes'],0)
        self.ops.release(self.cookie(),rid);self.assertIsNone(self.coordinator._record)
    def test_later_failure_preserves_scoped_evidence(self):
        self.mode='failure';rid=self.submit();before=dict(self.coordinator._record['bundle']._objects)
        self.finish_request();value,_=self.ops.observe(self.cookie(),rid,True)
        self.assertEqual(value['state'],'EXECUTION_FAILED');self.assertTrue(value['evidence_available'])
        self.assertEqual(self.coordinator._record['bundle']._objects,before)
        self.assertEqual(value['files'][0]['state'],'COMPLETED');self.assertEqual(value['files'][1]['state'],'PENDING')
    def test_cancellation_and_revocation_preserve_prior_scope(self):
        rid=self.submit();before=dict(self.coordinator._record['bundle']._objects)
        self.ops.cancel(self.cookie(),rid);self.finish_request()
        value,_=self.ops.observe(self.cookie(),rid,True);self.assertEqual(value['state'],'CANCELLED')
        self.assertTrue(value['evidence_available'])
        row=self.coordinator._record['bundle'].files[0]
        self.assertGreater(self.details.evidence(self.cookie(),'front',rid,row['evidence']['id'],0,1024)[0]['bytes'],0)
        self.assertEqual(self.coordinator._record['bundle']._objects,before)
        self.auth.valid.remove('alice')
        with self.assertRaises(Exception):self.ops.observe(self.cookie(),rid)
        self.auth.valid.add('alice');self.ops.slot['expires']=0
        with self.assertRaises(Denied):self.ops.observe(self.cookie(),rid)
        self.assertIsNone(self.coordinator._record)
