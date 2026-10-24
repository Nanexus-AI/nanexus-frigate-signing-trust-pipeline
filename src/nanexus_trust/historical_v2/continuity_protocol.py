"""Opt-in bounded CONT03A protocol, not a Companion/browser API."""
import hashlib
import json
import struct
from dataclasses import dataclass, asdict

VERSION = 'hv2-cont03a/1'
CONFIG_DIGEST = 'f3d7c70f0a9e8c0e5b49b41d0be5cfdffe785d1620953ec238bbc8a50a7cee26'
MAX_HEADER = 2048
MAX_PAYLOAD = 65536
MAX_INPUT = 32 << 20
MAX_OUTPUT = 10 << 20
MAX_NALS = 4096
FIELDS = {
 'READY': {'abi','guard','capability','configuration'},
 'BEGIN': {'request','run','source','generation','source_digest','source_bytes','nal_count','trust_digest','trust_bytes','camera','binding','profile','cut','policy','configuration','files'},
 'SOURCE_BEGIN': {'ordinal','source','generation','original_sha256','source_digest','source_bytes','nal_count'},
 'SOURCE_END': {'ordinal','source_digest'},
 'TRUST_CHUNK': {'offset'}, 'TRUST_END': set(), 'ADMITTED': {'trust_digest'},
 'NAL_BEGIN': {'ordinal','token','size','digest'}, 'NAL_CHUNK': {'ordinal','offset'},
 'NAL_END': {'ordinal'}, 'ACK': {'ordinal','token','consumed'},
 'NATIVE_ERROR': {'code','stop','consumed','configuration','trust_digest','stage'},
 'UPDATE_BEGIN': {'update','size','digest','items','missing'}, 'UPDATE_CHUNK': {'update','offset'}, 'UPDATE_END': {'update','digest'},
 'FINISH_INPUT': set(), 'TERMINAL': {'consumed','completeness'}, 'STOP': {'reason'},
}
class ProtocolError(ValueError): pass

def pairs(values):
 d={}
 for k,v in values:
  if k in d: raise ProtocolError('duplicate-key')
  d[k]=v
 return d

def validate(h):
 if type(h)is not dict or h.get('type') not in FIELDS: raise ProtocolError('message-type')
 if set(h) != {'protocol','type','attempt','seq','payload_bytes'} | FIELDS[h['type']]: raise ProtocolError('closed-fields')
 if h['protocol']!=VERSION:raise ProtocolError('version')
 numeric={'seq','payload_bytes','offset','ordinal','size','source_bytes','nal_count','trust_bytes','abi','consumed','update','items','missing','code','stop'}
 for k,v in h.items():
  if (k in numeric and type(v)is not int) or (k not in numeric and type(v)is not str):raise ProtocolError('field-type')
  if type(v) is str:
   if not v or len(v.encode())>(512 if k=='token' else 256) or any(ord(c)<32 or ord(c)>126 for c in v): raise ProtocolError('text')
  elif type(v)is not int or not 0<=v<2**63:raise ProtocolError('integer')
 if not 0<=h['payload_bytes']<=MAX_PAYLOAD:raise ProtocolError('payload-size')
 if h['type'] not in ('TRUST_CHUNK','NAL_CHUNK','UPDATE_CHUNK') and h['payload_bytes']:raise ProtocolError('unexpected-payload')
 return h

def encode(kind,attempt,seq,payload=b'',**fields):
 if kind=='BEGIN':fields.setdefault('configuration',CONFIG_DIGEST)
 h=validate(dict(protocol=VERSION,type=kind,attempt=attempt,seq=seq,payload_bytes=len(payload),**fields))
 raw=json.dumps(h,separators=(',',':'),sort_keys=True).encode()
 if len(raw)>MAX_HEADER:raise ProtocolError('header-cap')
 return struct.pack('!I',len(raw))+raw+payload

class Decoder:
 def __init__(self):self.buffer=bytearray()
 def feed(self,data):
  if len(self.buffer)+len(data)>MAX_HEADER+MAX_PAYLOAD+4:raise ProtocolError('frame-buffer-cap')
  self.buffer.extend(data)
 def pop(self):
  if len(self.buffer)<4:return None
  n=struct.unpack('!I',self.buffer[:4])[0]
  if not 0<n<=MAX_HEADER:raise ProtocolError('header-size')
  if len(self.buffer)<4+n:return None
  try:h=validate(json.loads(self.buffer[4:4+n],object_pairs_hook=pairs))
  except (UnicodeError,json.JSONDecodeError) as e:raise ProtocolError('json') from e
  size=4+n+h['payload_bytes']
  if len(self.buffer)<size:return None
  payload=bytes(self.buffer[4+n:size]);del self.buffer[:size];return h,payload

def sha(data):return hashlib.sha256(data).hexdigest()

@dataclass(frozen=True)
class Attempt:
 attempt: str
 request: str
 run: str
 principal: str
 camera: str
 binding: str
 source: str
 generation: str
 source_digest: str
 source_bytes: int
 nal_count: int
 trust_digest: str
 trust_bytes: int
 profile: str = 'native-hv2-cont03a-v1'
 cut: str = 'synthetic-captured-cut'
 policy: str = 'qualified-rolling-context-v1'
 configuration: str = CONFIG_DIGEST
 files: str = '1'
 def __post_init__(self):
  for k,v in asdict(self).items():
   if k in ('source_bytes','nal_count','trust_bytes'):
    if type(v)is not int or v<=0:raise ProtocolError('attempt-size')
   elif type(v)is not str or not v or len(v)>128 or any(ord(c)<32 or ord(c)>126 for c in v):raise ProtocolError('attempt-text')
  if self.profile!='native-hv2-cont03a-v1' or self.policy not in ('qualified-rolling-context-v1','candidate-rolling-context-v1'):
   raise ProtocolError('execution-policy')
  for digest in (self.source_digest,self.trust_digest):
   if len(digest)!=64 or any(c not in '0123456789abcdef' for c in digest):raise ProtocolError('digest')
  if self.source_bytes>MAX_INPUT or self.nal_count>MAX_NALS or self.trust_bytes>65536:raise ProtocolError('attempt-cap')
 @property
 def identity(self):return sha(json.dumps(asdict(self),sort_keys=True,separators=(',',':')).encode())
