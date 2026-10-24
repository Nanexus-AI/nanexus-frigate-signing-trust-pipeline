"""Controlled clocks/timers: no real delay, network, media or Worker required."""
import io
from email.message import Message
import json
import socket
import threading
import unittest
from http.server import BaseHTTPRequestHandler
from types import SimpleNamespace
from unittest.mock import patch
from nanexus_trust.companion import historical_transport as h, transport as live
from nanexus_trust.historical_v2.prototype import PrototypeFailure

TOKEN='x'*48
BODY=dict(binding_guard='a'*64,request_id='b'*32,start='11',end='12')
URL='/v1/historical/v2/verify?camera=front'
class Timer:
    created=[]
    def __init__(self,seconds,callback,args=()):
        self.seconds,self.callback,self.args=seconds,callback,args;self.cancelled=False;self.created.append(self)
    def start(self):pass
    def cancel(self):self.cancelled=True
    def fire(self):self.callback(*self.args) # Also model a callback already queued before cancel.

class Service:
    def __init__(self):self.events=[];self.denied=False
    def _hv2_camera(self,camera,guard):
        self.events.append('authorization')
        if self.denied:raise PrototypeFailure('UNAUTHORIZED')
    def hv2_verify(self,*args):self.events.append('work');return {'schema':'nanexus-hv2-prototype/1','status':'RESULT'}
    def hv2_cancel(self,*args):return {'schema':'nanexus-hv2-prototype/1','status':'CANCELLATION_REQUESTED'}

class LifetimeTests(unittest.TestCase):
    def setUp(self):Timer.created=[];self.now=0;self.closed=[]
    def handler(self):
        handler=h._HistoricalHandler.__new__(h._HistoricalHandler)
        handler.connection=SimpleNamespace(shutdown=lambda mode:self.closed.append(mode))
        return handler
    def run_handler(self,work,live_handler=False):
        handler=self.handler() if not live_handler else live._Handler.__new__(live._Handler)
        handler.connection=SimpleNamespace(shutdown=lambda mode:self.closed.append(mode))
        with patch.object(h.threading,'Timer',Timer),patch.object(h.time,'monotonic',lambda:self.now),patch.object(BaseHTTPRequestHandler,'handle',lambda _:work(handler)):
            handler.handle()
        return handler
    def call(self,service,callback,body=BODY,token=TOKEN,url=URL):
        return h.HistoricalTransport(service,TOKEN).historical('POST',url,'Bearer '+token,json.dumps(body).encode(),_admit_verify=callback)
    def test_admitted_request_outlives_old_guard_and_has_finite_delivery_budget(self):
        service=Service()
        def work(handler):
            def verify(*args):
                self.now=3;Timer.created[0].fire();self.assertEqual(self.closed,[])
                self.assertEqual(Timer.created[-1].seconds,65)
                return {'schema':'nanexus-hv2-prototype/1','status':'RESULT'}
            service.hv2_verify=verify
            self.assertEqual(self.call(service,handler._admit_verify)[0],200)
        self.run_handler(work)
        self.assertEqual([t.seconds for t in Timer.created],[2,65]);self.assertTrue(all(t.cancelled for t in Timer.created))
    def test_final_guard_still_shuts_down_stalled_delivery(self):
        def work(handler):
            self.assertTrue(handler._admit_verify());Timer.created[-1].fire()
            self.assertEqual(self.closed,[socket.SHUT_RDWR])
        self.run_handler(work)
    def test_expiry_wins_race_and_prevents_work(self):
        service=Service()
        def work(handler):
            Timer.created[0].fire()
            self.assertEqual(self.call(service,handler._admit_verify)[0],404)
        self.run_handler(work);self.assertNotIn('work',service.events);self.assertEqual(len(Timer.created),1)
    def test_elapsed_admission_rejected_even_if_timer_callback_is_delayed(self):
        def work(handler):self.now=2;self.assertFalse(handler._admit_verify())
        self.run_handler(work);self.assertEqual(len(Timer.created),1)
    def test_unauthenticated_invalid_and_unauthorized_never_extend(self):
        for body,token,denied,code in [(BODY,'bad',False,401),(BODY|{'start':'bad'},TOKEN,False,400),(BODY|{'end':'1000'},TOKEN,False,413),(BODY,TOKEN,True,403),(BODY|{'path':'/private'},TOKEN,False,400)]:
            with self.subTest(code=code):
                Timer.created=[];service=Service();service.denied=denied
                self.run_handler(lambda handler:self.assertEqual(self.call(service,handler._admit_verify,body,token)[0],code))
                self.assertEqual(len(Timer.created),1);self.assertNotIn('work',service.events)
    def test_oversized_raw_body_and_cancel_do_not_extend(self):
        service=Service();transport=h.HistoricalTransport(service,TOKEN);calls=[]
        self.assertEqual(transport.historical('POST',URL,'Bearer '+TOKEN,b'x'*1025,_admit_verify=lambda:calls.append(1))[0],400)
        body={k:v for k,v in BODY.items() if k not in ('start','end')}
        self.assertEqual(self.call(service,lambda:calls.append(1),body,url=URL.replace('/verify','/cancel'))[1]['status'],'CANCELLATION_REQUESTED')
        self.assertEqual(calls,[])
    def test_live_and_nonadmitted_historical_keep_two_seconds(self):
        for live_handler in (False,True):
            Timer.created=[];self.closed=[]
            self.run_handler(lambda _:Timer.created[0].fire(),live_handler)
            self.assertEqual([t.seconds for t in Timer.created],[2]);self.assertEqual(self.closed,[socket.SHUT_RDWR])
    def test_http_malformed_oversized_truncated_and_stalled_admission_stays_short(self):
        valid=json.dumps(BODY).encode()
        for size,raw,content,transfer in [(1025,valid,'application/json',False),(len(valid),valid[:-1],'application/json',False),(len(valid),valid,'text/plain',False),(len(valid),valid,'application/json',True),(len(valid),None,'application/json',False)]:
            with self.subTest(size=size,content=content,transfer=transfer):
                Timer.created=[];replies=[]
                def work(handler):
                    handler.path=URL;handler.command='POST';handler.headers=Message()
                    for key,value in {'Authorization':'Bearer '+TOKEN,'Content-Length':str(size),'Content-Type':content}.items():handler.headers[key]=value
                    if transfer:handler.headers['Transfer-Encoding']='chunked'
                    handler.rfile=io.BytesIO(raw) if raw is not None else SimpleNamespace(read=lambda n:(_ for _ in ()).throw(TimeoutError()))
                    handler._reply=lambda code,data:replies.append((code,data))
                    handler.server=SimpleNamespace(transport=h.HistoricalTransport(Service(),TOKEN))
                    handler.do_POST()
                self.run_handler(work)
                self.assertEqual(replies[0][0],400);self.assertEqual([t.seconds for t in Timer.created],[2])
    def test_actual_http_dispatch_extends_only_valid_verify(self):
        replies=[];service=Service()
        def work(handler):
            raw=json.dumps(BODY).encode();handler.path=URL;handler.command='POST';handler.headers=Message()
            for k,v in {'Authorization':'Bearer '+TOKEN,'Content-Length':str(len(raw)),'Content-Type':'application/json'}.items():handler.headers[k]=v
            handler.rfile=io.BytesIO(raw);handler.wfile=io.BytesIO();handler.server=SimpleNamespace(transport=h.HistoricalTransport(service,TOKEN))
            handler.send_response=lambda code:replies.append(code);handler.send_header=lambda *args:None;handler.end_headers=lambda:None
            handler.do_POST();self.assertEqual(json.loads(handler.wfile.getvalue())['status'],'RESULT')
        self.run_handler(work);self.assertEqual(replies,[200]);self.assertEqual([t.seconds for t in Timer.created],[2,65])
    def test_cleanup_invalidates_already_queued_callbacks(self):
        handler=self.run_handler(lambda _:None);Timer.created[0].fire();self.assertEqual(self.closed,[])
    def test_authorization_precedes_extension_and_work(self):
        service=Service()
        def admit():service.events.append('admitted');return True
        self.assertEqual(self.call(service,admit)[0],200);self.assertEqual(service.events,['authorization','admitted','work'])

if __name__=='__main__':unittest.main()
