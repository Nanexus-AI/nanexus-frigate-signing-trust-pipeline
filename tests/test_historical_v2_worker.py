"""Synthetic D1 fault tests only. No media, native B, service or live authority."""
from dataclasses import replace
import ctypes,hashlib,json,os,signal,struct,subprocess,sys,threading,time,unittest
from pathlib import Path
from nanexus_trust.historical_v2.worker_protocol import Attempt,Decoder,ProtocolError,encode,sha
from nanexus_trust.historical_v2.worker_supervisor import Supervisor
ROOT=Path(__file__).resolve().parents[1]
EXE=ROOT/'.local/governance/ai-dlc/evidence/m1-hv2-d-planning/d1.5-artifacts/worker_child'
class SyntheticAuthority:
 def __init__(self):self.allowed=True;self.calls=0
 def authorize(self,spec,checkpoint):
  self.calls+=1
  return self.allowed and spec.principal=='test-principal' and spec.camera=='test-camera' and spec.binding=='test-binding' and spec.request=='request' and spec.source=='source' and spec.cut=='synthetic-captured-cut'
class SyntheticSource:
 def __init__(self):self.data=(b'\0\0\0\1\x67same',b'\0\0\0\1\x67same');self.generation='generation';self.reads=0
 def describe(self):return dict(source='source',generation=self.generation,digest=sha(b''.join(self.data)),bytes=sum(map(len,self.data)),count=len(self.data))
 def occurrences(self):return [(f'occurrence-{i}',len(d),sha(d)) for i,d in enumerate(self.data)]
 def read(self,i):self.reads+=1;return self.generation,f'occurrence-{i}',self.data[i]
class WorkerTests(unittest.TestCase):
 def setUp(self):
  if not EXE.is_file():self.skipTest('D1 synthetic executable unavailable; no implicit native build')
  self.source=SyntheticSource();self.authority=SyntheticAuthority();self.trust=b'public synthetic trust bytes'
  d=self.source.describe();self.spec=Attempt('attempt','request','run','test-principal','test-camera','test-binding','source',d['generation'],d['digest'],d['bytes'],d['count'],sha(self.trust),len(self.trust));self.supervisor=Supervisor(EXE,self.authority)
 def run_child(self,**kw):return self.supervisor.run(self.spec,self.source,self.trust,**kw)
 def assert_reaped(self,r):
  self.assertTrue(r.reaped)
  with self.assertRaises(ProcessLookupError):os.kill(r.pid,0)
 def test_success_identity_and_no_signing_claim(self):
  r=self.run_child();self.assertEqual(r.state,'EXITED_SUCCESSFULLY');self.assertEqual(r.consumed,2);self.assertEqual(r.completeness,'INCOMPLETE');self.assertIsNone(r.signing);self.assert_reaped(r)
 def test_authority_denied_before_spawn(self):
  for field in ('camera','binding','principal','request','source','cut'):
   r=self.supervisor.run(replace(self.spec,**{field:'wrong'}),self.source,self.trust);self.assertEqual(r.state,'REVOKED');self.assertIsNone(r.pid)
  self.assertEqual(self.source.reads,0)
  with self.assertRaises(TypeError):Supervisor(EXE,lambda *_:True)
 def test_source_and_trust_admission(self):
  for spec in (replace(self.spec,generation='other'),replace(self.spec,source_digest='0'*64)):
   r=self.supervisor.run(spec,self.source,self.trust);self.assertEqual(r.state,'FAILED');self.assertIsNone(r.pid)
  r=self.supervisor.run(self.spec,self.source,b'wrong trust');self.assertIsNone(r.pid)
 def test_substitution_after_admission(self):
  def wrong(i):self.source.generation='substituted';return self.source.generation,'occurrence-0',self.source.data[i]
  self.source.read=wrong;r=self.run_child();self.assertEqual(r.state,'FAILED');self.assert_reaped(r)
 def test_duplicate_and_conflicting_request(self):
  r=self.run_child();same=self.run_child();self.assertEqual(r,same)
  conflict=self.supervisor.run(replace(self.spec,run='different'),self.source,self.trust);self.assertEqual(conflict.state,'FAILED');self.assertEqual(conflict.error,'attempt-conflict')
 def test_busy_zero_queue_cancel_and_escalation(self):
  event=threading.Event();box=[];thread=threading.Thread(target=lambda:box.append(self.run_child(cancel=event,_fault='hang')));thread.start()
  deadline=time.monotonic()+1
  while not self.supervisor._lock.locked() and time.monotonic()<deadline:time.sleep(.002)
  self.assertEqual(self.run_child().state,'BUSY');time.sleep(.05);event.set();thread.join(2);self.assertFalse(thread.is_alive());self.assertEqual(box[0].state,'CANCELLED');self.assertEqual(box[0].exit_code,-signal.SIGKILL);self.assertIn('TERMINATING',box[0].history);self.assert_reaped(box[0])
 def test_revocation_while_native_hangs(self):
  box=[];thread=threading.Thread(target=lambda:box.append(self.run_child(_fault='hang')));thread.start();time.sleep(.05);self.authority.allowed=False;thread.join(2);self.assertFalse(thread.is_alive());self.assertEqual(box[0].state,'REVOKED');self.assertEqual(box[0].exit_code,-signal.SIGKILL);self.assert_reaped(box[0])
 def test_crash_hang_memory_and_floods(self):
  for fault in ('crash','hang','memory','output','stderr','truncated'):
   with self.subTest(fault=fault):
    sup=Supervisor(EXE,self.authority,timeout=.8,no_progress=.15);r=sup.run(self.spec,self.source,self.trust,_fault=fault);self.assertNotEqual(r.state,'EXITED_SUCCESSFULLY');self.assertIsNone(r.signing);self.assert_reaped(r)
    if fault=='crash':self.assertEqual(r.state,'INTERRUPTED')
    if fault in ('hang','memory','stderr'):self.assertEqual(r.state,'RESOURCE_STOPPED')
 def test_python_protocol_rejections(self):
  good=encode('FINISH_INPUT','attempt',0)
  for raw in (struct.pack('!I',2049),struct.pack('!I',0),struct.pack('!I',2)+b'{}'):
   dec=Decoder();dec.feed(raw)
   with self.assertRaises(ProtocolError):dec.pop()
  with self.assertRaises(ProtocolError):encode('NAL_CHUNK','attempt',0,b'x'*65537,ordinal=0,offset=0)
  with self.assertRaises(ProtocolError):encode('STOP','attempt',0,reason='cancel',extra='no')
  with self.assertRaises(ProtocolError):encode('FINISH_INPUT','attempt',True)
  header=b'{"protocol":"hv2-d1.5/1","type":"FINISH_INPUT","attempt":"attempt","seq":0,"seq":0,"payload_bytes":0}'
  d=Decoder();d.feed(struct.pack('!I',len(header))+header)
  with self.assertRaises(ProtocolError):d.pop()
  d=Decoder();d.feed(good[:-1]);self.assertIsNone(d.pop())
 def test_incremental_chunking_and_trust_quota(self):
  self.source.data=(b'\0\0\0\1\x67'+b'x'*70000,)
  d=self.source.describe();spec=replace(self.spec,source_digest=d['digest'],source_bytes=d['bytes'],nal_count=d['count'])
  r=self.supervisor.run(spec,self.source,self.trust);self.assertEqual(r.state,'EXITED_SUCCESSFULLY');self.assertEqual(r.consumed,1)
 def test_native_protocol_conflicts_and_truncation(self):
  for raw in (encode('FINISH_INPUT','attempt',0),encode('BEGIN','attempt',0,request='r',run='r',source='s',generation='g',source_digest='0'*64,source_bytes=1,nal_count=1,trust_digest='0'*64,trust_bytes=1,camera='c',binding='b',profile='synthetic-hv2-d1-v1',cut='cut',policy='synthetic-single-source-v1')[:-1]):
   p=subprocess.Popen([str(EXE),str(os.getpid()),'normal'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True)
   out,err=p.communicate(raw,timeout=2);self.assertNotEqual(p.returncode,0);self.assertLess(len(out)+len(err),4096)
 def test_native_closed_fields_sequences_and_chunk_offsets(self):
  begin=encode('BEGIN','attempt',0,request='r',run='r',source='s',generation='g',source_digest=sha(b'abcde'),source_bytes=5,nal_count=1,trust_digest=sha(b'x'),trust_bytes=1,camera='c',binding='b',profile='synthetic-hv2-d1-v1',cut='cut',policy='synthetic-single-source-v1')
  trust=encode('TRUST_CHUNK','attempt',1,b'x',offset=0)+encode('TRUST_END','attempt',2)
  nal=encode('NAL_BEGIN','attempt',3,ordinal=0,token='token'.encode().hex(),size=5,digest=sha(b'abcde'))
  header=json.loads(begin[4:]);header['unknown']='no';text=json.dumps(header).encode();extra=struct.pack('!I',len(text))+text
  quoted=dict(header);quoted.pop('unknown');quoted['seq']='0';text=json.dumps(quoted).encode();wrong_type=struct.pack('!I',len(text))+text
  values=[extra,wrong_type,struct.pack('!I',2049),begin+encode('TRUST_CHUNK','attempt',1,b'x',offset=1),begin+encode('TRUST_CHUNK','attempt',2,b'x',offset=0),begin+trust+nal+encode('NAL_CHUNK','attempt',4,b'abcde',ordinal=0,offset=1)]
  for raw in values:
   p=subprocess.run([str(EXE),str(os.getpid()),'normal'],input=raw,capture_output=True,timeout=2,start_new_session=True);self.assertNotEqual(p.returncode,0);self.assertLess(len(p.stdout)+len(p.stderr),4096)
 def test_native_attempt_identity_cannot_rebind_startup_name(self):
  data=b'\0\0\0\1\x67';begin=encode('BEGIN','startup',0,request='r',run='r',source='s',generation='g',source_digest=sha(data),source_bytes=len(data),nal_count=1,trust_digest=sha(b'x'),trust_bytes=1,camera='c',binding='b',profile='synthetic-hv2-d1-v1',cut='cut',policy='synthetic-single-source-v1')
  wrong=begin+encode('TRUST_CHUNK','other',1,b'x',offset=0)+encode('TRUST_END','other',2)+encode('NAL_BEGIN','other',3,ordinal=0,token='t'.encode().hex(),size=len(data),digest=sha(data))+encode('NAL_CHUNK','other',4,data,ordinal=0,offset=0)+encode('NAL_END','other',5,ordinal=0)+encode('FINISH_INPUT','other',6)
  p=subprocess.run([str(EXE),str(os.getpid()),'normal'],input=wrong,capture_output=True,timeout=2,start_new_session=True);self.assertNotEqual(p.returncode,0)
 def test_guard_effectiveness(self):
  import tempfile
  with tempfile.TemporaryDirectory() as root:
   path=Path(root)/'harmless';path.write_text('owned harmless sentinel');p=subprocess.run([str(EXE),str(os.getpid()),'check',str(path)],capture_output=True,timeout=2,start_new_session=True);self.assertEqual(p.returncode,0);d=Decoder();d.feed(p.stdout);h,_=d.pop();self.assertEqual(h['guard'],'landlock-seccomp-pdeath');self.assertGreaterEqual(h['abi'],1);self.assertEqual(path.read_text(),'owned harmless sentinel')
 def test_guard_observed_from_parent_and_only_ipc_fds(self):
  p=subprocess.Popen([str(EXE),str(os.getpid()),'hang'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True,close_fds=True)
  try:
   import selectors
   with selectors.DefaultSelector() as sel:
    sel.register(p.stdout,selectors.EVENT_READ);self.assertTrue(sel.select(2))
   n=int.from_bytes(p.stdout.read(4),'big');self.assertLessEqual(n,2048);self.assertEqual(json.loads(p.stdout.read(n))['type'],'READY')
   status=dict(line.split(':',1) for line in Path(f'/proc/{p.pid}/status').read_text().splitlines() if ':' in line)
   self.assertEqual(status['NoNewPrivs'].strip(),'1');self.assertEqual(status['Seccomp'].strip(),'2');self.assertEqual(sorted(os.listdir(f'/proc/{p.pid}/fd')),['0','1','2']);self.assertEqual(os.getpgid(p.pid),p.pid)
  finally:
   os.killpg(p.pid,signal.SIGKILL);p.wait(timeout=1);p.stdin.close();p.stdout.close();p.stderr.close()
 def _parent_death_check(self,native=False):
  # Subreaper is local to this test process; permits reaping the deliberate orphan.
  libc=ctypes.CDLL(None,use_errno=True);self.assertEqual(libc.prctl(36,1,0,0,0),0)
  p=subprocess.Popen([sys.executable,'-B',str(ROOT/'tests/helpers/historical_v2_worker_faults.py'),str(EXE)]+(['native'] if native else []),stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
  try:
   if native:
    _,ca=retained_fixture();p.stdin.write(json.dumps({'public_ca_hex':ca.hex()}).encode()+b'\n');p.stdin.flush()
   import selectors
   with selectors.DefaultSelector() as sel:
    sel.register(p.stdout,selectors.EVENT_READ);self.assertTrue(sel.select(2));pid=int(p.stdout.readline())
   if native:
    status=Path(f'/proc/{pid}/status').read_text();self.assertIn('NoNewPrivs:\t1',status);self.assertIn('Seccomp:\t2',status);self.assertEqual(sorted(os.listdir(f'/proc/{pid}/fd')),['0','1','2'])
   p.stdin.write(b'x');p.stdin.flush();p.wait(timeout=2)
   end=time.monotonic()+2;status=None
   while time.monotonic()<end:
    child,got=os.waitpid(pid,os.WNOHANG)
    if child:status=got;break
    time.sleep(.005)
   self.assertIsNotNone(status);self.assertTrue(os.WIFSIGNALED(status));self.assertEqual(os.WTERMSIG(status),signal.SIGKILL)
  finally:
   if p.poll() is None:p.kill();p.wait()
   p.stdin.close();p.stdout.close();p.stderr.close();libc.prctl(36,0,0,0,0)
 def test_parent_death_kills_and_adopted_child_reaped(self):self._parent_death_check()
 def test_native_parent_death_with_configured_b(self):self._parent_death_check(native=True)
 def test_callback_error_is_operational_and_reaped(self):
  def failing(i):raise RuntimeError('do not expose callback detail')
  self.source.read=failing;r=self.run_child();self.assertEqual(r.state,'FAILED');self.assertEqual(r.error,'adapter-failure');self.assert_reaped(r)
 def test_no_live_or_legacy_mutable_dependencies(self):
  from nanexus_trust.historical_v2 import worker_supervisor
  text=Path(worker_supervisor.__file__).read_text();self.assertNotIn('companion.',text);self.assertNotIn('historical.service',text)

class NativeSource(SyntheticSource):
 def __init__(self, data, tokens=None):
  self.data=tuple(data);self.tokens=tuple(tokens or ('native-'+str(i) for i in range(len(data))));self.generation='generation';self.reads=0
 def occurrences(self):return [(t,len(d),sha(d)) for t,d in zip(self.tokens,self.data)]
 def read(self,i):self.reads+=1;return self.generation,self.tokens[i],self.data[i]

def retained_fixture():
 import re
 root=ROOT/'.local/governance/ai-dlc/evidence/m1-hv2-b-extraction/artifacts'
 raw=(root/'failing-h264-low.es').read_bytes();ca=(root/'test-ca.pem').read_bytes()
 assert sha(raw)=='08064001b9e482adbe7af5c520dd81e7f6f1cb27deef46d7edd7f6fbe05c6152'
 assert sha(ca)=='dce908933ac27249edbab18491f6162188ec6259a1444c7d59ab0f97acf481e8'
 matches=list(re.finditer(b'\x00\x00\x00?\x01',raw))
 return [b'\0\0\0\1'+raw[m.end():matches[i+1].start() if i+1<len(matches) else len(raw)] for i,m in enumerate(matches)],ca

def native_spec(source,ca):
 d=source.describe()
 return Attempt('native-attempt','request','run','test-principal','test-camera','test-binding','source',d['generation'],d['digest'],d['bytes'],d['count'],sha(ca),len(ca),profile='native-hv2-d1.5-v1',policy='native-single-source-v1')

class NativeWorkerTests(unittest.TestCase):
 def setUp(self):
  self.assertTrue(EXE.is_file(),'native artifact required; no implicit build')
  self.nals,self.ca=retained_fixture();self.authority=SyntheticAuthority()
 def execute(self,data=None,ca=None,**kw):
  source=NativeSource(self.nals if data is None else data);ca=self.ca if ca is None else ca
  spec=native_spec(source,ca);r=Supervisor(EXE,self.authority,timeout=5,no_progress=1).run(spec,source,ca,**kw)
  return r,source,spec
 def test_gate_a_guarded_native_readiness(self):
  from nanexus_trust.historical_v2.worker_updates import decode
  r,_,_=self.execute();self.assertEqual(r.state,'EXITED_SUCCESSFULLY',r);self.assertTrue(r.reaped);self.assertEqual(r.consumed,91)
  updates=[decode(raw) for raw in r.updates];self.assertEqual(len(updates),92);self.assertEqual(sum(u['fresh_report'] for u in updates),5)
  latest={i['received_ordinal']:i for u in updates for i in u['items']}
  self.assertEqual(sum(i['state']==1 for i in latest.values()),15)
  self.assertTrue(any(i['state']==0 for i in latest.values()));self.assertEqual(updates[-1]['phase'],'finish');self.assertEqual(updates[-1]['stop'],4)
 def test_native_open_tail_is_provisional(self):
  from nanexus_trust.historical_v2.worker_updates import decode
  r,_,_=self.execute(self.nals[:-1]);self.assertEqual(r.state,'EXITED_SUCCESSFULLY',r)
  tail=decode(r.updates[-1]);self.assertTrue(tail['items']);self.assertTrue(all(not i['finalized'] and i['state']==3 for i in tail['items']));self.assertEqual(r.completeness,'INCOMPLETE')
 def test_wrong_and_malformed_and_missing_trust(self):
  import base64
  from nanexus_trust.historical_v2.worker_updates import decode
  # Valid ASN.1 public CA with a deliberately different RSA modulus; no key,
  # signing operation or claim that this is a valid issuing identity.
  der=bytearray(base64.b64decode(b''.join(self.ca.splitlines()[1:-1])));at=der.index(b'\x02\x82\x01\x01\x00')+5+32;der[at]^=1
  wrong=b'-----BEGIN CERTIFICATE-----\n'+base64.encodebytes(der)+b'-----END CERTIFICATE-----\n'
  r,_,_=self.execute(ca=wrong);self.assertEqual(r.state,'EXITED_SUCCESSFULLY',r)
  self.assertFalse(any(i['state']==0 for raw in r.updates for i in decode(raw)['items']))
  r,_,_=self.execute(ca=b'not PEM');self.assertEqual(r.state,'FAILED');self.assertTrue(r.reaped);self.assertEqual(r.updates,());self.assertEqual(r.native_error,(5,3))
  source=NativeSource(self.nals);spec=native_spec(source,self.ca)
  r=Supervisor(EXE,self.authority).run(spec,source,b'');self.assertIsNone(r.pid);self.assertEqual(r.updates,())
  with self.assertRaises(ProtocolError):native_spec(source,b'')
  r=Supervisor(EXE,self.authority).run(replace(spec,trust_bytes=32769,trust_digest=sha(b'x'*32769)),source,b'x'*32769);self.assertIsNone(r.pid)
 def test_native_feed_finish_hangs_crashes_and_revocation(self):
  for fault in ('feed-hang','finish-hang','feed-crash','finish-crash'):
   r,_,_=self.execute([b'\0\0\0\1\x67same'],_fault=fault)
   self.assertTrue(r.reaped);self.assertNotEqual(r.state,'EXITED_SUCCESSFULLY')
   if fault.endswith('hang'):self.assertEqual(r.state,'RESOURCE_STOPPED');self.assertEqual(r.exit_code,-signal.SIGKILL)
   else:self.assertEqual(r.state,'INTERRUPTED')
  event=threading.Event();box=[]
  thread=threading.Thread(target=lambda:box.append(self.execute([b'\0\0\0\1\x67same'],cancel=event,_fault='feed-hang')[0]));thread.start();time.sleep(.05);event.set();thread.join(2)
  self.assertFalse(thread.is_alive());self.assertEqual(box[0].state,'CANCELLED');self.assertTrue(box[0].reaped)
  box=[];thread=threading.Thread(target=lambda:box.append(self.execute([b'\0\0\0\1\x67same'],_fault='feed-hang')[0]));thread.start();time.sleep(.05);self.authority.allowed=False;thread.join(2)
  self.assertFalse(thread.is_alive());self.assertEqual(box[0].state,'REVOKED');self.assertTrue(box[0].reaped)
 def test_native_utf8_tokens_and_identical_payloads(self):
  from nanexus_trust.historical_v2.worker_updates import decode,token_text
  source=NativeSource([b'\0\0\0\1\x67same']*2,['occurrence-é','occurrence-漢'])
  r=Supervisor(EXE,self.authority).run(native_spec(source,self.ca),source,self.ca);self.assertEqual(r.state,'EXITED_SUCCESSFULLY',r)
  self.assertEqual([token_text(i['source_token_hex']) for i in decode(r.updates[-1])['items']],list(source.tokens))

 def test_cpp_complete_serializer_nondefault_fields(self):
  from nanexus_trust.historical_v2.worker_updates import Reassembly,decode
  p=subprocess.run([str(EXE),str(os.getpid()),'transport-check'],capture_output=True,timeout=2,start_new_session=True)
  self.assertEqual(p.returncode,0);d=Decoder();r=Reassembly();result=None
  at=0
  while at<len(p.stdout):
   n=int.from_bytes(p.stdout[at:at+4],'big');h=json.loads(p.stdout[at+4:at+4+n]);size=4+n+h['payload_bytes'];payload=p.stdout[at+4+n:at+size];at+=size
   if h['type']!='READY':result=r.accept(h,payload) or result
  u=decode(result);i=u['items'][0];self.assertEqual((u['consumed'],u['generating_feed_ordinal'],i['revision'],i['raw_status']),(8,7,3,'N'))
  self.assertEqual(i['axes'],dict(authenticity=2,provenance=3,combined=0,key_changed=True,raw_signing_start=-9,raw_signing_end=20));self.assertEqual(u['missing'],[dict(left_received=1,right_received=3,report_generation=2)])
 def test_native_input_error_does_not_consume_or_become_negative(self):
  from nanexus_trust.historical_v2.worker_updates import decode
  r,_,_=self.execute([b'\0\0\0\1\x67a\0\0\0\1\x67b'])
  self.assertEqual(r.state,'FAILED');self.assertEqual(r.consumed,0);self.assertTrue(r.reaped)
  u=decode(r.updates[-1]);self.assertEqual((u['phase'],u['stop'],u['error'],u['consumed']),('error',2,1,0));self.assertEqual(u['items'],[])
