"""Private Live protocol; no external network, credentials or Frigate dependencies."""
from copy import deepcopy
import json
import secrets
import unittest
from unittest.mock import Mock

from nanexus_trust.companion.transport import LiveTransport, live_document, unknown, MAX_RESPONSE


def result(status='ACTIVE'):
    return {'status':status,'reason':{'ACTIVE':'recent_signing_evidence','DEGRADED':'signing_evidence_overdue','UNKNOWN':'mapping_unavailable'}[status],
            'evidence_age_ms':'123.4','observed_at':'2026-10-16T00:00:00Z','last_evidence_at':'2026-10-16T00:00:00Z',
            'session_id':'session-1','mapping_id':'mapping-1','camera':'front','rendition':'main'}


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.token=secrets.token_urlsafe(32)
        self.service=Mock();self.service.live_status.return_value=result();self.service.readiness.return_value={'ready':True}
        self.transport=LiveTransport(self.service,self.token)

    def call(self,target='/v1/live/status?camera=front&stream=main&context=single',method='GET',token=True):
        return self.transport.dispatch(method,target,'Bearer '+self.token if token else None)

    def test_exact_request_and_whitelisted_response(self):
        code,body=self.call();self.assertEqual(code,200);self.assertEqual(body['status'],'ACTIVE')
        self.service.live_status.assert_called_once_with('front','main','single')
        self.assertEqual(set(body),set(unknown()))
        self.assertNotIn(self.token,json.dumps(body))

    def test_missing_and_wrong_token_prevent_service(self):
        self.assertEqual(self.call(token=False)[0],401)
        self.assertEqual(self.transport.dispatch('GET','/readyz','Bearer wrong')[0],401)
        self.service.live_status.assert_not_called();self.service.readiness.assert_not_called()

    def test_three_statuses(self):
        for status in ('ACTIVE','DEGRADED','UNKNOWN'):
            with self.subTest(status=status):
                self.service.live_status.return_value=result(status)
                self.assertEqual(self.call()[1]['status'],status)

    def test_missing_and_ambiguous_mapping(self):
        from test_integration import POLICY
        from nanexus_trust.companion import LiveScheduler, PlaybackMapping, CompanionService
        from nanexus_trust.live import Binding
        from nanexus_trust.contracts import CameraId,RenditionId
        b=Binding(CameraId('front'),RenditionId('main'),'source','revision',POLICY)
        m=PlaybackMapping('front','main','single',b)
        for mappings in ((),(m,m)):
            scheduler=LiveScheduler(mappings,lambda b: self.fail('ambiguous source opened'))
            try:
                self.transport.service=CompanionService(scheduler,Mock(),('front',))
                self.assertEqual(self.call()[1]['status'],'UNKNOWN')
            finally:scheduler.close()

    def test_malformed_and_extra_parameters(self):
        for query in ('','camera=x','camera=x&stream=y&context=z&url=http://evil',
                      'camera=x&stream=y&context=','camera=../x&stream=y&context=z',
                      'camera=x&camera=y&stream=y&context=z','camera=x&stream='+('a'*129)+'&context=z'):
            with self.subTest(query=query):self.assertEqual(self.call('/v1/live/status?'+query)[0],400)
        self.service.live_status.assert_not_called()

    def test_methods_and_no_other_routes(self):
        for method in ('POST','PUT','PATCH','DELETE','HEAD','OPTIONS'):
            self.assertEqual(self.call(method=method)[0],405)
        for path in ('/v1/cameras','/v1/historical/jobs','/v1/evidence/x','/config'):
            self.assertEqual(self.call(path)[0],404)

    def test_ready_not_signing_state(self):
        self.assertEqual(self.call('/readyz'),(200,{'ready':True}))
        self.service.readiness.return_value={'ready':False}
        self.assertEqual(self.call('/readyz'),(503,{'ready':False}))

    def test_exception_sanitized(self):
        self.service.live_status.side_effect=RuntimeError('/private token=secret')
        self.assertEqual(self.call()[1],unknown())
        self.service.readiness.side_effect=RuntimeError('secret')
        self.assertEqual(self.call('/readyz'),(503,{'ready':False}))

    def test_invalid_and_oversized_service_response(self):
        for key,value in [('camera','/private/path'),('status','VALID'),('reason','secret'),
                          ('session_id','x'*10000),('evidence_age_ms','NaN'),('observed_at','not-time')]:
            data=result();data[key]=value;self.service.live_status.return_value=data
            with self.subTest(key=key):self.assertEqual(self.call()[1]['status'],'UNKNOWN')
        self.assertLess(len(json.dumps(self.call()[1]).encode()),MAX_RESPONSE)

    def test_source_unavailable_and_rendition_missing(self):
        data=result('UNKNOWN');data.update(camera=None,rendition=None,mapping_id=None,session_id=None,evidence_age_ms=None)
        self.service.live_status.return_value=data;self.assertEqual(self.call()[1]['status'],'UNKNOWN')
        data=result();data['session_id']=None
        self.service.live_status.return_value=data;self.assertEqual(self.call()[1]['status'],'UNKNOWN')

    def test_existing_decimal_zero_serialization(self):
        self.service.live_status.return_value=result()|{'evidence_age_ms':'0E-9'}
        body=self.call()[1]
        self.assertEqual(body['status'],'ACTIVE')
        self.assertEqual(body['age_ms'],'0.000000000')

    def test_token_shape_required(self):
        with self.assertRaises(ValueError):LiveTransport(self.service,'short')
