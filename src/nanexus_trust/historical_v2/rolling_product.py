"""Opt-in request-scoped adapter for the qualified CONT09 native profile.

No signing inference, persistence, transcript archive or change to public A/HTTP.
"""
from dataclasses import asdict, dataclass
from types import SimpleNamespace
import hashlib
from decimal import Decimal
from pathlib import PurePosixPath
from . import wire
from .composition import canonical
from .continuity import Original, Rolling, require
from .continuity_protocol import Attempt, sha, validate
from .continuity_updates import Admission

CONFIGURATION = 'd0d6181bf911e3c5552ee2c6f4bb91db6362b767ccbd7686c07fdf79a8d1f943'
WORKER_SHA256 = 'cfe8d86efebd749e82e308d86f88316795fcc6828bb205da3d8f2d151e8d4d22'

@dataclass(frozen=True)
class ProductAttempt(Attempt):
    def __post_init__(self):
        require(self.configuration==CONFIGURATION and self.profile=='native-hv2-cont09-v1'
                and self.policy=='standalone-candidate-v1', 'product-native-profile')
        require(self.source_bytes==256<<20 and self.nal_count==32768
                and 0<self.trust_bytes<=32768 and 0<int(self.files)<=182, 'product-native-bounds')
        fields={k:v for k,v in asdict(self).items() if k not in ('attempt','principal')}
        validate(dict(protocol='hv2-cont03a/1',type='BEGIN',attempt=self.attempt,seq=0,payload_bytes=0,**fields))
        require(type(self.principal) is str and 0<len(self.principal)<=256, 'product-owner')
        for d in (self.trust_digest,self.source_digest):
            require(len(d)==64 and all(c in '0123456789abcdef' for c in d), 'product-digest')

class ProductPlan:
    product=True
    candidate=True
    input_sha256=None  # Candidate input is receipt-bound as read, not pre-proven upstream.
    execution_policy='standalone-candidate-v1'
    def __init__(self,request,owner,items,snapshot,trust_digest,paths):
        require(0<len(items)<=182 and len(paths)==len(items), 'product-selection-cap')
        require(len({i.item_id for i in items})==len(items) and len(set(paths))==len(paths), 'product-selection-duplicate')
        require(all(i.camera==request.camera and i.coordinate==request.coordinate for i in items), 'product-selection-camera')
        order=[(Decimal(i.metadata_interval.start),i.item_id) for i in items]
        require(order==sorted(order),'product-selection-order')
        require(all(type(p) is str and p and str(PurePosixPath(p))==p and not PurePosixPath(p).is_absolute()
                    and '..' not in PurePosixPath(p).parts and '\\' not in p for p in paths),'product-source-path')
        self.request,self.owner,self.originals=request,owner,tuple(items)
        self.trust_sha256=trust_digest
        self.qualified_order=canonical({'schema':'candidate-rolling-product/1','request_sha256':sha(wire.dumps(request)),
            'owner_sha256':sha(owner.encode()),'snapshot':{'sha256':snapshot.sha256,'cut_id':snapshot.cut_id,'source_pin':snapshot.source_pin},'trust_sha256':trust_digest,
            'policy':'catalog-start-id-packet-nal/1','sources':[{'recording':i.item_id,'generation':i.generation,
            'path':p,'metadata_interval':asdict(i.metadata_interval)} for i,p in zip(items,paths)]})
        self.admitted_proof_sha256=sha(self.qualified_order)
        self.identity=sha(canonical([self.admitted_proof_sha256,trust_digest,CONFIGURATION]))
    def bind_request(self,request,owner):
        require(request==self.request and owner==self.owner, 'product-request-owner')
    def attempt(self,request,owner,attempt):
        self.bind_request(request,owner)
        run='run-'+sha(canonical([self.identity,attempt]))
        return ProductAttempt(attempt,request.request_id,run,owner,request.camera.native_key,
            request.camera.binding_generation,self.identity,self.identity,self.admitted_proof_sha256,
            256<<20,32768,self.trust_sha256,1,profile='native-hv2-cont09-v1',
            cut=self.admitted_proof_sha256,policy=self.execution_policy,configuration=CONFIGURATION,files=str(len(self.originals)))

class _Facts(list):
    """Existing projection uses global ordinal slices; stored facts are file-local."""
    def __init__(self,values,start): super().__init__(values);self.start=start
    def __getitem__(self,key):
        if isinstance(key,slice):key=slice(key.start-self.start,key.stop-self.start,key.step)
        return super().__getitem__(key)

class ProductRolling:
    def __init__(self,plan,request,spec,publish):
        plan.bind_request(request,spec.principal)
        require(spec.configuration==CONFIGURATION and spec.source==plan.identity and spec.trust_digest==plan.trust_sha256, 'product-context')
        self.plan,self.request,self.spec,self.publish=plan,request,spec,publish
        self.files=[];self.tokens={};self.next_ordinal=0;self.input_bytes=0;self.admission=None
        self.published=set();self.ended=False;self.failed=False;self.max_active=0;self.max_report=0
        self.max_working_bytes=0;self.cross_file_retirements=0
    def owner(self,ordinal):
        found=[f for f in self.files if f['start']<=ordinal<f['end']]
        require(len(found)==1,'product-ordinal-owner');return found[0]
    def register(self,item,receipt,nals,metadata):
        require(not self.failed and not self.ended and item==self.plan.originals[len(self.files)],'product-source-order')
        require(receipt['item_id']==item.item_id and receipt['generation']==item.generation,'product-source-receipt')
        require(0<receipt['actual_bytes']<=8<<20 and 0<len(nals)<=1024,'product-source-cap')
        digest=hashlib.sha256();size=0;facts=[];prefix=sha((item.item_id+':'+item.generation+':'+receipt['sha256']).encode())
        for n in nals:
            require(n.annex_b.startswith(b'\0\0\0\1') and 5<=len(n.annex_b)<=1<<20,'product-nal')
            require(n.token==f'{prefix}:p{n.packet}:n{n.nal_in_packet}:o{n.payload_offset}:l{n.payload_bytes}', 'product-occurrence')
            digest.update(n.annex_b);size+=len(n.annex_b)
            facts.append({'token':n.token,'packet':n.packet,'nal':n.nal_in_packet,'length_offset':n.length_offset,
                'payload_offset':n.payload_offset,'payload_bytes':n.payload_bytes,'payload_sha256':n.payload_sha256,
                'pts':n.pts,'dts':n.dts,'duration':n.duration,'time_base':str(n.time_base)})
        positions=[(n.packet,n.nal_in_packet) for n in nals]
        require(positions==sorted(positions) and len(set(positions))==len(positions) and size<=8<<20,'product-nal-order')
        self.input_bytes+=size;require(self.input_bytes<=256<<20,'product-input-cap')
        start=self.next_ordinal;self.next_ordinal+=len(nals);require(self.next_ordinal<=32768,'product-feed-cap')
        self.tokens.update((start+i,n.token) for i,n in enumerate(nals))
        if self.admission is None:self.admission=Admission(self.spec,tuple(n.token for n in nals),compact=True)
        self.admission.tokens=self.tokens;self.admission.token_count=self.next_ordinal
        original=Original(item.item_id,item.generation,receipt['sha256'],len(nals),digest.hexdigest())
        self.files.append(dict(original=original,start=start,end=self.next_ordinal,receipt=dict(receipt),metadata=dict(metadata),
            committed=False,items={},reports={},raws={},missing=[],facts=facts,dependencies=set()))
        return size,digest.hexdigest()
    def accept(self,raw):
        require(not self.failed and not self.ended,'product-update-phase')
        previous_retired=self.admission.retired
        u=self.admission.add(raw)
        active=u['consumed']-(0 if previous_retired is None else previous_retired+1)
        require(active<=1024,'product-active-cap')
        self.max_active=max(self.max_active,active)
        self.max_report=max(self.max_report,len(u['items'])+len(u['missing']))
        require(u['consumed']-(0 if self.admission.retired is None else self.admission.retired+1)<=1024,'product-active-cap')
        if any(i['axes']['key_changed'] for i in u['items']):
            self.failed=True;raise ValueError('unsupported-trust-change')
        require(u['phase']!='error','native-context-stop')
        producer=self.owner(u['generating_feed_ordinal'])
        touched=set()
        for i in u['items']:
            f=self.owner(i['received_ordinal']);idx=self.files.index(f);touched.add(idx)
            old=f['items'].get(i['received_ordinal'])
            if i['finalized'] and old is not None and not old['finalized'] and producer['start']>f['start']:
                self.cross_file_retirements+=1
            f['items'][i['received_ordinal']]=i
            f['dependencies'].add(self.files.index(producer))
        for m in u['missing']:
            for ordinal in {m['left_received'],m['right_received']}-{None}:
                f=self.owner(ordinal);idx=self.files.index(f)
                require(f['original'].item_id not in self.published,'missing-after-publication')
                if m not in f['missing']:f['missing'].append(m)
                touched.add(idx)
        for idx in touched:
            f=self.files[idx]
            if u['fresh_report']:f['reports'][u['report_generation']]=u;f['raws'][u['report_generation']]=raw
            # Keep only generating reports referenced by current items/missing anchors.
            needed={i['report_generation'] for i in f['items'].values()}|{m['report_generation'] for m in f['missing']}
            f['reports']={g:r for g,r in f['reports'].items() if g in needed}
            f['raws']={g:r for g,r in f['raws'].items() if g in needed}
            if u['phase']=='finish':f['raws'][-1]=raw
        # Native active validation retains only unresolved items; immutable finalized
        # detail lives in its file aggregation until Bundle accepts publication.
        self.admission.last={n:i for n,i in self.admission.last.items() if not i['finalized']}
        working=sum(sum(len(r) for r in f['raws'].values())+len(canonical(f['items']))+len(canonical(f['facts'])) for f in self.files)
        require(working<=32<<20,'product-working-evidence-cap');self.max_working_bytes=max(self.max_working_bytes,working)
        return u
    def _ready(self,f):
        return (f['committed'] and not f['missing'] and len(f['items'])==f['end']-f['start']
            and all(i['finalized'] for i in f['items'].values())
            and all(self.files[n]['committed'] for n in f['dependencies']))
    def entry(self,f,complete):
        # Reuse unchanged A projection/classification with a scoped evidence view,
        # never a cumulative native transcript or copies of previous file results.
        view=object.__new__(Rolling)
        descriptor={'schema':'candidate-rolling-product-file/1','selection_sha256':self.plan.admitted_proof_sha256,
            'request_sha256':sha(wire.dumps(self.request)),'owner_sha256':sha(self.spec.principal.encode()),
            'policy':'catalog-start-id-packet-nal/1'}
        view.plan=SimpleNamespace(candidate=True,qualified_order=canonical(descriptor),
            admitted_proof_sha256=self.plan.admitted_proof_sha256,trust_sha256=self.plan.trust_sha256,identity=self.plan.identity)
        view.request=self.request;view.spec=self.spec;view.tokens=self.tokens
        view.facts=_Facts(f['facts'],f['start'])
        # A retained generating report can also retire controls owned by an
        # earlier file. Preserve their existing compact source receipts, even
        # after that file's mutable observation aggregation has been released.
        # This is provenance coverage for report context, not a new assertion
        # that every co-reported control cryptographically signs this file.
        sources={self.files.index(f)} | f['dependencies']
        for report in f['reports'].values():
            for item in report['items']:
                if item['finalized'] and item['nalu_type'] not in 'IiPp':
                    owner=self.owner(item['received_ordinal'])
                    require(owner['committed'], 'supporting-control-source-uncommitted')
                    sources.add(self.files.index(owner))
        view.files=[{k:v for k,v in g.items() if k in ('original','start','end','receipt','metadata','committed')}
                    for index,g in enumerate(self.files) if index in sources]
        view.admission=SimpleNamespace(last=f['items'],reports=f['reports'],raws=tuple(f['raws'].values()),consumed=self.admission.consumed)
        # Missing anchors remain local, including records without a finalized item.
        view._missing=lambda unused:list(f['missing'])
        entry=Rolling.entry(view,f,complete)
        return entry
    def _release(self,terminal=False):
        for f in self.files:
            identity=f['original'].item_id
            if identity in self.published:continue
            ready=self._ready(f)
            if ready or terminal:
                self.publish(self.entry(f,ready));self.published.add(identity)
                for n in range(f['start'],f['end']):self.tokens.pop(n,None)
                f['items'].clear();f['reports'].clear();f['raws'].clear();f['facts'].clear();f['missing'].clear();f['dependencies'].clear()
    def commit_source(self,index):
        require(index==sum(f['committed'] for f in self.files) and self.admission.consumed==self.files[index]['end'],'product-source-commit')
        self.files[index]['committed']=True;self._release()
    def finish(self):
        require(not self.failed and not self.ended and self.admission.ended and len(self.files)==len(self.plan.originals)
                and all(f['committed'] for f in self.files),'product-finish')
        self.ended=True;self._release(True)
    def fail(self):self.failed=True
