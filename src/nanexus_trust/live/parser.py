"""Bounded H.264 ONVIF r25.12.6 structural observation; no authentication."""
import re
from .policy import ParserLimits
UUID=bytes.fromhex('005bc93f2d715e95ada4796f90877a6f')
MAX_NAL=1024*1024
START=re.compile(b'\x00\x00(?:\x00)?\x01')
class Unsupported(ValueError): pass

def rbsp(data):
    out=bytearray(); zeros=0; i=0
    while i<len(data):
        b=data[i]
        if zeros>=2 and b==3:
            if i+1>=len(data) or data[i+1]>3: raise ValueError('invalid_emulation_prevention')
            zeros=0; i+=1; continue
        out.append(b); zeros=zeros+1 if b==0 else 0; i+=1
    return bytes(out)

def messages(nal, limits=None):
    limits=limits or ParserLimits()
    if not nal or len(nal)>limits.max_nal_bytes or nal[0]&0x80: raise ValueError('nal_bound_or_header')
    if nal[0]&31!=6: return []
    b=rbsp(nal[1:]); i=0; result=[]
    def extended():
        nonlocal i
        n=0
        while i<len(b):
            v=b[i]; i+=1; n+=v
            if n>limits.max_nal_bytes: raise ValueError('sei_bound')
            if v!=255: return n
        raise ValueError('incomplete_sei_header')
    while i<len(b):
        if b[i:]==b'\x80': return result
        if len(result)>=limits.max_messages_per_nal: raise ValueError('message_count_bound')
        t=extended(); size=extended()
        if i+size>len(b): raise ValueError('incomplete_sei_payload')
        result.append((t,b[i:i+size])); i+=size
    raise ValueError('missing_rbsp_trailing_bits')

def structure(payload):
    if not payload.startswith(UUID): return None
    if len(payload)<20: raise ValueError('short_signing_structure')
    flags=payload[16]
    if flags&0x3f: raise Unsupported('reserved_flags')
    i=17; tags={}
    while i<len(payload):
        if len(tags)>=7 or i+3>len(payload): raise ValueError('tlv_header')
        tag=payload[i]; size=int.from_bytes(payload[i+1:i+3],'big'); i+=3
        if tag not in range(1,8): raise Unsupported('unknown_tag')
        if tag in tags or size==0 or i+size>len(payload): raise ValueError('tlv_length_or_duplicate')
        tags[tag]=payload[i:i+size]; i+=size
    # Optional tag contents are bounded opaque metadata, not accepted signatures.
    if flags&0x80: raise Unsupported('certificate_only_structure')
    if 1 not in tags or 3 not in tags: raise Unsupported('no_general_signature_pair')
    g=tags[1]; sig=tags[3]
    if g[0]!=2 or sig[0]!=1: raise Unsupported('tag_version')
    # General v2 fixed fields: v + software[3] + trigger + start/end[8] + counter[4] + count[2].
    # Measured SHA256 profile has two 32-byte hashes.
    if len(g)!=91 or g[4] not in (0,1): raise Unsupported('general_profile')
    if len(sig)<4: raise ValueError('signature_header')
    n=int.from_bytes(sig[1:3],'big')
    if not 0<n<=len(sig)-3 or sig[3+n:]!=b'\x01'*(len(sig)-3-n): raise ValueError('signature_length_or_padding')
    if 2 in tags and (tags[2][0]!=1 or (len(tags[2])-1)%32): raise Unsupported('hash_list_profile')
    return {'tags':sorted(tags),'gop_counter':int.from_bytes(g[21:25],'big'),'signature_bytes':n,'payload_bytes':len(payload)}

def accepted(nal, limits=None):
    return [s for t,p in messages(nal, limits) if t==5 for s in [structure(p)] if s is not None]

class Incremental:
    """At most one pending NAL plus bounded chunk. Never flush uncertain EOF."""
    def __init__(self, limits):
        self.limits=limits
        self.buf=bytearray()
        self.peak=0

    def feed(self, data):
        if len(data)>self.limits.max_chunk_bytes:
            raise ValueError('input_chunk_bound')
        self.buf.extend(data)
        self.peak=max(self.peak,len(self.buf))
        self.peak=max(self.peak,len(self.buf))
        while True:
            first=START.search(self.buf)
            if first is None:
                if len(self.buf)>3: raise ValueError('missing_start')
                return
            if first.start()!=0: raise ValueError('framing_prefix')
            second=START.search(self.buf,first.end())
            if second is None:
                # Up to three bytes may belong to an incomplete next start code.
                if len(self.buf)>self.limits.max_nal_bytes+first.end()+3:
                    raise ValueError('nal_size_bound')
                return
            size=second.start()-first.end()
            if not 1<=size<=self.limits.max_nal_bytes:
                raise ValueError('nal_size_bound')
            nal=bytes(self.buf[first.end():second.start()])
            del self.buf[:second.start()]
            yield nal

    def clear(self):
        self.buf.clear()
