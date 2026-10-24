"""Opt-in finite rolling reconciliation. No media IO, verifier, service or clock mapping.

A Plan binds either an independently qualified source witness or a declared
catalog-order candidate. Neither is accepted from the browser.
"""
from dataclasses import dataclass, asdict
from decimal import Decimal
from pathlib import PurePosixPath
import base64
import hashlib
import json
from . import contracts as a, wire
from .composition import canonical, Composition
from .continuity_protocol import Attempt, CONFIG_DIGEST, ProtocolError, sha
from .continuity_updates import Admission, token_text

MAX_FILES=4
MAX_FILE_BYTES=8<<20
MAX_FILE_NALS=1024
MAX_CONTEXT_NALS=4096


def require(value,reason):
    if not value:raise ProtocolError(reason)


def digest(value):
    require(type(value) is str and len(value)==64 and set(value)<=set('0123456789abcdef'),'digest')


@dataclass(frozen=True)
class Original:
    item_id: str
    generation: str
    sha256: str
    nal_count: int
    annex_b_sha256: str

    def __post_init__(self):
        for value in (self.item_id,self.generation):
            require(type(value) is str and 0<len(value)<=128 and value.isascii() and all(ord(c)>=32 for c in value),'original-identity')
        digest(self.sha256);digest(self.annex_b_sha256)
        require(type(self.nal_count) is int and 0<self.nal_count<=MAX_FILE_NALS,'file-nal-cap')


@dataclass(frozen=True)
class Plan:
    """Closed private manifest; legacy field names also carry explicit candidates.

    Only the qualified schema claims corroborated source order. Candidate digests
    identify the declared input, never upstream equivalence or coverage.
    """
    originals: tuple[Original,...]
    trust_sha256: str
    qualified_order: bytes
    admitted_proof_sha256: str

    def __post_init__(self):
        require(type(self.originals) is tuple and 0<len(self.originals)<=4 and all(type(o) is Original for o in self.originals),'file-cap')
        require(len({o.item_id for o in self.originals})==len(self.originals),'duplicate-original')
        digest(self.trust_sha256);digest(self.admitted_proof_sha256)
        require(type(self.qualified_order) is bytes and 0<len(self.qualified_order)<=1<<20 and sha(self.qualified_order)==self.admitted_proof_sha256,'unqualified-order')
        # Exact closed evidence shape; no timestamps, arbitrary adjacency flag, or payload dedup.
        from .continuity_protocol import pairs
        try:w=json.loads(self.qualified_order,object_pairs_hook=pairs)
        except (ValueError,UnicodeError) as e:raise ProtocolError('order-json') from e
        require(type(w) is dict,'order-schema')
        if w.get('schema')=='qualified-source-occurrence-chain/1':
            require(set(w)=={'schema','originals','source_annex_b_sha256','source_nal_start'},'order-schema')
            require(type(w['source_nal_start']) is int and w['source_nal_start']>=0,'order-start')
            digest(w['source_annex_b_sha256'])
        else:
            require(w.get('schema')=='candidate-rolling-input/1' and set(w)=={'schema','policy','originals','candidate_annex_b_sha256','binding','sources'},'candidate-schema')
            require(w['policy']=='catalog-start-id-packet-nal/1','candidate-policy')
            digest(w['candidate_annex_b_sha256'])
            binding=w['binding']
            require(type(binding) is dict and set(binding)=={'request_sha256','owner_sha256','camera','binding_generation','snapshot_sha256','provider_profile'},'candidate-binding')
            for key in ('request_sha256','owner_sha256','snapshot_sha256'):digest(binding[key])
            from ..frigate.historical_v2 import PROFILE_0182
            require(binding['provider_profile']==PROFILE_0182,'candidate-profile')
            require(all(type(binding[k]) is str and 0<len(binding[k])<=256 for k in ('camera','binding_generation')),'candidate-camera')
            sources=w['sources'];require(type(sources) is list and len(sources)==len(self.originals),'candidate-sources')
            paths=[];order=[]
            for original,source in zip(self.originals,sources):
                require(type(source) is dict and set(source)=={'path','start','end','occurrences_sha256'},'candidate-source')
                path=source['path'];require(type(path) is str and 0<len(path)<=512 and '\\' not in path and all(ord(c)>=32 for c in path),'candidate-path')
                parts=PurePosixPath(path)
                require(not parts.is_absolute() and '..' not in parts.parts and str(parts)==path and path!='.','candidate-path')
                a.Interval(source['start'],source['end']);digest(source['occurrences_sha256'])
                paths.append(path);order.append((Decimal(source['start']),original.item_id))
            require(len(set(paths))==len(paths) and order==sorted(order),'candidate-order')
        require(w['originals']==[asdict(o) for o in self.originals],'order-originals')
        require(canonical(w)==self.qualified_order,'canonical-order')

    @property
    def candidate(self):return json.loads(self.qualified_order)['schema']=='candidate-rolling-input/1'

    @property
    def execution_policy(self):return 'candidate-rolling-context-v1' if self.candidate else 'qualified-rolling-context-v1'

    def bind_request(self,request,owner):
        if self.candidate:
            b=json.loads(self.qualified_order)['binding']
            require(b['request_sha256']==sha(wire.dumps(request)) and b['owner_sha256']==sha(owner.encode())
                    and b['camera']==request.camera.native_key and b['binding_generation']==request.camera.binding_generation,'candidate-request')

    def bind_selection(self,request,owner,items,snapshot,paths):
        self.bind_request(request,owner)
        if self.candidate:
            w=json.loads(self.qualified_order)
            require(w['binding']['snapshot_sha256']==snapshot.sha256,'candidate-snapshot')
            require(tuple(s['path'] for s in w['sources'])==tuple(paths),'candidate-path-binding')
            require(tuple((s['start'],s['end']) for s in w['sources'])==tuple((i.metadata_interval.start,i.metadata_interval.end) for i in items),'candidate-metadata')

    @property
    def identity(self):return sha(canonical({'proof':self.admitted_proof_sha256,'trust':self.trust_sha256,'configuration':CONFIG_DIGEST}))

    @property
    def input_sha256(self):return json.loads(self.qualified_order)['candidate_annex_b_sha256' if self.candidate else 'source_annex_b_sha256']

    def attempt(self,request,owner,attempt):
        self.bind_request(request,owner)
        identity=sha(canonical({'request':json.loads(wire.dumps(request)),'owner':owner,'plan':self.identity,'attempt':attempt}))
        return Attempt(attempt,request.request_id,'run-'+identity,owner,request.camera.native_key,request.camera.binding_generation,
                       self.identity,self.identity,self.identity,32<<20,4096,self.trust_sha256,1,
                       cut=self.admitted_proof_sha256,files=str(len(self.originals)),policy=self.execution_policy)


class Rolling:
    """Reconcile every B update; publish a file once after immutable dependencies.

    Caller commits a source only after direct read consistency and worker digest ACK.
    Reports are scoped to their generating original, never reassigned to that file.
    """
    def __init__(self,plan,request,spec,publish):
        require(spec.policy==plan.execution_policy and spec.configuration==CONFIG_DIGEST and spec.source==plan.identity and spec.cut==plan.admitted_proof_sha256 and spec.trust_digest==plan.trust_sha256,'context-binding')
        plan.bind_request(request,spec.principal)
        require(spec.request==request.request_id and spec.camera==request.camera.native_key and spec.binding==request.camera.binding_generation,'request-binding')
        self.plan,self.request,self.spec,self.publish=plan,request,spec,publish
        self.admission=None;self.files=[];self.tokens=[];self.facts=[];self.published=set();self.ended=False;self.failed=False

    def register(self,original,receipt,nals,metadata):
        require(not self.ended and not self.failed and len(self.files)<len(self.plan.originals),'source-phase')
        require(original==self.plan.originals[len(self.files)],'source-order')
        require(type(nals) is tuple and len(nals)==original.nal_count,'source-count')
        require(receipt['item_id']==original.item_id and receipt['generation']==original.generation and receipt['sha256']==original.sha256,'source-receipt')
        require(type(receipt['actual_bytes']) is int and 0<receipt['actual_bytes']<=MAX_FILE_BYTES,'original-byte-cap')
        h=hashlib.sha256();new=[];facts=[];size=0
        for n in nals:
            require(type(n.annex_b) is bytes and 5<=len(n.annex_b)<=1<<20 and n.annex_b.startswith(b'\0\0\0\1'),'nal')
            h.update(n.annex_b);size+=len(n.annex_b);new.append(n.token)
            facts.append({'token':n.token,'packet':n.packet,'nal':n.nal_in_packet,'length_offset':n.length_offset,'payload_offset':n.payload_offset,'payload_bytes':n.payload_bytes,'payload_sha256':n.payload_sha256,'pts':n.pts,'dts':n.dts,'duration':n.duration,'time_base':str(n.time_base)})
        require(size<=MAX_FILE_BYTES and h.hexdigest()==original.annex_b_sha256,'source-input-digest')
        if self.plan.candidate:
            positions=[(f['packet'],f['nal']) for f in facts]
            require(positions==sorted(positions) and len(set(positions))==len(positions),'candidate-packet-nal-order')
            expected=json.loads(self.plan.qualified_order)['sources'][len(self.files)]['occurrences_sha256']
            require(sha(canonical(facts))==expected,'candidate-occurrence-binding')
        require(len(set(self.tokens+new))==len(self.tokens)+len(new),'duplicate-occurrence')
        start=len(self.tokens);self.tokens.extend(new);self.facts.extend(facts)
        require(len(self.tokens)<=4096,'context-feed-cap')
        if self.admission is None:self.admission=Admission(self.spec,self.tokens)
        else:self.admission.tokens=tuple(self.tokens)
        self.files.append({'original':original,'start':start,'end':len(self.tokens),'receipt':dict(receipt),'metadata':dict(metadata),'committed':False})
        return size,h.hexdigest()

    def accept(self,raw):
        require(self.admission is not None and not self.failed and not self.ended,'update-phase')
        u=self.admission.add(raw)
        require(u['consumed']-(0 if self.admission.retired is None else self.admission.retired+1)<=1024,'active-cap')
        if any(i['axes']['key_changed'] for i in u['items']):
            self.failed=True;raise ProtocolError('unsupported-trust-change')
        if u['phase']=='error':self.failed=True;raise ProtocolError('native-context-stop')
        return u

    def commit_source(self,index):
        require(not self.failed and not self.ended and index==len([f for f in self.files if f['committed']]),'commit-order')
        f=self.files[index];require(self.admission.consumed==f['end'],'source-not-consumed')
        f['committed']=True;self._release(False)

    def finish(self):
        require(not self.failed and self.admission is not None and self.admission.ended and not self.ended,'finish-phase')
        require(len(self.files)==len(self.plan.originals) and all(f['committed'] for f in self.files),'unfinished-sources')
        self.ended=True;self._release(True)

    def fail(self):self.failed=True

    def _missing(self,f):
        return [m for r in self.admission.reports.values() for m in r['missing']
                if any(v is not None and f['start']<=v<f['end'] for v in (m['left_received'],m['right_received']))]

    def _ready(self,f):
        if not f['committed'] or self._missing(f):return False
        for ordinal in range(f['start'],f['end']):
            item=self.admission.last.get(ordinal)
            if item is None or not item['finalized']:return False
            report=self.admission.reports.get(item['report_generation'])
            if report is None:return False
            origin=report['generating_feed_ordinal']
            if not any(g['committed'] and g['start']<=origin<g['end'] for g in self.files):return False
        return True

    def _release(self,terminal):
        for f in self.files:
            identity=f['original'].item_id
            if identity not in self.published and (self._ready(f) or terminal):
                entry=self.entry(f,self._ready(f));self.publish(entry)
                self.published.add(identity)

    def entry(self,f,complete):
        refs=[];objects={}
        def put(label,raw):
            require(type(raw) is bytes and len(raw)<=1<<20,'object-cap')
            identity=label+'-'+sha(raw);objects[identity]=raw
            refs.append(a.EvidenceReference(identity,sha(raw),len(raw),'rolling-context-evidence-v1'));return identity
        source=put('sources',canonical({('candidate' if self.plan.candidate else 'proof'):json.loads(self.plan.qualified_order),'admitted_proof_sha256':self.plan.admitted_proof_sha256,'attempt':self.spec.identity,'run':self.spec.run,'files':[{k:v for k,v in g.items() if k not in ('original',)}|{'original':asdict(g['original'])} for g in self.files],'occurrences':self.facts}))
        trust=put('trust',canonical({'sha256':self.plan.trust_sha256,'configuration':self.spec.configuration}))
        chunk=[];size=2
        for raw in self.admission.raws:
            if size+len(raw)+1>1<<20:
                put('updates',b'['+b','.join(chunk)+b']');chunk=[];size=2
            require(len(raw)+2<=1<<20,'update-object-cap');chunk.append(raw);size+=len(raw)+1
        if chunk:put('updates',b'['+b','.join(chunk)+b']')
        owned=range(f['start'],f['end']);unresolved=[self.tokens[n] for n in owned if n not in self.admission.last or not self.admission.last[n]['finalized']]
        unprocessed=[self.tokens[n] for n in owned if n>=self.admission.consumed]
        missing=self._missing(f);tail=put('tail',canonical({'unresolved_tokens':unresolved,'unprocessed_tokens':unprocessed,'missing':missing}))
        context=tuple(r.evidence_id for r in refs);observations=[]
        for n in owned:
            i=self.admission.last.get(n)
            if i is None or not i['finalized']:continue
            state=(a.SigningState.VERIFIED_SIGNING,a.SigningState.INTEGRITY_NEGATIVE,a.SigningState.NO_RECOGNIZED_SIGNING_EVIDENCE,a.SigningState.INDETERMINATE)[i['state']]
            report=self.admission.reports[i['report_generation']];token=self.tokens[n]
            observations.append(a.Observation('obs-'+sha(canonical([self.spec.run,n,i['revision']])),self.spec.run,state,
                a.Support('sample',(token,),sample_ref=token),a.Placement(a.PlacementStatus.UNPLACED,None,None),
                a.Quality.SATISFIED if i['state'] in (0,1) else a.Quality.UNKNOWN,context,a.Finality.FINALIZED,
                a.ReportReference(str(i['report_generation']),report['generating_feed_ordinal'],i['revision'],(token,),report['retired_prefix_ordinal']),
                'public-ca-'+self.spec.trust_digest if i['state']==0 else None,context,('source-authenticity-not-established',)+(('candidate-order-not-upstream-corroborated',) if self.plan.candidate else ())))
        cursor=None
        if len(observations)>128:
            cursor=put('continuation',canonical({'run':self.spec.run,'offset':128,'observations':[json.loads(wire.dumps(o)) for o in observations[128:]]}))
        phase=a.Lifecycle.COMPLETE if complete else a.Lifecycle.INCOMPLETE
        page=a.ResultPage(self.request,'rolling-'+self.plan.identity,('candidate-rolling-context-experimental-v1' if self.plan.candidate else 'rolling-context-experimental-v1'),phase,None,
            a.Progress(phase,'stable-plan-file-size-weighted',sha(canonical(asdict(f['original']))),f['receipt']['actual_bytes'],f['receipt']['actual_bytes'] if complete else 0,f['receipt']['actual_bytes']),
            context,(source,),tuple(unresolved[:128])+((tail,) if missing or len(unresolved)>128 else ()),tuple(unprocessed[:128]),complete,
            (a.Coverage(self.request.interval,a.CoverageState.UNKNOWN,'inventory-unqualified',False),),tuple(observations[:128]),tuple(refs),cursor)
        raw=wire.dumps(page);require(wire.loads(raw)==page,'page-roundtrip')
        require(sum(map(len,objects.values()))<=4<<20,'file-evidence-cap')
        return {'recording':f['original'].item_id,'generation':f['original'].generation,'metadata_interval':f['metadata'],
                'status':'RESULT','execution':{'state':'ROLLING_RETIRED' if complete else 'CONTEXT_ENDED_INCOMPLETE','run':self.spec.run,'attempt':self.spec.attempt},
                'input':{'nal_occurrences':len(owned),'annex_b_bytes':0},
                'result':{'page':json.loads(raw),'sha256':sha(raw),'size':len(raw),'objects':[{'id':k,'sha256':sha(v),'size':len(v),'encoding':'base64','data':base64.b64encode(v).decode()} for k,v in objects.items()],
                          'source_ledger':self.facts[f['start']:f['end']],'original_file_sha256':f['original'].sha256}}
