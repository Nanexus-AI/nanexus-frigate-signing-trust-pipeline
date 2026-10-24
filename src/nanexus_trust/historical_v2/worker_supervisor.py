"""Private offline D1.5 supervision. No service wiring or durable publication."""
from dataclasses import dataclass, asdict
import hashlib
import itertools
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import threading
import time
from .worker_protocol import Attempt, Decoder, ProtocolError, encode, sha, MAX_INPUT, MAX_OUTPUT, MAX_HEADER, MAX_PAYLOAD, CONFIG_DIGEST
from .worker_updates import Admission, Reassembly, token_hex

class Denied(Exception):pass
class ResourceStop(Exception):pass
class Cancelled(Exception):pass
class NativeStop(Exception):pass

@dataclass(frozen=True)
class Outcome:
 state: str
 history: tuple
 pid: int | None
 reaped: bool
 consumed: int = 0
 completeness: str = 'INCOMPLETE'
 signing: None = None
 error: str | None = None
 exit_code: int | None = None
 updates: tuple[bytes, ...] = ()
 configuration: str | None = None
 native_error: tuple[int,int] | None = None

class Supervisor:
 """Trusted deployment-owned executable; callers cannot supply command/path flags.

 Authority must implement authorize(Attempt, checkpoint)->bool. Synthetic tests
 name their test double explicitly; production principal integration is deferred.
 Source adapter owns describe/occurrences/read; no paths cross into the child.
 """
 def __init__(self, executable, authority, *, timeout=2., no_progress=.4):
  if not callable(getattr(authority,'authorize',None)):raise TypeError('trusted-authority-interface')
  if not .05<=timeout<=10 or not .05<=no_progress<=1:raise ValueError('time-policy')
  self.executable=Path(executable).resolve();self.authority=authority
  self.timeout,self.no_progress=timeout,no_progress;self._lock=threading.Lock();self._records={}
 def run(self, spec, source, trust, cancel=None, *, _fault='normal'):
  if not isinstance(spec,Attempt):raise TypeError('attempt')
  if _fault not in ('normal','crash','hang','memory','output','stderr','truncated','feed-hang','feed-crash','finish-hang','finish-crash'):raise ValueError('test-fault')
  history=['CREATED'];pid=None;process=None;consumed=0;state='FAILED';error=None;reaped=False;native_error=None
  def auth(checkpoint):
   if self.authority.authorize(spec,checkpoint) is not True:raise Denied()
  try:
   auth('admission')
   native=spec.profile=='native-hv2-d1.5-v1' and spec.policy=='native-single-source-v1'
   if not native and (spec.profile!='synthetic-hv2-d1-v1' or spec.policy!='synthetic-single-source-v1'):raise ProtocolError('profile')
   if spec.configuration!=CONFIG_DIGEST or (native and not 0<len(trust)<=32768):raise ProtocolError('native-trust-or-configuration')
   expected={'source':spec.source,'generation':spec.generation,'digest':spec.source_digest,'bytes':spec.source_bytes,'count':spec.nal_count}
   if source.describe()!=expected or not isinstance(trust,bytes) or len(trust)!=spec.trust_bytes or sha(trust)!=spec.trust_digest:raise ProtocolError('source-or-trust')
   occurrences=tuple(itertools.islice(source.occurrences(),1025))
   if len(occurrences)!=spec.nal_count or len({t for t,n,h in occurrences})!=len(occurrences):raise ProtocolError('occurrences')
   for token,n,h in occurrences:
    token_hex(token)
    if type(n)is not int or not 0<n<=1<<20 or len(h)!=64:raise ProtocolError('occurrence')
   if sum(n for _,n,_ in occurrences)!=spec.source_bytes:raise ProtocolError('source-bytes')
   identity=sha((spec.identity+json.dumps(occurrences)).encode())
   admission=Admission(spec,[t for t,_,_ in occurrences]) if native else None;reassembly=Reassembly()
  except Denied:return Outcome('REVOKED',tuple(history),None,False,error='authority')
  except (ProtocolError,ValueError,TypeError):return Outcome('FAILED',tuple(history),None,False,error='admission')
  except Exception:return Outcome('FAILED',tuple(history),None,False,error='authority-or-source')
  if not self._lock.acquire(False):return Outcome('BUSY',tuple(history),None,False,error='single-slot')
  try:
   if spec.attempt in self._records:
    key,outcome=self._records[spec.attempt]
    if key!=identity:return Outcome('FAILED',tuple(history),None,False,error='attempt-conflict')
    return outcome
   if len(self._records)>=16:return Outcome('RESOURCE_STOPPED',tuple(history),None,False,error='attempt-history-cap')
   if cancel is not None and cancel.is_set():raise Cancelled()
   history+=['ADMITTED','STARTING'];start=time.monotonic();last=start;sendseq=0;recvseq=0;incoming=0;stderr=0;sent=0;decoder=Decoder();stdout_eof=False
   process=subprocess.Popen([str(self.executable),str(os.getpid()),_fault],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True,close_fds=True,env={'LANG':'C','LC_ALL':'C'})
   pid=process.pid
   for f in (process.stdin,process.stdout,process.stderr):os.set_blocking(f.fileno(),False)
   def checkpoint():
    auth('active')
    if source.describe()!=expected:raise ProtocolError('source-generation')
    if cancel is not None and cancel.is_set():raise Cancelled()
    now=time.monotonic()
    if now-start>self.timeout or now-last>self.no_progress:raise ResourceStop('deadline')
   def pump():
    nonlocal incoming,stderr,last,stdout_eof
    checkpoint()
    for stream in (process.stdout,process.stderr):
     try:
      room=(MAX_HEADER+MAX_PAYLOAD+4-len(decoder.buffer)) if stream is process.stdout else 16384
      if not room:continue
      b=os.read(stream.fileno(),min(16384,room))
     except BlockingIOError:continue
     if not b and stream is process.stdout:stdout_eof=True
     if b:
      if stream is process.stderr:
       stderr+=len(b)
       if stderr>65536:raise ResourceStop('stderr-cap')
      else:
       incoming+=len(b)
       if incoming>MAX_OUTPUT:raise ResourceStop('output-cap')
       decoder.feed(b)
   def send(kind,payload=b'',**fields):
    nonlocal sendseq,sent,last
    checkpoint();data=encode(kind,spec.attempt,sendseq,payload,**fields);sendseq+=1;sent+=len(data)
    if sent>10<<20:raise ResourceStop('input-cap')
    at=0
    while at<len(data):
     pump()
     try:n=os.write(process.stdin.fileno(),data[at:])
     except BlockingIOError:n=0
     if n:at+=n;last=time.monotonic()
     else:time.sleep(.002)
   def message():
    nonlocal recvseq,last,native_error
    while True:
     checkpoint();value=decoder.pop()
     if value:
      h,payload=value
      if h['seq']!=recvseq or h['attempt']!=('startup' if h['type']=='READY' and recvseq==0 else spec.attempt):raise ProtocolError('response-binding')
      recvseq+=1;auth('output')
      if h['type']=='NATIVE_ERROR':
       if payload or h['code'] not in range(1,7) or h['stop'] not in (1,2,3) or h['consumed']!=0 or h['stage'] not in ('create','trust') or h['configuration']!=spec.configuration or h['trust_digest']!=spec.trust_digest:raise ProtocolError('native-error')
       native_error=(h['code'],h['stop']);raise NativeStop(str(h['stop']))
      return h,payload
     if stdout_eof:
      if decoder.buffer:raise ProtocolError('truncated-eof')
      raise EOFError('child-exit')
     pump()
     time.sleep(.002)
   def receive(kind):
    nonlocal last
    h,payload=message()
    if h['type']!=kind or payload:raise ProtocolError('response-type')
    last=time.monotonic();return h
   def receive_update(phase):
    nonlocal consumed,last
    while True:
     h,payload=message()
     if h['type'] not in ('UPDATE_BEGIN','UPDATE_CHUNK','UPDATE_END'):raise ProtocolError('update-frame')
     raw=reassembly.accept(h,payload)
     if raw is not None:
      u=admission.add(raw);consumed=admission.consumed;last=time.monotonic()
      if u['phase']=='error':raise NativeStop(str(u['stop']))
      if u['phase']!=phase:raise ProtocolError('update-phase')
      return u
   ready=receive('READY')
   if ready['abi']<1 or ready['guard']!='landlock-seccomp-pdeath' or ready['capability']!='B-h264-v1' or ready['configuration']!=spec.configuration:raise ProtocolError('guard')
   begin={k:getattr(spec,k) for k in ('request','run','source','generation','source_digest','source_bytes','nal_count','trust_digest','trust_bytes','camera','binding','profile','cut','policy','configuration')}
   send('BEGIN',**begin)
   for at in range(0,len(trust),65536):send('TRUST_CHUNK',trust[at:at+65536],offset=at)
   send('TRUST_END');reply=receive('ADMITTED')
   if reply['trust_digest']!=spec.trust_digest:raise ProtocolError('trust-ack')
   history+=['RUNNING'];total=hashlib.sha256()
   for ordinal,(token,n,digest) in enumerate(occurrences):
    checkpoint()
    if source.describe()!=expected:raise ProtocolError('source-generation')
    generation,actual_token,data=source.read(ordinal)
    if source.describe()!=expected or generation!=spec.generation or actual_token!=token or not isinstance(data,bytes) or len(data)!=n or sha(data)!=digest or len(data)<5 or not data.startswith(b'\0\0\0\1'):raise ProtocolError('source-changed')
    total.update(data);send('NAL_BEGIN',ordinal=ordinal,token=token_hex(token),size=n,digest=digest)
    for at in range(0,n,65536):send('NAL_CHUNK',data[at:at+65536],ordinal=ordinal,offset=at)
    send('NAL_END',ordinal=ordinal)
    if native:receive_update('feed')
    else:
     ack=receive('ACK')
     if (ack['ordinal'],ack['token'],ack['consumed'])!=(ordinal,token_hex(token),ordinal+1):raise ProtocolError('ack')
     consumed+=1
   if total.hexdigest()!=spec.source_digest:raise ProtocolError('source-digest')
   send('FINISH_INPUT')
   if native:receive_update('finish')
   terminal=receive('TERMINAL')
   if terminal['consumed']!=consumed or terminal['completeness']!='INCOMPLETE':raise ProtocolError('terminal')
   while process.poll() is None:pump();time.sleep(.002)
   while not stdout_eof:pump()
   if decoder.buffer:raise ProtocolError('trailing-output')
   if process.returncode!=0:raise EOFError('nonzero-exit')
   state='EXITED_SUCCESSFULLY'
  except NativeStop as ex:state='RESOURCE_STOPPED' if str(ex)=='1' else 'FAILED';error='native-stop-'+str(ex)
  except Denied:state='REVOKED';error='authority'
  except Cancelled:history+=['CANCELLATION_REQUESTED'];state='CANCELLED';error='cancel'
  except ResourceStop as ex:state='RESOURCE_STOPPED';error=str(ex)
  except (EOFError,BrokenPipeError):
   code=process.poll() if process else None;state='INTERRUPTED' if code is not None and code<0 else 'RESOURCE_STOPPED' if code==14 else 'FAILED';error='child-exit'
  except (OSError,ProtocolError,ValueError):state='FAILED';error='protocol-or-startup'
  except Exception:state='FAILED';error='adapter-failure'
  finally:
   if process is not None:
    if process.poll() is None:
     history+=['TERMINATING']
     if state in ('CANCELLED','REVOKED','RESOURCE_STOPPED'):
      try:os.write(process.stdin.fileno(),encode('STOP',spec.attempt,sendseq,reason=state))
      except (OSError,UnboundLocalError):pass
     try:os.killpg(process.pid,signal.SIGTERM)
     except ProcessLookupError:pass
     try:process.wait(timeout=.25)
     except subprocess.TimeoutExpired:
      try:os.killpg(process.pid,signal.SIGKILL)
      except ProcessLookupError:pass
      process.wait(timeout=1)
    process.wait(timeout=1);reaped=True
    if state=='FAILED' and error=='child-exit':
     if process.returncode<0:state='INTERRUPTED'
     elif process.returncode==14:state='RESOURCE_STOPPED'
    for f in (process.stdin,process.stdout,process.stderr):f.close()
   history.append(state)
   outcome=Outcome(state,tuple(history),pid,reaped,consumed,error=error,exit_code=process.returncode if process else None,updates=admission.raws if 'admission' in locals() and admission is not None else (),configuration=spec.configuration,native_error=native_error)
   # A single owning thread holds the slot until cleanup/reap; no Live/Legacy lock.
   if 'identity' in locals() and pid is not None:self._records[spec.attempt]=(identity,outcome)
   self._lock.release()
  return outcome
