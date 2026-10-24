"""Pinned API controls and explicitly selected original saved-MP4/native witness."""
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit, parse_qs
from unittest.mock import patch
import base64, hashlib, io, json, tempfile, threading, unittest
from dataclasses import replace
from test_historical_v2_prototype import Harness, WORKER, ROOT, RID
from nanexus_trust.historical_v2 import prototype as p, composition as c
from nanexus_trust.frigate import historical_v2_api as api

ART=ROOT/'.local/governance/ai-dlc/evidence/m1-hv2-d-planning/prototype-slice3-api-artifacts'
FIELDS={'id','start_time','end_time','duration','segment_size','motion','objects','motion_heatmap'}

class Reply(io.BytesIO):
    status=200
    headers={}

class ApiHarness(Harness):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=ART);self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.media=self.root/'media';self.media.mkdir();self.scratch=self.root/'scratch';self.scratch.mkdir(mode=0o700)
        self.ca=self.root/'ca.pem';self.ca.write_bytes(b'not yet used');self.token=self.root/'api-token';self.token.write_text('private-test-token')
        self.coordinator=p.Coordinator(p.ApiProfile('http://frigate.test',self.token,self.media,self.scratch,WORKER,self.ca,Path('/usr/bin/ffprobe')))
        self.rows=[];self.calls=[];self.version=api.VERSION;self.camera='front';self.connect()
    def row(self,rid='10.0-aaaaaa',start=10,end=20,data=b'placeholder'):
        row=dict(id=rid,start_time=start,end_time=end,duration=end-start,segment_size=0.01,motion=0,objects=0,motion_heatmap=None)
        date=datetime.fromtimestamp(int(start),timezone.utc)
        path=self.media/date.strftime('%Y-%m-%d/%H')/self.camera/date.strftime('%M.%S.mp4')
        path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data);self.rows.append(row)
        return path
    @contextmanager
    def http(self,raw=None):
        def open(request,timeout):
            self.assertEqual(timeout,2);self.calls.append(request)
            path=urlsplit(request.full_url).path
            if path=='/api/version':return Reply(self.version.encode())
            self.assertEqual(path,'/api/'+self.camera+'/recordings')
            self.assertEqual(request.get_header('Authorization'),'Bearer private-test-token')
            return Reply(json.dumps(self.rows).encode() if raw is None else raw)
        with patch.object(api,'build_opener',return_value=SimpleNamespace(open=open)),patch('sqlite3.connect',side_effect=AssertionError('API must not access SQLite')):
            yield
    def refuse_media(self,*args):args[-1]['status']='UNSUPPORTED'

class DiscoveryTests(ApiHarness):
    def test_authority_default_off_and_exact_camera_before_http(self):
        with self.http():
            self.assertEqual(self.call(token='bad')[0],401)
            self.assertEqual(self.call(binding_guard='b'*64)[0],403)
            self.service.historical_v2=None;self.assertEqual(self.call()[0],404)
            self.assertEqual(self.calls,[])
    def test_empty_and_bad_version(self):
        with self.http():_,r=self.call()
        self.assertEqual(r['status'],'NO_MATCHING_RECORDING');self.assertIsNone(r['result'])
        self.version='0.18.0'
        with self.http():_,r=self.call()
        self.assertEqual(r['status'],'API_VERSION_UNSUPPORTED');self.assertIsNone(r['result'])
    def test_outward_boundaries_order_gap_and_full_file_metadata(self):
        self.row('9.0-aaaaaa',9,10);self.row('10.0-bbbbbb',10,13);self.row('11.0-cccccc',11,14)
        self.row('16.0-dddddd',16,19);self.row('18.0-eeeeee',18,20)
        with self.http(),patch.object(self.coordinator,'_verify_file',side_effect=self.refuse_media):_,r=self.call(start='10',end='18')
        self.assertEqual(r['selection']['order'],['10.0-bbbbbb','11.0-cccccc','16.0-dddddd'])
        self.assertEqual(r['selection']['recordings'][-1]['metadata_interval'],dict(start='16',end='19'))
        self.assertEqual(r['selection']['catalog_gaps'],[dict(start='14',end='16')])
        self.assertEqual(r['coverage'],'UNKNOWN');self.assertEqual(r['continuity'],'UNKNOWN')
        query=parse_qs(urlsplit(self.calls[-1].full_url).query)
        self.assertLess(float(query['after'][0]),10);self.assertGreater(float(query['before'][0]),18)
        self.assertEqual(r['profile'],'api-independent-files-native-b-v1');self.assertEqual(r['catalog']['capability'],api.API_PROFILE);self.assertNotIn('private-test-token',json.dumps(r))
    def test_equal_starts_ambiguous_path_and_duplicate_ids_refused(self):
        self.row();self.row('10.0-bbbbbb')
        with self.http():_,r=self.call()
        self.assertEqual(r['status'],'API_MALFORMED')
        self.rows[1]=dict(self.rows[0])
        with self.http():_,r=self.call()
        self.assertEqual(r['status'],'API_MALFORMED')
    def test_malformed_incomplete_and_excessive_responses(self):
        self.row()
        bad=[b'{}',b'[',b'[{"id":"x","id":"y"}]',json.dumps([{k:v for k,v in self.rows[0].items() if k!='duration'}]).encode(),b' '*((128<<10)+1)]
        for raw in bad:
            with self.http(raw=raw),patch.object(self.coordinator,'_verify_file') as run:_,r=self.call()
            self.assertIn(r['status'],('API_MALFORMED','LIMIT'));run.assert_not_called()
    def test_nine_and_excessive_lists_no_selection_truncation(self):
        for i in range(9):self.row(f'{10+i}.0-aaaaaa',10+i,20+i)
        with self.http(),patch.object(self.coordinator,'_verify_file') as run:_,r=self.call(start='10',end='30')
        self.assertEqual(r['status'],'LIMIT');self.assertEqual(r['reason'],'SELECTION_TOO_LARGE');run.assert_not_called()
        self.rows*=15
        with self.http():_,r=self.call(start='10',end='30')
        self.assertEqual(r['status'],'LIMIT')
    def test_record_disappears_after_discovery(self):
        path=self.row();real=p.ApiProvider.discover
        def discover(provider,*args):
            inventory=real(provider,*args);path.unlink();return inventory
        with self.http(),patch.object(p.ApiProvider,'discover',discover):_,r=self.call()
        self.assertEqual(r['status'],'SOURCE_UNAVAILABLE');self.assertIsNone(r['result']);self.assertEqual(list(self.scratch.iterdir()),[])
    def test_origin_and_redirect_and_request_local_immutability(self):
        for url in ('file:///tmp/a','http://a/path','http://u:p@a','http://a?x=1'):
            with self.assertRaises(ValueError):api.endpoint(url)
        with self.assertRaises(api.ProviderFailure):api._NoRedirect().redirect_request(None,None,None,None,None,None)
        self.row();self.row('11.0-bbbbbb',11,21);real=self.coordinator._verify_file
        def mutate(*args):
            self.rows.clear();args[-1]['status']='UNSUPPORTED'
        with self.http(),patch.object(self.coordinator,'_verify_file',side_effect=mutate):_,r=self.call()
        self.assertEqual(r['selection']['count'],2);self.assertEqual(len(r['files']),2)
        self.assertEqual(len(self.calls),2)
    def test_http_error_truncation_timeout_encoding_and_cancellation(self):
        from urllib.error import URLError
        self.row()
        for error in (URLError('not exposed'),TimeoutError('not exposed')):
            with patch.object(api,'build_opener',return_value=SimpleNamespace(open=lambda *a,**k:(_ for _ in ()).throw(error))):_,r=self.call()
            self.assertEqual(r['status'],'API_UNAVAILABLE');self.assertIsNone(r['result'])
        for headers,expected in (({'Content-Length':'10'},'API_INCOMPLETE_RESPONSE'),({'Content-Encoding':'gzip'},'API_RESPONSE_ENCODING_UNSUPPORTED'),({'Content-Length':str(129<<10)},'LIMIT')):
            response=Reply(b'[]');response.headers=headers
            def open(*args,**kwargs):return response
            # Version response is intentionally mismatched in framing: reject
            # before ever interpreting an empty recording selection.
            with patch.object(api,'build_opener',return_value=SimpleNamespace(open=open)):_,r=self.call()
            self.assertEqual(r['status'],expected);self.assertIsNone(r['result'])
        original=api.ApiProvider._get
        def cancel(provider,target,*args):
            data=original(provider,target,*args)
            if target=='/api/version':self.coordinator.cancel(self.camera,RID)
            return data
        with self.http(),patch.object(api.ApiProvider,'_get',cancel):_,r=self.call()
        self.assertEqual(r['status'],'CANCELLED');self.assertEqual(len(self.calls),1)
    def test_api_uses_available_reads(self):
        self.row()
        class AvailableOnly(Reply):
            def read(self,*args):raise AssertionError('must not fill read(n)')
        def open(request,timeout):
            if urlsplit(request.full_url).path=='/api/version':return AvailableOnly(api.VERSION.encode())
            return AvailableOnly(json.dumps(self.rows).encode())
        with patch.object(api,'build_opener',return_value=SimpleNamespace(open=open)),patch.object(self.coordinator,'_verify_file',side_effect=self.refuse_media):_,r=self.call()
        self.assertEqual(r['status'],'UNSUPPORTED')
        self.assertEqual(r['profile'],'api-whole-single-file-native-b-v1')
    def test_live_binding_revocation_blocks_release(self):
        self.row()
        def revoke(*args):
            self.live.configure((replace(self.live.mappings[0],binding=replace(self.live.mappings[0].binding,revision='rev2')),))
            args[-1]['status']='UNSUPPORTED'
        with self.http(),patch.object(self.coordinator,'_verify_file',side_effect=revoke):code,r=self.call()
        self.assertEqual(code,403)

class NativeApiTests(ApiHarness):
    @contextmanager
    def media_controls(self):
        from test_historical_v2_worker import retained_fixture
        raw,ca=retained_fixture();self.ca.write_bytes(ca)
        for rid,start,nals in [('10.0-aaaaaa',10,raw),('11.0-bbbbbb',11,raw[:-1])]:
            self.row(rid,start,start+10,b''.join(len(n[4:]).to_bytes(4,'big')+n[4:] for n in nals))
        def probe(executable,path,*args):
            data=Path(path).read_bytes();packets=[];offset=0
            while offset<len(data):
                size=4+int.from_bytes(data[offset:offset+4],'big');i=len(packets)
                packets.append(dict(stream_index=0,pos=str(offset),size=str(size),pts=i*3000,dts=i*3000,duration=3000));offset+=size
            return dict(streams=[dict(index=0,codec_name='h264',codec_type='video',codec_tag_string='avc1',nal_length_size='4',time_base='1/90000')],packets=packets)
        with self.http(),patch('nanexus_trust.historical_v2.media._probe',side_effect=probe):yield
    def test_two_real_workers_negatives_and_pending_preserved(self):
        with self.media_controls():_,r=self.call()
        self.assertEqual(r['status'],'RESULT');self.assertEqual((r['processed'],r['unprocessed']),(2,0))
        self.assertEqual(len({f['execution']['attempt'] for f in r['files']}),2)
        self.assertEqual([f['execution']['consumed'] for f in r['files']],[91,90])
        first=self.decoded(r['files'][0]);second=self.decoded(r['files'][1])
        self.assertEqual(sum(o.state.value=='INTEGRITY_NEGATIVE' for o in first[3]),15)
        self.assertEqual(len(second[0].unresolved_refs),16)
        for f in r['files']:
            entry=dict(f);digest=entry.pop('closure_sha256')
            self.assertEqual(digest,p.sha(c.canonical(dict(request_id=RID,camera=self.camera,snapshot=r['selection']['snapshot'],cut=r['selection']['cut'],file=entry))))
            self.assertTrue(f['execution']['reaped'])
        self.assertEqual(list(self.scratch.iterdir()),[])
        (ART/'api-native-summary.json').write_text(json.dumps(dict(bytes=len(c.canonical(r)),negative=15,unresolved=16,executions=[f['execution'] for f in r['files']]),indent=2))
    def test_one_complete_original_file_api_profile(self):
        with self.media_controls():
            self.rows.pop();_,r=self.call()
        self.assertEqual(r['status'],'RESULT');self.assertEqual(r['profile'],'api-whole-single-file-native-b-v1')
        self.assertEqual(r['execution']['consumed'],91)
        self.assertEqual(r['result']['original_file_bytes'],(self.media/'1970-01-01/00/front/00.10.mp4').stat().st_size)
        self.assertEqual(r['selection']['metadata_interval'],dict(start='10',end='20'))
        self.assertEqual(sum(o.state.value=='INTEGRITY_NEGATIVE' for o in self.decoded(r)[3]),15)
    def test_cancel_between_files_and_response_limit(self):
        with self.media_controls(),patch.object(p,'MAX_RESPONSE',4000):_,r=self.call()
        self.assertEqual(r['status'],'LIMIT');self.assertEqual(r['reason'],'RESPONSE_TOO_LARGE');self.assertEqual(r['files'],[])
        self.rows=[]
        real=self.coordinator._verify_file
        def cancel(*args):real(*args);self.coordinator.cancel(self.camera,RID)
        with self.media_controls(),patch.object(self.coordinator,'_verify_file',side_effect=cancel):_,r=self.call()
        self.assertEqual(r['status'],'CANCELLED');self.assertEqual((r['processed'],r['unprocessed']),(1,1))
        self.assertEqual(r['files'][1]['reason'],'CANCELLED')

class ConfigurationTests(ApiHarness):
    def test_explicit_api_configuration_and_captured_default_preserved(self):
        from nanexus_trust.launcher import validate_hv2_config,prototype_coordinator
        value=dict(profile=api.API_PROFILE,origin='http://frigate.test',token_file=str(self.token),media_root=str(self.media),worker=str(WORKER),public_ca=str(self.ca),ffprobe='/usr/bin/ffprobe')
        with patch.object(api,'build_opener',side_effect=AssertionError('no HTTP at configuration')):
            validate_hv2_config(value);coordinator=prototype_coordinator(value,self.scratch);coordinator.close()
        self.assertIsInstance(coordinator.profile,p.ApiProfile)
        value['profile']='0.18.2'
        with self.assertRaises(ValueError):validate_hv2_config(value)

class SavedApiTests(ApiHarness):
    def test_original_saved_mp4_api_to_reader_native(self):
        saved=ROOT/'.local/governance/ai-dlc/evidence/m1-hv2-d-planning/d1.5-artifacts'
        original=json.loads((saved/'source-ledger.json').read_text())['source']
        media=Path(next(k for k in original if k.endswith('.mp4')));ca=Path(next(k for k in original if k.endswith('.pem')))
        before={str(k):hashlib.sha256(k.read_bytes()).hexdigest() for k in (media,ca)}
        self.assertTrue(all(original[k]==digest for k,digest in before.items()))
        self.coordinator.profile=replace(self.coordinator.profile,media_root=media.parents[3],public_ca=ca)
        self.camera='slice3_signed';self.connect()
        rows=json.loads((ROOT.parent/'nanexus-media-signing-gateway/build/m2-slice3/run/recording-rows.json').read_text())
        row=next(r for r in rows if r['id']=='1792359726.0-o9qwbr');self.rows=[{k:row[k] for k in FIELDS}]
        with self.http():code,r=self.call(start='1792359727',end='1792359728')
        self.assertEqual(code,200);self.assertEqual(r['status'],'RESULT',r)
        base,objects,updates,obs=self.decoded(r)
        actual=dict(nals=len(r['result']['source_ledger']),updates=len(updates),finalized=len(obs),verified=sum(o.state.value=='VERIFIED_SIGNING' for o in obs),indeterminate=sum(o.state.value=='INDETERMINATE' for o in obs),tail=len(base.unresolved_refs))
        self.assertEqual(actual,dict(nals=204,updates=205,finalized=188,verified=165,indeterminate=23,tail=16))
        self.assertEqual(r['selection']['metadata_interval']['start'],'1792359726')
        self.assertEqual(r['result']['original_file_bytes'],media.stat().st_size)
        self.assertTrue(base.next_cursor);self.assertTrue(all(o.placement.status.value=='UNPLACED' for o in obs));self.assertEqual(base.coverage[0].state.value,'UNKNOWN')
        self.assertEqual(before,{str(k):hashlib.sha256(k.read_bytes()).hexdigest() for k in (media,ca)})
        self.assertEqual(list(self.scratch.iterdir()),[])
        (ART/'saved-api-summary.json').write_text(json.dumps(dict(actual=actual,source=before,bytes=len(c.canonical(r)),execution=r['execution']),indent=2))
