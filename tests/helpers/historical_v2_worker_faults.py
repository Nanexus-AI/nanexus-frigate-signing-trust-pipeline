"""Controlled helper parent for D1.5 death-signal tests; never service code."""
import json,os,subprocess,sys
from nanexus_trust.historical_v2.worker_protocol import encode,sha

def parent_death(executable, native=False):
 trust=None
 if native:
  command=sys.stdin.buffer.readline(65537)
  assert len(command)<=65536
  value=json.loads(command);assert set(value)=={'public_ca_hex'}
  trust=bytes.fromhex(value['public_ca_hex']);assert 0<len(trust)<=32768
 p=subprocess.Popen([executable,str(os.getpid()),'feed-hang' if native else 'hang'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True,close_fds=True,env={'LANG':'C','LC_ALL':'C'})
 def receive(kind):
  n=int.from_bytes(p.stdout.read(4),'big');assert 0<n<=2048
  h=json.loads(p.stdout.read(n));assert h['type']==kind and h['payload_bytes']==0
 receive('READY')
 if native:
  data=b'\0\0\0\1\x67same'
  p.stdin.write(encode('BEGIN','parent-death',0,request='request',run='run',source='source',generation='generation',source_digest=sha(data),source_bytes=len(data),nal_count=1,trust_digest=sha(trust),trust_bytes=len(trust),camera='camera',binding='binding',profile='native-hv2-d1.5-v1',cut='synthetic-captured-cut',policy='native-single-source-v1'))
  p.stdin.write(encode('TRUST_CHUNK','parent-death',1,trust,offset=0));p.stdin.write(encode('TRUST_END','parent-death',2));p.stdin.flush();receive('ADMITTED')
  p.stdin.write(encode('NAL_BEGIN','parent-death',3,ordinal=0,token=b'occurrence'.hex(),size=len(data),digest=sha(data)))
  p.stdin.write(encode('NAL_CHUNK','parent-death',4,data,ordinal=0,offset=0));p.stdin.write(encode('NAL_END','parent-death',5,ordinal=0));p.stdin.flush()
 print(p.pid,flush=True)
 sys.stdin.buffer.read(1)
 os._exit(0)

if __name__=='__main__':parent_death(sys.argv[1],len(sys.argv)==3 and sys.argv[2]=='native')
