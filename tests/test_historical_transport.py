import json
import unittest
import threading
from copy import deepcopy
from nanexus_trust.companion.historical_transport import HistoricalTransport, job_document, MAX_DOWNLOAD

TOKEN='x'*48
JOB='1'*32
EVIDENCE='2'*32

def job(state='completed',verdict='VALID'):
    request={'camera':'front','start':'11','end':'19'}
    result={'request':{'camera':{'value':'front'},'interval':{'start':'11','end':'19'}},'verification':verdict,'metadata_coverage':'FULL','requested_coverage':'FULL' if verdict=='VALID' else 'INDETERMINATE','context':{'camera':{'value':'front'},'intervals':[{'start':'10','end':'20'}]},'detail':'private path must not escape'}
    return {'job_id':JOB,'request':request,'state':state,'stage':state,'result':result if state=='completed' else None,'evidence_id':EVIDENCE if state=='completed' else None}
class Service:
    def __init__(self):self.record=job();self.calls=[]
    def _camera(self,camera):
        if camera not in ('front','back'):raise ValueError()
    def submit(self,camera,start,end):self.calls.append(('submit',camera,start,end));self.record=job('queued');self.record['request']={'camera':camera,'start':start,'end':end};return {'job_id':JOB}
    def job(self,identity):self.calls.append(('job',identity));return deepcopy(self.record)
    def download(self,evidence):self.calls.append(('download',evidence));return b'PK\x03\x04fixture'
    def evidence_summary(self,evidence):return {'manifest_sha256':'a'*64,'recognition':'SUPPORTED','result':{'private':'not forwarded'}}
class TransportTests(unittest.TestCase):
    def setUp(self):self.service=Service();self.transport=HistoricalTransport(self.service,TOKEN)
    def call(self,path='',method='GET',body=b'',camera='front',token=TOKEN):return self.transport.historical(method,'/v1/historical/jobs'+path+'?camera='+camera,'Bearer '+token,body)
    def test_authenticated_explicit_submit(self):
        code,data=self.call(method='POST',body=b'{"start":"11","end":"19"}');self.assertEqual(code,202);self.assertEqual(data['state'],'queued');self.assertIsNone(data['verification']);self.assertIn(('submit','front','11','19'),self.service.calls)
    def test_wrong_or_missing_credential_before_service(self):
        self.assertEqual(self.call(token='wrong')[0],401);self.assertEqual(self.service.calls,[])
    def test_bounded_invalid_ranges_and_duplicate_keys(self):
        for body in [b'{}',b'{"start":"19","end":"11"}',b'{"start":11,"end":19}',b'{"start":"11","end":"19","path":"x"}',b'{"start":"11","start":"12","end":"19"}',b'x'*1025]:
            self.assertEqual(self.call(method='POST',body=body)[0],400)
        self.assertEqual(self.service.calls,[])
    def test_query_parameters_not_arbitrary_paths(self):
        for target in ['/v1/historical/jobs/'+JOB+'?camera=front&path=/etc/passwd','http://bad/v1/historical/jobs?camera=front','/v1/historical/jobs?camera=front&camera=back']:
            self.assertEqual(self.transport.historical('GET',target,'Bearer '+TOKEN)[0],400)
    def test_stored_camera_association_job(self):
        self.assertEqual(self.call('/'+JOB,camera='back')[0],404)
    def test_evidence_id_is_not_authority(self):
        self.assertEqual(self.call('/'+JOB+'/evidence/'+EVIDENCE,camera='back')[0],404)
        self.assertEqual(self.call('/'+JOB+'/evidence/'+'3'*32)[0],404)
        self.assertFalse(any(c[0]=='download' for c in self.service.calls))
    def test_four_completed_verdicts_remain_independent(self):
        for verdict in ('VALID','PARTIAL','INVALID','UNKNOWN'):
            self.service.record=job(verdict=verdict);code,data=self.call('/'+JOB);self.assertEqual(code,200);self.assertEqual(data['verification'],verdict);self.assertEqual(data['metadata_coverage'],'FULL');self.assertEqual(data['context'],[{'start':'10','end':'20'}]);self.assertNotIn('private',json.dumps(data));self.assertNotIn('detail',data)
    def test_job_lifecycle_not_media_verdict(self):
        for state in ('accepted','queued','running','failed','interrupted'):
            self.service.record=job(state);code,data=self.call('/'+JOB);self.assertEqual(code,200);self.assertIsNone(data['verification']);self.assertIsNone(data['evidence_id'])
    def test_evidence_summary_and_download(self):
        code,data=self.call('/'+JOB+'/evidence/'+EVIDENCE);self.assertEqual(code,200);self.assertEqual(set(data),{'evidence_id','recognition','manifest_sha256'});self.assertEqual(self.call('/'+JOB+'/evidence/'+EVIDENCE+'/download')[1],b'PK\x03\x04fixture')
    def test_evidence_bound(self):
        self.service.download=lambda e:b'x'*(MAX_DOWNLOAD+1);self.assertEqual(self.call('/'+JOB+'/evidence/'+EVIDENCE+'/download')[0],413)
    def test_internal_failure_not_invalid(self):
        def fail(*a):raise OSError('/private/secret')
        self.service.job=fail;code,data=self.call('/'+JOB);self.assertEqual(code,503);self.assertNotIn('secret',json.dumps(data));self.assertNotIn('INVALID',json.dumps(data))
    def test_malformed_result_fails_closed(self):
        self.service.record['result']['request']['camera']['value']='back';self.assertEqual(self.call('/'+JOB)[0],400)

    def test_only_one_evidence_assembly_can_run(self):
        entered, release = threading.Event(), threading.Event()
        def download(evidence):
            entered.set(); release.wait(1); return b'PK\x03\x04fixture'
        self.service.download = download
        result = []
        thread = threading.Thread(target=lambda: result.append(self.call('/'+JOB+'/evidence/'+EVIDENCE+'/download')))
        thread.start()
        try:
            self.assertTrue(entered.wait(1))
            self.assertEqual(self.call('/'+JOB+'/evidence/'+EVIDENCE+'/download')[0], 503)
        finally:
            release.set(); thread.join(timeout=2)
        self.assertEqual(result[0][0], 200)

class PrototypeRouteTests(unittest.TestCase):
    def test_new_route_auth_closed_body_and_legacy_cap_unchanged(self):
        from nanexus_trust.companion.historical_transport import MAX_JSON
        service=Service();calls=[]
        service.hv2_verify=lambda *args:(calls.append(args) or {'schema':'nanexus-hv2-prototype/1','status':'RESULT','test_payload':'x'*40000})
        transport=HistoricalTransport(service,TOKEN);url='/v1/historical/v2/verify?camera=front'
        body=json.dumps(dict(start='11',end='12',binding_guard='a'*64,request_id='b'*32)).encode()
        self.assertEqual(transport.historical('POST',url,'bad',body)[0],401);self.assertEqual(calls,[])
        self.assertEqual(transport.historical('POST',url,'Bearer '+TOKEN,body)[0],200)
        self.assertEqual(MAX_JSON,32768)
        self.assertEqual(transport.historical('POST',url,'Bearer '+TOKEN,body[:-1]+b',"path":"/private"}')[0],400)
        service.hv2_verify=lambda *args: {'payload':'x'*(4<<20)}
        self.assertEqual(transport.historical('POST',url,'Bearer '+TOKEN,body)[0],413)
