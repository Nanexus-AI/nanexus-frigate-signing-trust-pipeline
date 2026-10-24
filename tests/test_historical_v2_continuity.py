"""Synthetic protocol/finality tests; no native worker, source IO or services."""
import json
import unittest
from dataclasses import asdict,replace
from types import SimpleNamespace
from nanexus_trust.historical_v2 import contracts as a,wire
from nanexus_trust.historical_v2.composition import canonical
from nanexus_trust.historical_v2.continuity import Plan,Original,Rolling
from nanexus_trust.historical_v2.continuity_protocol import sha,encode,Decoder,ProtocolError,CONFIG_DIGEST
from nanexus_trust.historical_v2.worker_protocol import Decoder as OldDecoder
from nanexus_trust.historical_v2.window_results import Bundle
from nanexus_trust.companion.window_http import finding
from nanexus_trust.historical_v2.windows import windows
from nanexus_trust.historical_v2.continuity_worker import run_context


def setup(count=3,kind=5):
    req=a.Request('rolling-test',a.Camera('logical','camera','binding','Camera'),a.Interval('0','10'),'host-metadata-unix-seconds')
    files=[];loaded=[];allbytes=b''
    for i in range(count):
        data=b'\0\0\0\1'+bytes([kind])+b'same-payload'
        n=SimpleNamespace(token=f'original-{i}:p0:n0:o4:l13',annex_b=data,packet=0,nal_in_packet=0,length_offset=0,payload_offset=4,payload_bytes=len(data)-4,payload_sha256=sha(data[4:]),pts=0,dts=0,duration=1,time_base='1/15')
        original=Original('file-'+str(i),sha(str(i).encode()),sha(('original'+str(i)).encode()),1,sha(data));files.append(original);allbytes+=data
        loaded.append(({'item_id':original.item_id,'generation':original.generation,'sha256':original.sha256,'actual_bytes':100},(n,),{'start':str(i),'end':str(i+1)}))
    proof=canonical({'schema':'qualified-source-occurrence-chain/1','originals':[asdict(o) for o in files],'source_annex_b_sha256':sha(allbytes),'source_nal_start':0})
    plan=Plan(tuple(files),sha(b'CA'),proof,sha(proof));spec=replace(plan.attempt(req,'owner','attempt'),trust_bytes=2)
    out=[];ledger=Rolling(plan,req,spec,out.append)
    return plan,req,spec,loaded,out,ledger


def feed(ledger,statuses,types=None,missing=(),key_changed=False):
    ad=ledger.admission;n=ad.consumed+1;g=ad.generation+1;start=0 if ad.retired is None else ad.retired+1
    assert len(statuses)==n-start
    items=[];pending=False;ret=ad.retired
    for ordinal,status in zip(range(start,n),statuses):
        typ=types[ordinal-start] if types else 'P';old=ad.last.get(ordinal);pending|=status=='P';final=not pending
        state=0 if final and status=='.' and typ in 'IiPp' else 1 if final and status=='N' and typ in 'IiPp' else 3
        items.append(dict(received_ordinal=ordinal,source_token_hex=ad.tokens[ordinal].encode().hex(),nalu_type=typ,raw_status=status,
                          revision=0 if old is None else old['revision']+int((old['raw_status'],old['nalu_type'])!=(status,typ)),report_generation=g,finalized=final,state=state,
                          axes={'authenticity':2 if state==1 else 3,'provenance':3,'combined':1,'key_changed':key_changed,'raw_signing_start':None,'raw_signing_end':None}))
        if final:ret=ordinal
    raw=dict(run_id=ledger.spec.run,attempt=ledger.spec.attempt,phase='feed',trust=ledger.spec.trust_digest,configuration=CONFIG_DIGEST,stop=0,error=0,consumed=n,generating_feed_ordinal=n-1,report_generation=g,fresh_report=True,retired_prefix_ordinal=ret,items=items,missing=list(missing))
    return canonical(raw)


def finish(ledger):
    ad=ledger.admission;items=[]
    for n in range(0 if ad.retired is None else ad.retired+1,ad.consumed):
        i=dict(ad.last[n]);i.update(finalized=False,state=3);items.append(i)
    return canonical(dict(run_id=ledger.spec.run,attempt=ledger.spec.attempt,phase='finish',trust=ledger.spec.trust_digest,configuration=CONFIG_DIGEST,stop=4,error=0,consumed=ad.consumed,generating_feed_ordinal=max(0,ad.consumed-1),report_generation=ad.generation,fresh_report=False,retired_prefix_ordinal=ad.retired,items=items,missing=[]))


class RollingTests(unittest.TestCase):
    def test_delayed_third_file_releases_all_once(self):
        p,q,s,loaded,out,r=setup()
        for i,status in enumerate(('P','PP','...')):
            r.register(p.originals[i],*loaded[i]);r.accept(feed(r,status))
            self.assertEqual(out,[])
            r.commit_source(i)
            if i<2:self.assertEqual(out,[])
        self.assertEqual([x['recording'] for x in out],['file-0','file-1','file-2'])
        r.accept(finish(r));r.finish();self.assertEqual(len(out),3)
        with self.assertRaises(ProtocolError):r.finish()
        for entry in out:
            page=wire.loads(canonical(entry['result']['page']));self.assertIs(page.lifecycle,a.Lifecycle.COMPLETE);self.assertTrue(page.output_complete)
            self.assertEqual(page.progress.scanned,page.progress.denominator)
        self.assertEqual(r.admission.last[0]['revision'],1)
        self.assertEqual(r.admission.reports[3]['generating_feed_ordinal'],2)

    def test_actual_occurrences_not_payload_deduplicated(self):
        p,q,s,loaded,out,r=setup(2)
        self.assertEqual(loaded[0][1][0].annex_b,loaded[1][1][0].annex_b)
        for i in range(2):r.register(p.originals[i],*loaded[i])
        self.assertEqual(len(set(r.tokens)),2)

    def test_no_publication_before_receipt_commit(self):
        p,q,s,ls,out,r=setup(1);r.register(p.originals[0],*ls[0]);r.accept(feed(r,'.'));self.assertFalse(out)
        r.commit_source(0);self.assertEqual(len(out),1)

    def test_terminal_unresolved_is_amber_not_complete(self):
        p,q,s,ls,out,r=setup(1);r.register(p.originals[0],*ls[0]);r.accept(feed(r,'P'));r.commit_source(0);self.assertFalse(out)
        r.accept(finish(r));r.finish();bundle=Bundle();bundle.add_file(out[0],q,'snapshot')
        self.assertEqual(finding(out[0],bundle.files[0]),'AMBER');self.assertFalse(wire.loads(canonical(out[0]['result']['page'])).output_complete)
        self.assertEqual(bundle.files[0]['unresolved'],1)

    def test_control_evidence_complete_still_amber(self):
        p,q,s,ls,out,r=setup(1,7);r.register(p.originals[0],*ls[0]);r.accept(feed(r,'U','S'));r.commit_source(0)
        b=Bundle();b.add_file(out[0],q,'snapshot');self.assertEqual(finding(out[0],b.files[0]),'AMBER')
        self.assertTrue(wire.loads(canonical(out[0]['result']['page'])).output_complete);self.assertEqual(b.files[0]['finalized_counts'],{'INDETERMINATE':1})

    def test_genuine_negative_preserved_complete_red(self):
        p,q,s,ls,out,r=setup(1);r.register(p.originals[0],*ls[0]);r.accept(feed(r,'N'));r.commit_source(0)
        b=Bundle();b.add_file(out[0],q,'snapshot');self.assertEqual(finding(out[0],b.files[0]),'RED');self.assertTrue(wire.loads(canonical(out[0]['result']['page'])).output_complete)

    def test_missing_blocks_local_finality(self):
        p,q,s,ls,out,r=setup(1);r.register(p.originals[0],*ls[0]);r.accept(feed(r,'.',missing=[{'left_received':0,'right_received':None,'report_generation':1}]))
        r.commit_source(0);self.assertFalse(out);r.accept(finish(r));r.finish();self.assertFalse(wire.loads(canonical(out[0]['result']['page'])).output_complete)

    def test_bad_order_and_receipts_fail_admission(self):
        p,q,s,ls,out,r=setup(2)
        with self.assertRaises(ProtocolError):r.register(p.originals[1],*ls[1])
        bad=dict(ls[0][0],sha256='0'*64)
        with self.assertRaises(ProtocolError):r.register(p.originals[0],bad,*ls[0][1:])
        with self.assertRaises(ProtocolError):Plan(p.originals,p.trust_sha256,b'{}',sha(b'{}'))
        with self.assertRaises(ProtocolError):Plan(p.originals,p.trust_sha256,p.qualified_order,'0'*64)

    def test_duplicate_tokens_rejected(self):
        p,q,s,ls,out,r=setup(2);r.register(p.originals[0],*ls[0]);ls[1][1][0].token=ls[0][1][0].token
        with self.assertRaises(ProtocolError):r.register(p.originals[1],*ls[1])

    def test_trust_change_never_publishes(self):
        p,q,s,ls,out,r=setup(1);r.register(p.originals[0],*ls[0])
        with self.assertRaises(ProtocolError):r.accept(feed(r,'U','S',key_changed=True))
        self.assertFalse(out)

    def test_retired_revision_or_source_replay_rejected(self):
        p,q,s,ls,out,r=setup(1);r.register(p.originals[0],*ls[0]);raw=feed(r,'.');r.accept(raw);r.commit_source(0)
        with self.assertRaises(ProtocolError):r.accept(raw)
        with self.assertRaises(ProtocolError):r.register(p.originals[0],*ls[0])
        self.assertEqual(len(out),1)

    def test_protocol_is_fail_closed_and_v1_unchanged(self):
        raw=encode('SOURCE_END','a',0,ordinal=0,source_digest='a'*64);d=Decoder();d.feed(raw);self.assertEqual(d.pop()[0]['protocol'],'hv2-cont03a/1')
        old=OldDecoder();old.feed(raw)
        with self.assertRaises(ValueError):old.pop()
        with self.assertRaises(ProtocolError):encode('SOURCE_END','a',0,ordinal=0,source_digest='a'*64,extra='x')

    def test_finite_limits_and_failure_keep_prior_result(self):
        with self.assertRaises(ProtocolError):setup(5)
        with self.assertRaises(ProtocolError):Original('a','b','0'*64,1025,'0'*64)
        p,q,s,ls,out,r=setup(2);r.register(p.originals[0],*ls[0]);r.accept(feed(r,'.'));r.commit_source(0);r.fail()
        with self.assertRaises(ProtocolError):r.register(p.originals[1],*ls[1])
        self.assertEqual(len(out),1)

    def test_cancelled_before_launch_and_bad_trust(self):
        p,q,s,ls,out,r=setup(1)
        def cancel():raise RuntimeError('cancelled')
        value=run_context('/nonexistent',p,q,'owner','attempt',b'CA',lambda *a:None,out.append,cancel)
        self.assertIsNone(value.pid);self.assertEqual(value.state,'FAILED');self.assertFalse(out)
        with self.assertRaises(ProtocolError):run_context('/nonexistent',p,q,'owner','attempt',b'wrong',lambda *a:None,out.append,lambda:None)

    def test_reference_window_members_not_duplicate_execution(self):
        groups=list(windows(31));new=[n for i,g in enumerate(groups) for n in (g[1:] if i else g)]
        self.assertEqual(new,list(range(31)));self.assertEqual(groups[0][-1],groups[1][0])

    def test_no_global_uncertainty_from_local_missing(self):
        p,q,s,ls,out,r=setup(2)
        for i in range(2):r.register(p.originals[i],*ls[i])
        r.accept(feed(r,'.'));r.commit_source(0)
        r.accept(feed(r,'.',missing=[{'left_received':1,'right_received':None,'report_generation':2}]));r.commit_source(1)
        self.assertEqual([e['recording'] for e in out],['file-0'])
        r.accept(finish(r));r.finish();self.assertEqual(len(out),2)
        self.assertTrue(wire.loads(canonical(out[0]['result']['page'])).output_complete);self.assertFalse(wire.loads(canonical(out[1]['result']['page'])).output_complete)


class CompanionIntegrationTests(unittest.TestCase):
    """Real ledger/Bundle and adapter seams; synthetic input and worker transport only."""
    def setUp(self):
        import tempfile
        from pathlib import Path
        from nanexus_trust.frigate.historical_v2 import Snapshot,PIN_0182
        from nanexus_trust.historical_v2.prototype import DirectCapturedProfile
        from nanexus_trust.historical_v2.continuity_windows import RollingWindowCoordinator
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);root=Path(self.tmp.name)
        self.ca=root/'ca';self.ca.write_bytes(b'CA')
        self.profile=DirectCapturedProfile(Snapshot(root/'db','0'*64,'synthetic',PIN_0182,True,True),root,root,root/'never-execute',self.ca,root/'never-probe')
        p,self.req,s,self.loaded,out,r=setup(2);n=self.loaded[0][1][0]
        ns=tuple(SimpleNamespace(**{**vars(n),'token':f'first:p{i}:n0:o4:l13','packet':i}) for i in range(131))
        first=replace(p.originals[0],nal_count=131,annex_b_sha256=sha(b''.join(n.annex_b for n in ns)))
        originals=(first,p.originals[1]);proof=canonical({'schema':'qualified-source-occurrence-chain/1','originals':[asdict(o) for o in originals],'source_annex_b_sha256':sha(b''.join(n.annex_b for n in ns+self.loaded[1][1])),'source_nal_start':0})
        self.plan=Plan(originals,p.trust_sha256,proof,sha(proof));self.loaded[0]=(self.loaded[0][0],ns,self.loaded[0][2])
        self.items=tuple(SimpleNamespace(item_id=o.item_id,generation=o.generation,metadata_interval=a.Interval(str(i),str(i+1))) for i,o in enumerate(originals))
        self.resolutions=[]
        def resolver(*args):self.resolutions.append(args);return self.plan
        self.coordinator=RollingWindowCoordinator(self.profile,resolver);self.addCleanup(self.coordinator.close)
        self.coordinator._record={'owner':'owner','bundle':Bundle()};self.events=[];self.coordinator._observer=lambda *args:self.events.append(args)
        self.provider=SimpleNamespace(snapshot=self.profile.snapshot);self.response={};self.reads=[];self.calls=0

    def execute(self,mode='normal',permission=lambda:None):
        import time,threading
        from contextlib import contextmanager
        from unittest.mock import patch
        from nanexus_trust.historical_v2.direct_media import DirectInput
        @contextmanager
        def source(provider,item,*args):
            index=self.items.index(item);self.reads.append(index);receipt,nals,meta=self.loaded[index]
            if mode=='bad-hash':receipt=dict(receipt,sha256='0'*64)
            yield DirectInput(**receipt),iter(nals)
        def native(executable,plan,request,owner,attempt,trust,load,publish,check,**kwargs):
            self.calls+=1;executed=[];spec=replace(plan.attempt(request,owner,attempt),trust_bytes=len(trust));ledger=Rolling(plan,request,spec,publish)
            for index,original in enumerate(plan.originals):
                receipt,nals,metadata=load(index,check);executed.extend(n.annex_b for n in nals);ledger.register(original,receipt,nals,metadata)
                for local,n in enumerate(nals):
                    if index==0:statuses='N' if local==0 else '.' if local<128 else 'P'*(local-127)
                    else:statuses='....'
                    ledger.accept(feed(ledger,statuses))
                ledger.commit_source(index)
                if index==0:self.assertFalse(any(e[0]=='file' for e in self.events))
            if sha(b''.join(executed))!=plan.input_sha256:raise ValueError('context-occurrence-chain')
            if mode=='failure':return SimpleNamespace(state='FAILED',error='synthetic',pid=None,reaped=True,consumed=132)
            ledger.accept(finish(ledger));ledger.finish()
            return SimpleNamespace(state='EXITED_SUCCESSFULLY',error=None,pid=None,reaped=True,consumed=132)
        with patch.object(self.coordinator,'_input',source),patch('nanexus_trust.historical_v2.continuity_windows.run_context',side_effect=native):
            self.coordinator._verify_many(self.provider,self.items,None,None,self.req,self.req.camera,'rid',permission,threading.Event(),time.monotonic()+10,lambda:None,self.response)

    def test_request_local_admission_delayed_batch_full_continuation(self):
        self.execute();self.assertEqual(self.reads,[0,1]);self.assertEqual(self.calls,1)
        self.assertEqual(self.resolutions[0],(self.req,'owner',self.items,self.profile.snapshot,sha(b'CA')))
        rows=self.coordinator._record['bundle'].files;self.assertEqual([r['recording'] for r in rows],['file-0','file-1'])
        self.assertEqual(sum(rows[0]['finalized_counts'].values()),131);self.assertEqual(rows[0]['finalized_counts']['INTEGRITY_NEGATIVE'],1)
        self.assertEqual([e[0] for e in self.events],['plan','file','file']);self.assertEqual(self.response['status'],'RESULT')
        for event,entry,row in self.events[1:]:self.assertTrue(wire.loads(canonical(entry['result']['page'])).output_complete)

    def test_unqualified_wrong_identity_generation_and_trust_no_launch(self):
        from unittest.mock import patch
        for plan in (None,replace(self.plan,trust_sha256=sha(b'wrong'))):
            with self.subTest(plan=plan),patch.object(self.coordinator,'_resolve_plan',return_value=plan):
                with self.assertRaises(ValueError):self.execute()
        for attribute in ('item_id','generation'):
            value=getattr(self.items[0],attribute);setattr(self.items[0],attribute,'wrong')
            with self.assertRaises(ValueError):self.execute()
            setattr(self.items[0],attribute,value)
        self.assertEqual(self.calls,0);self.assertEqual(self.reads,[])

    def test_source_hash_failure_and_failed_aggregate(self):
        with self.assertRaises(ValueError):self.execute('bad-hash')
        self.assertFalse(self.coordinator._record['bundle'].files)
        # No retry: separate synthetic scenario, not a native execution.
        self.reads=[];self.events=[]
        with self.assertRaises(Exception):self.execute('failure')
        self.assertNotEqual(self.response.get('status'),'RESULT')
        self.assertEqual(len(self.coordinator._record['bundle'].files),2)

    def test_snapshot_mismatch_and_revoked_permission(self):
        self.provider.snapshot=replace(self.profile.snapshot,sha256='1'*64)
        with self.assertRaises(ValueError):self.execute()
        self.assertEqual(self.calls,0)

    def test_revoked_resolver_and_profile_boundaries(self):
        from nanexus_trust.historical_v2.continuity_windows import RollingWindowCoordinator
        from nanexus_trust.historical_v2.prototype import PrototypeFailure,CapturedProfile
        with self.assertRaises(TypeError):RollingWindowCoordinator(self.profile,self.plan)
        with self.assertRaises(TypeError):RollingWindowCoordinator(CapturedProfile(**vars(self.profile)),lambda *a:self.plan)
        def revoked():raise PrototypeFailure('UNAUTHORIZED')
        with self.assertRaises(PrototypeFailure):self.execute(permission=revoked)
        self.assertEqual(self.calls,0);self.assertEqual(self.resolutions,[])
        from nanexus_trust.historical_v2.windows import WindowCoordinator
        self.assertNotEqual(self.coordinator.execution_profile,WindowCoordinator.execution_profile)

class CandidateAdmissionTests(CompanionIntegrationTests):
    def setUp(self):
        super().setUp()
        self.qualified=self.plan
        self.provider._items={item.item_id:(item,item.item_id+'.mp4',None) for item in self.items}
        sources=[]
        for item,(_,nals,metadata) in zip(self.items,self.loaded):
            facts=[{'token':n.token,'packet':n.packet,'nal':n.nal_in_packet,'length_offset':n.length_offset,'payload_offset':n.payload_offset,'payload_bytes':n.payload_bytes,'payload_sha256':n.payload_sha256,'pts':n.pts,'dts':n.dts,'duration':n.duration,'time_base':str(n.time_base)} for n in nals]
            sources.append({'path':item.item_id+'.mp4','start':metadata['start'],'end':metadata['end'],'occurrences_sha256':sha(canonical(facts))})
        self.manifest={'schema':'candidate-rolling-input/1','policy':'catalog-start-id-packet-nal/1',
            'originals':[asdict(o) for o in self.qualified.originals],
            'candidate_annex_b_sha256':self.qualified.input_sha256,
            'binding':{'request_sha256':sha(wire.dumps(self.req)),'owner_sha256':sha(b'owner'),'camera':self.req.camera.native_key,'binding_generation':self.req.camera.binding_generation,'snapshot_sha256':self.profile.snapshot.sha256,'provider_profile':self.profile.provider_profile},'sources':sources}
        self.plan=self.make(self.manifest)
    def make(self,value):
        raw=canonical(value);return Plan(self.qualified.originals,self.qualified.trust_sha256,raw,sha(raw))
    def test_candidate_without_upstream_reference_and_preserved_stronger_schema(self):
        self.assertNotIn('source_nal_start',self.manifest);self.assertNotIn('source_annex_b_sha256',self.manifest)
        self.assertTrue(self.plan.candidate);self.assertFalse(self.qualified.candidate)
        self.assertEqual(self.qualified.execution_policy,'qualified-rolling-context-v1')
        self.assertEqual(self.plan.execution_policy,'candidate-rolling-context-v1')
        self.assertNotEqual(self.plan.identity,self.qualified.identity)
        self.execute()
        self.assertEqual(self.coordinator.execution_profile,'candidate-rolling-four-originals-experimental-v1')
        entry=self.events[1][1];page=wire.loads(canonical(entry['result']['page']))
        self.assertEqual(page.engine_profile,'candidate-rolling-context-experimental-v1')
        self.assertTrue(all('candidate-order-not-upstream-corroborated' in o.reasons for o in page.observations))
        import base64
        source=next(o for o in entry['result']['objects'] if o['id'].startswith('sources-'))
        evidence=json.loads(base64.b64decode(source['data']))
        self.assertIn('candidate',evidence);self.assertNotIn('proof',evidence)
    def test_unknown_schema_policy_and_extra_fields_rejected(self):
        import copy
        for key,value in [('schema','unknown/1'),('policy','upstream-is-proven/1'),('extra',True)]:
            m=copy.deepcopy(self.manifest);m[key]=value
            with self.assertRaises(ValueError):self.make(m)
        spec=self.plan.attempt(self.req,'owner','attempt')
        with self.assertRaises(ValueError):replace(spec,policy='unknown')
        with self.assertRaises(ValueError):Rolling(self.plan,self.req,replace(spec,policy='qualified-rolling-context-v1'),lambda e:None)
    def test_request_owner_camera_binding_snapshot_paths_and_metadata(self):
        import copy
        for field in ('request_sha256','owner_sha256','camera','binding_generation','snapshot_sha256'):
            m=copy.deepcopy(self.manifest);m['binding'][field]='f'*64;self.plan=self.make(m)
            with self.subTest(field=field),self.assertRaises(ValueError):self.execute()
        self.plan=self.make(self.manifest);self.provider._items[self.items[0].item_id]=(self.items[0],'different.mp4',None)
        with self.assertRaises(ValueError):self.execute()
        self.assertEqual(self.calls,0)
    def test_occurrence_and_source_hash_binding_without_deduplication(self):
        # Identical payloads at131 positions retain unique tokens and counts.
        self.assertEqual(len({n.annex_b for n in self.loaded[0][1]}),1)
        self.assertEqual(len({n.token for n in self.loaded[0][1]}),131)
        self.loaded[0][1][0].packet=99
        with self.assertRaises(ValueError):self.execute()
        self.assertFalse(self.coordinator._record['bundle'].files)
    def test_ties_are_candidate_order_not_continuity_and_unsafe_paths_rejected(self):
        import copy
        m=copy.deepcopy(self.manifest);m['sources'][1]['start']='0';m['sources'][1]['end']='1'
        plan=self.make(m);self.assertTrue(plan.candidate) # explicit ID tie-break, no continuity claim
        for path in ('../escape','/absolute','file-0.mp4'):
            m=copy.deepcopy(self.manifest);m['sources'][1]['path']=path
            with self.assertRaises(ValueError):self.make(m)
        m=copy.deepcopy(self.manifest);m['sources'][1]['start']='-1'
        with self.assertRaises(ValueError):self.make(m)
    def test_candidate_digest_is_executed_input_binding(self):
        actual=sha(b''.join(n.annex_b for _,nals,_ in self.loaded for n in nals))
        self.assertEqual(self.plan.input_sha256,actual)
        self.assertNotEqual(actual,sha(b'claimed upstream reference'))
        self.assertEqual(self.plan.attempt(self.req,'owner','attempt').policy,'candidate-rolling-context-v1')

    def test_wrong_candidate_input_digest_never_completes_aggregate(self):
        import copy
        m=copy.deepcopy(self.manifest);m['candidate_annex_b_sha256']=sha(b'not executed')
        self.plan=self.make(m)
        with self.assertRaisesRegex(ValueError,'context-occurrence-chain'):self.execute()
        self.assertNotEqual(self.response.get('status'),'RESULT')

    def test_retained_task06_qualified_manifest_unchanged(self):
        from pathlib import Path
        root=Path(__file__).resolve().parents[1]
        raw=(root/'.local/runtime/hv2-cont03d/task06-plan.json').read_bytes()
        value=json.loads(raw)
        plan=Plan(tuple(Original(**o) for o in value['originals']),'bd3ce7aaefe870a520caee940fbb99aa4ac36d6cc9aff8e12851873b65f71bd3',raw,sha(raw))
        self.assertFalse(plan.candidate);self.assertEqual(plan.qualified_order,raw)
        self.assertEqual(plan.input_sha256,value['source_annex_b_sha256'])
        self.assertEqual(plan.execution_policy,'qualified-rolling-context-v1')

    def test_candidate_profile_and_metadata_mismatch(self):
        import copy
        m=copy.deepcopy(self.manifest);m['binding']['provider_profile']='unknown'
        with self.assertRaises(ValueError):self.make(m)
        m=copy.deepcopy(self.manifest);m['sources'][0]['end']='9'
        self.plan=self.make(m)
        with self.assertRaises(ValueError):self.execute()
        self.assertEqual(self.calls,0)

if __name__=='__main__':unittest.main()
