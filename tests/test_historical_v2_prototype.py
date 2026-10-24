"""Staged prototype gates. Saved MP4 runs only by explicit SavedWitnessTests selection."""
from dataclasses import replace
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace
import base64,hashlib,json,os,sqlite3,tempfile,threading,time,unittest
from unittest.mock import patch
from nanexus_trust.historical_v2 import prototype as p, composition as c, wire
from nanexus_trust.historical_v2.media import Nal
from nanexus_trust.frigate.historical_v2 import Snapshot,PIN_0182,PROFILE_0182,_COLUMNS
from nanexus_trust.companion.service import CompanionService
from nanexus_trust.companion.live import LiveScheduler,PlaybackMapping
from nanexus_trust.companion.historical_transport import HistoricalTransport
from nanexus_trust.contracts import CameraId,RenditionId
from nanexus_trust.live import Binding
from test_live import POLICY

ROOT=Path(__file__).resolve().parents[1]
ART=ROOT/'.local/governance/ai-dlc/evidence/m1-hv2-d-planning/prototype-slice1-artifacts'
WORKER=ROOT/'.local/governance/ai-dlc/evidence/m1-hv2-d-planning/d1.5-artifacts/worker_child'
TOKEN='x'*48
RID='a'*32

def no_source(binding):raise AssertionError('Live source must not start')

class Harness(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=ART);self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.media=self.root/'media';self.media.mkdir();self.scratch=self.root/'scratch';self.scratch.mkdir(mode=0o700)
        self.database=self.root/'snapshot.db';self.ca=self.root/'ca.pem';self.ca.write_bytes(b'not yet used')
        with sqlite3.connect(self.database) as db:
            db.execute('CREATE TABLE recordings ('+','.join('"'+k+'" '+v+(' PRIMARY KEY' if k=='id' else '') for k,v in _COLUMNS.items())+')')
            db.execute('CREATE INDEX recordings_camera_start_time_end_time ON recordings(camera,start_time,end_time)')
        self.coordinator=p.Coordinator(self.profile())
        self.camera='front';self.connect()
    def profile(self):
        return p.CapturedProfile(Snapshot(self.database,c.sha(self.database.read_bytes()) if hasattr(c,'sha') else hashlib.sha256(self.database.read_bytes()).hexdigest(),'captured-test',PIN_0182,True,True),self.media,self.scratch,WORKER,self.ca,Path('/usr/bin/ffprobe'))
    def connect(self):
        binding=Binding(CameraId('logical'),RenditionId('main'),'source','rev1',POLICY)
        self.live=LiveScheduler((PlaybackMapping(self.camera,'stream','single',binding),),no_source)
        self.addCleanup(self.live.close)
        jobs=SimpleNamespace(close=lambda:None,lock=threading.RLock(),_closed=False)
        self.service=CompanionService(self.live,jobs,('logical',),historical_v2=self.coordinator)
        self.transport=HistoricalTransport(self.service,TOKEN);self.addCleanup(self.coordinator.close)
        self.guard=self.service.camera_bindings()['cameras'][0]['binding_guard']
    def row(self,rid='r1',start=10,end=20,data=b'harmless-synthetic-MP4-container-placeholder'):
        (self.media/(rid+'.mp4')).write_bytes(data)
        with sqlite3.connect(self.database) as db:db.execute('INSERT INTO recordings(id,camera,path,start_time,end_time,duration) VALUES(?,?,?,?,?,?)',(rid,self.camera,'/media/frigate/recordings/'+rid+'.mp4',start,end,end-start))
        self.coordinator.profile=self.profile()
    def call(self,operation='verify',token=TOKEN,**kwargs):
        data={'binding_guard':self.guard,'request_id':RID}
        if operation=='verify':data.update(start='11',end='12')
        data.update(kwargs)
        return self.transport.historical('POST','/v1/historical/v2/'+operation+'?camera='+self.camera,'Bearer '+token,json.dumps(data).encode())
    def decoded(self,response):
        result=response['result'];base=wire.loads(c.canonical(result['page']))
        objects={d['id']:base64.b64decode(d['data'],validate=True) for d in result['objects']}
        for d in result['objects']:
            self.assertEqual(len(objects[d['id']]),d['size']);self.assertEqual(hashlib.sha256(objects[d['id']]).hexdigest(),d['sha256'])
        self.assertEqual(hashlib.sha256(c.canonical(result['page'])).hexdigest(),result['sha256'])
        updates=[json.loads(v) for k,v in objects.items() if k.startswith('updates-')][0]
        observations=list(base.observations)
        if base.next_cursor:
            observations+= [wire.loads(c.canonical(o)) for o in json.loads(objects[base.next_cursor])['observations']]
        return base,objects,updates,observations

class AdmissionTests(Harness):
    def test_default_off_auth_and_invalid_scope_before_source(self):
        self.assertEqual(self.call(token='bad')[0],401)
        self.service.historical_v2=None;self.assertEqual(self.call()[0],404)
        self.service.historical_v2=self.coordinator
        self.assertEqual(self.call(binding_guard='b'*64)[0],403)
        self.assertEqual(self.call(start='12',end='11')[0],400)
        self.assertEqual(self.call(start='0',end='1000')[0],413)
        self.assertEqual(list(self.scratch.iterdir()),[])
    def test_empty_multiple_and_incomplete(self):
        self.assertEqual(self.call()[1]['status'],'NO_MATCHING_RECORDING')
        self.row();self.row('r2')
        self.assertEqual(self.call()[1]['selection']['count'],2)
        self.coordinator.profile=replace(self.coordinator.profile,snapshot=replace(self.coordinator.profile.snapshot,complete=False))
        self.assertEqual(self.call()[1]['status'],'SELECTION_INCOMPLETE')
    def test_busy_cancel_reader_and_no_live_execution(self):
        self.row();ready=threading.Event();responses=[]
        def blocked(*a,**kw):
            ready.set()
            while True:kw_check=a[2];kw_check();time.sleep(.005)
            yield
        with patch('nanexus_trust.historical_v2.prototype.read_nals',side_effect=blocked):
            thread=threading.Thread(target=lambda:responses.append(self.call()));thread.start()
            try:
                self.assertTrue(ready.wait(2));self.assertEqual(self.call()[0],409)
                self.assertEqual(self.call('cancel',request_id='b'*32)[0],404)
                self.assertEqual(self.call('cancel')[1]['status'],'CANCELLATION_REQUESTED')
            finally:thread.join(3)
        self.assertFalse(thread.is_alive());self.assertEqual(responses[0][1]['status'],'CANCELLED');self.assertEqual(list(self.scratch.iterdir()),[])
    def test_binding_change_denies_result_release(self):
        self.row();ready=threading.Event();responses=[]
        def reader(*args,**kwargs):
            self.live.configure((replace(self.live.mappings[0],binding=replace(self.live.mappings[0].binding,revision='rev2')),))
            args[2]();yield
        with patch('nanexus_trust.historical_v2.prototype.read_nals',side_effect=reader):responses.append(self.call())
        self.assertEqual(responses[0][0],403);self.assertEqual(list(self.scratch.iterdir()),[])
    def test_missing_file_and_shutdown(self):
        self.row();(self.media/'r1.mp4').unlink();self.assertEqual(self.call()[1]['status'],'SOURCE_UNAVAILABLE')
        self.coordinator.close();self.assertEqual(self.call()[1]['status'],'UNAVAILABLE')

class NativeResponseTests(Harness):
    def prepare(self,wrong=False):
        from test_historical_v2_worker import retained_fixture
        raw,ca=retained_fixture()
        if wrong:
            der=bytearray(base64.b64decode(b''.join(ca.splitlines()[1:-1])));at=der.index(b'\x02\x82\x01\x01\x00')+37;der[at]^=1
            ca=b'-----BEGIN CERTIFICATE-----\n'+base64.encodebytes(der)+b'-----END CERTIFICATE-----\n'
        self.ca.write_bytes(ca);self.row()
        return [Nal(i,'occurrence-'+str(i),i,0,i,i,len(b)-4,hashlib.sha256(b[4:]).hexdigest(),b[4]&31,b,None,None,None,Fraction(1,90000)) for i,b in enumerate(raw)]
    def test_real_native_negative_response(self):
        nals=self.prepare()
        with patch('nanexus_trust.historical_v2.prototype.read_nals',return_value=(n for n in nals)):
            code,response=self.call()
        self.assertEqual(code,200);self.assertEqual(response['status'],'RESULT',response.get('execution'))
        base,objects,updates,obs=self.decoded(response)
        self.assertEqual(sum(o.state.value=='INTEGRITY_NEGATIVE' for o in obs),15)
        self.assertEqual(response['execution']['consumed'],91);self.assertEqual(len(updates),92);self.assertTrue(response['execution']['reaped'])
        self.assertEqual(base.lifecycle.value,'INCOMPLETE');self.assertEqual(base.coverage[0].state.value,'UNKNOWN')
        self.assertTrue(all(o.placement.status.value=='UNPLACED' for o in obs));self.assertNotIn(str(self.root),json.dumps(response))
    def test_wrong_ca_and_output_refusal(self):
        nals=self.prepare(wrong=True)
        with patch('nanexus_trust.historical_v2.prototype.read_nals',return_value=(n for n in nals)):
            _,response=self.call()
        self.assertEqual(response['status'],'RESULT',response.get('execution'))
        self.assertFalse(any(o.state.value=='VERIFIED_SIGNING' for o in self.decoded(response)[3]))
        with patch('nanexus_trust.historical_v2.prototype.read_nals',return_value=(n for n in nals)),patch('nanexus_trust.historical_v2.prototype.MAX_RESPONSE',100):
            _,response=self.call(request_id='b'*32)
        self.assertEqual(response['status'],'LIMIT');self.assertIsNone(response.get('result'))

class SavedWitnessTests(Harness):
    def test_saved_single_file_through_companion(self):
        saved=ROOT/'.local/governance/ai-dlc/evidence/m1-hv2-d-planning/d1.5-artifacts'
        original=json.loads((saved/'source-ledger.json').read_text())['source']
        before={name:hashlib.sha256(Path(name).read_bytes()).hexdigest() for name in original}
        self.assertEqual(before,original)
        media=Path(next(k for k in original if k.endswith('.mp4')))
        database=Path(next(k for k in original if k.endswith('.sqlite')))
        ca=Path(next(k for k in original if k.endswith('.pem')))
        profile=p.CapturedProfile(Snapshot(database,original[str(database)],'slice3-captured',PIN_0182,True,True),media.parents[3],self.scratch,WORKER,ca,Path('/usr/bin/ffprobe'))
        self.coordinator.profile=profile;self.camera='slice3_signed';self.connect()
        interval=wire.loads((saved/'saved-result-page.json').read_bytes()).request.interval
        prior={'start':interval.start,'end':interval.end}
        start=time.monotonic();code,response=self.call(start=prior['start'],end=prior['end']);duration=time.monotonic()-start
        self.assertEqual(response['status'],'RESULT',response.get('execution'))
        base,objects,updates,observations=self.decoded(response)
        actual=dict(nals=len(response['result']['source_ledger']),updates=len(updates),fresh=sum(u['fresh_report'] for u in updates),finalized=len(observations),tail=len(base.unresolved_refs),verified=sum(o.state.value=='VERIFIED_SIGNING' for o in observations),indeterminate=sum(o.state.value=='INDETERMINATE' for o in observations))
        accepted=json.loads((saved/'saved-witness.json').read_text())
        self.assertEqual(actual,dict(nals=accepted['feeds'],updates=accepted['updates'],fresh=accepted['fresh_reports'],finalized=accepted['finalized'],tail=accepted['tail'],verified=accepted['verified'],indeterminate=accepted['indeterminate_finalized']))
        self.assertEqual(base.lifecycle.value,'INCOMPLETE');self.assertEqual(base.coverage[0].state.value,'UNKNOWN')
        self.assertEqual(original,{name:hashlib.sha256(Path(name).read_bytes()).hexdigest() for name in original})
        self.assertEqual(list(self.scratch.iterdir()),[])
        (ART/'saved-result.json').write_text(json.dumps(response,separators=(',',':')))
        (ART/'saved-summary.json').write_text(json.dumps(dict(seconds=duration,actual=actual,source_preserved=True,response_bytes=len(json.dumps(response,separators=(',',':')).encode()),execution=response['execution']),indent=2))

class NativeFailureTests(NativeResponseTests):
    # Reuse actual approved fixture preparation; do not inherit its test cases.
    test_real_native_negative_response=None
    test_wrong_ca_and_output_refusal=None
    def test_actual_child_truncated_output_not_partial_result(self):
        from nanexus_trust.historical_v2.worker_supervisor import Supervisor
        nals=self.prepare();real=Supervisor.run
        def run(supervisor,*args,**kwargs):return real(supervisor,*args,**kwargs,_fault='truncated')
        with patch('nanexus_trust.historical_v2.prototype.read_nals',return_value=(n for n in nals)),patch.object(Supervisor,'run',run):
            _,response=self.call()
        self.assertEqual(response['status'],'EXECUTION_FAILED');self.assertIsNone(response.get('result'));self.assertTrue(response['execution']['reaped'])
    def test_actual_worker_cancellation_and_shutdown_reap(self):
        import subprocess
        nals=self.prepare();spawned=threading.Event();processes=[];responses=[];real=subprocess.Popen
        def spawn(*args,**kwargs):process=real(*args,**kwargs);processes.append(process);spawned.set();return process
        with patch('nanexus_trust.historical_v2.prototype.read_nals',return_value=(n for n in nals)),patch('nanexus_trust.historical_v2.worker_supervisor.subprocess.Popen',side_effect=spawn):
            thread=threading.Thread(target=lambda:responses.append(self.call()));thread.start()
            try:
                self.assertTrue(spawned.wait(3));self.assertEqual(self.call('cancel')[1]['status'],'CANCELLATION_REQUESTED')
                self.coordinator.close()
            finally:thread.join(4)
        self.assertFalse(thread.is_alive());self.assertTrue(responses[0][1]['execution']['reaped'])
        self.assertEqual(responses[0][1]['status'],'CANCELLED');self.assertTrue(all(p.returncode is not None for p in processes))


class MultiSelectionTests(Harness):
    def stub(self,*args):
        # Selection-only control; never used as native acceptance evidence.
        args[-1]['status']='UNSUPPORTED'
    def test_two_through_eight_and_overflow_no_processing(self):
        for n in range(2,10):
            if n==2:self.row('r1')
            self.row('r'+str(n))
            with patch.object(self.coordinator,'_verify_file',side_effect=self.stub) as run:
                _,r=self.call()
            if n<=8:
                self.assertEqual(r['schema'],'nanexus-hv2-prototype-window/1')
                self.assertEqual((r['processed'],r['unprocessed']),(n,0))
                self.assertEqual(run.call_count,n)
                self.assertEqual(r['coverage'],'UNKNOWN');self.assertEqual(r['continuity'],'UNKNOWN')
            else:
                self.assertEqual(r['status'],'LIMIT');self.assertEqual(r['reason'],'SELECTION_TOO_LARGE');run.assert_not_called()
        self.row('r10')
        with patch.object(self.coordinator,'_verify_file') as run:
            _,r=self.call()
        self.assertEqual(r['status'],'LIMIT');self.assertFalse(r['selection']['count_exact']);run.assert_not_called()
    def test_order_metadata_gaps_and_overlaps_not_signing_coverage(self):
        self.row('z',10,14);self.row('a',10,13);self.row('later',16,18)
        with patch.object(self.coordinator,'_verify_file',side_effect=self.stub):
            _,r=self.call(start='9',end='19')
        self.assertEqual(r['selection']['order'],['a','z','later'])
        self.assertEqual(r['selection']['catalog_gaps'],[dict(start='9',end='10'),dict(start='14',end='16'),dict(start='18',end='19')])
        self.assertEqual(r['selection']['catalog_overlaps'],[dict(recordings=['a','z'],start='10',end='13')])
        self.assertEqual(r['coverage'],'UNKNOWN');self.assertEqual(r['continuity'],'UNKNOWN')
    def test_duplicate_path_and_hardlink_rejected(self):
        self.row('r1');self.row('r2')
        for hardlink in (False,True):
            with sqlite3.connect(self.database) as db:
                db.execute('UPDATE recordings SET path=? WHERE id=?',('/media/frigate/recordings/'+('r2' if hardlink else 'r1')+'.mp4','r2'))
            if hardlink:
                (self.media/'r2.mp4').unlink();os.link(self.media/'r1.mp4',self.media/'r2.mp4')
            self.coordinator.profile=self.profile()
            with patch.object(self.coordinator,'_verify_file') as run:_,r=self.call()
            self.assertEqual(r['status'],'DUPLICATE_RECORDING');run.assert_not_called()
    def test_inconsistent_and_incomplete_selection_no_processing(self):
        self.row();self.row('r2')
        self.coordinator.profile=replace(self.coordinator.profile,snapshot=replace(self.coordinator.profile.snapshot,complete=False))
        with patch.object(self.coordinator,'_verify_file') as run:_,r=self.call()
        self.assertEqual(r['status'],'SELECTION_INCOMPLETE');run.assert_not_called()
        self.coordinator.profile=self.profile()
        real=p.FrigateHistoricalProvider.discover
        def inconsistent(provider,*args):return replace(real(provider,*args),consistency=p.a.Quality.UNKNOWN)
        with patch.object(p.FrigateHistoricalProvider,'discover',inconsistent),patch.object(self.coordinator,'_verify_file') as run:_,r=self.call()
        self.assertEqual(r['status'],'SELECTION_INCOMPLETE');run.assert_not_called()
    def test_missing_and_unsupported_are_file_specific(self):
        self.row();self.row('r2');(self.media/'r2.mp4').unlink()
        with patch('nanexus_trust.historical_v2.prototype.read_nals',side_effect=p.MediaFailure('unsupported')):_,r=self.call()
        self.assertEqual([f['status'] for f in r['files']],['UNSUPPORTED','SOURCE_UNAVAILABLE'])
        self.assertEqual((r['processed'],r['unprocessed']),(2,0));self.assertEqual(r['status'],'PARTIAL')
        self.assertEqual(list(self.scratch.iterdir()),[])


class MultiNativeTests(Harness):
    def prepare_files(self,wrong=False,repeat_first=False):
        from test_historical_v2_worker import retained_fixture
        raw,ca=retained_fixture()
        if wrong:
            der=bytearray(base64.b64decode(b''.join(ca.splitlines()[1:-1])));at=der.index(b'\x02\x82\x01\x01\x00')+37;der[at]^=1
            ca=b'-----BEGIN CERTIFICATE-----\n'+base64.encodebytes(der)+b'-----END CERTIFICATE-----\n'
        self.ca.write_bytes(ca)
        self.raw=[raw*2 if repeat_first else raw,raw[:-1]]
        for rid,nals in zip(('r1','r2'),self.raw):
            data=b''.join(len(n[4:]).to_bytes(4,'big')+n[4:] for n in nals)
            self.row(rid,data=data)
        def probe(executable,path,*args):
            data=Path(path).read_bytes();packets=[];offset=0
            while offset<len(data):
                size=4+int.from_bytes(data[offset:offset+4],'big');i=len(packets)
                packets.append(dict(stream_index=0,pos=str(offset),size=str(size),pts=i*3000,dts=i*3000,duration=3000));offset+=size
            return dict(streams=[dict(index=0,codec_name='h264',codec_type='video',codec_tag_string='avc1',nal_length_size='4',time_base='1/90000')],packets=packets)
        return patch('nanexus_trust.historical_v2.media._probe',side_effect=probe)
    def assert_closure(self,response,file):
        bound=dict(file);digest=bound.pop('closure_sha256')
        self.assertEqual(digest,p.sha(c.canonical(dict(request_id=RID,camera=self.camera,snapshot=self.coordinator.profile.snapshot.sha256,cut=self.coordinator.profile.snapshot.cut_id,file=bound))))
        self.assertEqual(file['recording'],response['selection']['recordings'][response['files'].index(file)]['recording'])
        return self.decoded(file)
    def test_real_independent_negative_and_tail_closures(self):
        from nanexus_trust.historical_v2.worker_supervisor import Supervisor
        control=self.prepare_files();runs=[];real=Supervisor.run
        def run(supervisor,spec,*args,**kwargs):
            self.assertFalse(any(not o.reaped for _,o in runs))
            outcome=real(supervisor,spec,*args,**kwargs);runs.append((spec,outcome));return outcome
        with control,patch.object(Supervisor,'run',run):_,r=self.call()
        self.assertEqual(r['status'],'RESULT');self.assertEqual((r['processed'],r['unprocessed']),(2,0))
        self.assertEqual(len(runs),2);self.assertEqual(len({s.attempt for s,o in runs}),2);self.assertEqual(len({s.run for s,o in runs}),2)
        self.assertEqual([o.consumed for s,o in runs],[91,90]);self.assertTrue(all(o.reaped for s,o in runs))
        for f,(spec,outcome) in zip(r['files'],runs):
            base,objects,updates,obs=self.assert_closure(r,f)
            self.assertEqual(len(updates),outcome.consumed+1)
            self.assertEqual(updates[0]['generating_feed_ordinal'],0)
            self.assertEqual(updates[0]['consumed'],1)
            self.assertTrue(all(u['attempt']==spec.attempt and u['run_id']==spec.run for u in updates))
            self.assertEqual(len(obs)+len(base.unresolved_refs),outcome.consumed)
            self.assertTrue(all(o.placement.status.value=='UNPLACED' for o in obs))
            self.assertEqual(base.coverage[0].state.value,'UNKNOWN')
        self.assertEqual(sum(o.state.value=='INTEGRITY_NEGATIVE' for o in self.decoded(r['files'][0])[3]),15)
        self.assertTrue(self.decoded(r['files'][1])[0].unresolved_refs)
        self.assertTrue(set(n['token'] for n in r['files'][0]['result']['source_ledger']).isdisjoint(n['token'] for n in r['files'][1]['result']['source_ledger']))
        self.assertNotIn(str(self.root),json.dumps(r));self.assertEqual(list(self.scratch.iterdir()),[])
        (ART2/'native-summary.json').write_text(json.dumps(dict(bytes=len(c.canonical(r)),files=[dict(recording=f['recording'],execution=f['execution'],finalized=len(self.decoded(f)[3]),negative=sum(o.state.value=='INTEGRITY_NEGATIVE' for o in self.decoded(f)[3]),tail=len(self.decoded(f)[0].unresolved_refs)) for f in r['files']]),indent=2))
    def test_real_multifile_continuation_is_complete(self):
        # Repeated existing signed intervals are a paging stress control, not
        # a repaired signing stream or a qualified continuity witness.
        with self.prepare_files(repeat_first=True):_,r=self.call()
        self.assertEqual(r['status'],'RESULT')
        first=self.decoded(r['files'][0]);self.assertTrue(first[0].next_cursor)
        self.assertGreater(len(first[3]),128)
        self.assertIn(first[0].next_cursor,first[1])
        for f in r['files']:self.assert_closure(r,f)
        self.assertLessEqual(len(c.canonical(r)),p.MAX_RESPONSE)
    def test_wrong_ca_remains_nonverified(self):
        with self.prepare_files(wrong=True):_,r=self.call()
        self.assertEqual(r['status'],'RESULT')
        for f in r['files']:self.assertFalse(any(o.state.value=='VERIFIED_SIGNING' for o in self.decoded(f)[3]))

ART2=ROOT/'.local/governance/ai-dlc/evidence/m1-hv2-d-planning/prototype-slice2-artifacts'

class MultiOperationalTests(MultiNativeTests):
    test_real_independent_negative_and_tail_closures=None
    test_wrong_ca_remains_nonverified=None
    test_real_multifile_continuation_is_complete=None
    def test_cancellation_between_files_stops_remaining(self):
        control=self.prepare_files();real=self.coordinator._verify_file
        def verify(*args):
            real(*args);self.coordinator.cancel(self.camera,RID)
        with control,patch.object(self.coordinator,'_verify_file',side_effect=verify) as run:_,r=self.call()
        self.assertEqual(r['status'],'CANCELLED');self.assertEqual((r['processed'],r['unprocessed']),(1,1));self.assertEqual(run.call_count,1)
        self.assertEqual([f['status'] for f in r['files']],['RESULT','UNPROCESSED']);self.assertEqual(r['files'][1]['reason'],'CANCELLED')
        self.assert_closure(r,r['files'][0]);self.assertEqual(list(self.scratch.iterdir()),[])
    def test_later_native_failure_keeps_first_closure(self):
        from nanexus_trust.historical_v2.worker_supervisor import Supervisor
        real=Supervisor.run;number=0
        def run(supervisor,*args,**kwargs):
            nonlocal number
            number+=1
            return real(supervisor,*args,**kwargs,**({'_fault':'truncated'} if number==2 else {}))
        with self.prepare_files(),patch.object(Supervisor,'run',run):_,r=self.call()
        self.assertEqual(r['status'],'PARTIAL');self.assertEqual((r['processed'],r['unprocessed']),(2,0))
        self.assertEqual([f['status'] for f in r['files']],['RESULT','EXECUTION_FAILED']);self.assertIsNone(r['files'][1]['result'])
        self.assertTrue(all(f['execution']['reaped'] for f in r['files']));self.assert_closure(r,r['files'][0])
    def test_response_limit_withholds_all_closures(self):
        with self.prepare_files(),patch.object(p,'MAX_RESPONSE',2000):_,r=self.call()
        self.assertEqual(r['status'],'LIMIT');self.assertEqual(r['reason'],'RESPONSE_TOO_LARGE');self.assertEqual(r['files'],[]);self.assertTrue(r['evidence_withheld'])
        self.assertEqual((r['processed'],r['unprocessed']),(2,0))
    def test_cancel_during_second_reader(self):
        control=self.prepare_files();real=p.read_nals;count=0
        def reader(*args,**kwargs):
            nonlocal count
            count+=1
            if count==2:self.coordinator.cancel(self.camera,RID)
            yield from real(*args,**kwargs)
        with control,patch.object(p,'read_nals',reader):_,r=self.call()
        self.assertEqual(r['status'],'CANCELLED');self.assertEqual([f['status'] for f in r['files']],['RESULT','CANCELLED'])
        self.assertEqual((r['processed'],r['unprocessed']),(2,0));self.assertEqual(list(self.scratch.iterdir()),[])
    def test_per_file_nal_cap_and_stop(self):
        self.row();self.row('r2')
        ns=[Nal(i,'t'+str(i),i,0,i,i,1,hashlib.sha256(b'a').hexdigest(),1,b'\0\0\0\1a',None,None,None,Fraction(1)) for i in range(1025)]
        with patch.object(p,'read_nals',side_effect=lambda *args,**kw:(n for n in ns)):_,r=self.call()
        self.assertEqual(r['status'],'LIMIT');self.assertEqual([f['status'] for f in r['files']],['LIMIT','UNPROCESSED'])
        self.assertEqual((r['processed'],r['unprocessed']),(1,1));self.assertEqual(list(self.scratch.iterdir()),[])

    def test_request_deadline_between_files(self):
        self.row();self.row('r2');expired=False;clock=time.monotonic()
        def now():return clock+61 if expired else clock
        def first(*args):
            nonlocal expired
            args[-1]['status']='UNSUPPORTED';expired=True
        with patch.object(p.time,'monotonic',side_effect=now),patch.object(self.coordinator,'_verify_file',side_effect=first) as run:_,r=self.call()
        self.assertEqual(r['status'],'LIMIT');self.assertEqual((r['processed'],r['unprocessed']),(1,1));self.assertEqual(run.call_count,1)
        self.assertEqual(r['files'][1]['reason'],'LIMIT')
    def test_annex_b_byte_cap_stops_remaining(self):
        self.row();self.row('r2');payload=b'a'*(1<<20)
        ns=[Nal(i,'t'+str(i),i,0,i,i,len(payload),hashlib.sha256(payload).hexdigest(),1,b'\0\0\0\1'+payload,None,None,None,Fraction(1)) for i in range(9)]
        with patch.object(p,'read_nals',side_effect=lambda *args,**kw:(n for n in ns)):_,r=self.call()
        self.assertEqual(r['status'],'LIMIT');self.assertEqual((r['processed'],r['unprocessed']),(1,1));self.assertIsNone(r['files'][0]['result'])
        self.assertEqual(r['files'][1]['reason'],'LIMIT');self.assertEqual(list(self.scratch.iterdir()),[])
    def test_cancellation_during_second_worker_reaps_both(self):
        import subprocess
        real=subprocess.Popen;processes=[]
        def spawn(*args,**kwargs):
            process=real(*args,**kwargs);processes.append(process)
            if len(processes)==2:self.coordinator.cancel(self.camera,RID)
            return process
        with self.prepare_files(),patch('nanexus_trust.historical_v2.worker_supervisor.subprocess.Popen',side_effect=spawn):_,r=self.call()
        self.assertEqual(r['status'],'CANCELLED');self.assertEqual((r['processed'],r['unprocessed']),(2,0))
        self.assertEqual([f['status'] for f in r['files']],['RESULT','CANCELLED'])
        self.assertEqual(len(processes),2);self.assertTrue(all(x.returncode is not None for x in processes))
        self.assertTrue(all(f['execution']['reaped'] for f in r['files']));self.assertEqual(list(self.scratch.iterdir()),[])
