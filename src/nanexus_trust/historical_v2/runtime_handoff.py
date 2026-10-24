"""Synthetic-only immutable parent/Publisher seam. No Worker or service execution.

Authority receipts are ephemeral test policy, never native execution provenance.
Only trusted owner code may enroll leases or construct the Publisher component.
"""
from contextlib import contextmanager
from dataclasses import asdict, dataclass
import threading
import time
from . import publication as p, composition as c, wire, contracts as a
from .worker_protocol import Attempt, CONFIG_DIGEST
from .worker_updates import Admission, decode

SCHEMA='nanexus-hv2-handoff/1'
POLICY='synthetic-handoff-v1'
MAX_TOTAL=12<<20


@dataclass(frozen=True)
class Handoff:
    metadata: bytes
    blobs: tuple[bytes, ...]

    def __post_init__(self):
        p.need(type(self.metadata) is bytes and 0<len(self.metadata)<=65536,'HANDOFF_BOUNDS')
        p.need(type(self.blobs) is tuple and len(self.blobs)<=1093,'HANDOFF_BOUNDS')
        p.need(all(type(b) is bytes and 0<len(b)<=2<<20 for b in self.blobs),'HANDOFF_BOUNDS')
        p.need(len(self.metadata)+sum(map(len,self.blobs))<=MAX_TOTAL,'HANDOFF_BOUNDS')


def _digest_record(m):
    return p.digest(p.canonical({k:v for k,v in m.items() if k!='handoff_id'}))


def _intent(m):
    # Lease epoch and observed outcome are separate from immutable source intent.
    return p.digest(p.canonical({k:m[k] for k in ('schema','policy','producer','parent_incarnation','request','attempt','source','worker','publication')}))


def _ref(blobs, raw):
    p.need(type(raw) is bytes,'MUTABLE_EVIDENCE')
    d={'index':len(blobs),**p.descriptor(raw)};blobs.append(raw);return d


def freeze(candidate, *, session, lease, revision, parent_incarnation,
           key, publication=None, expected_head=None, checkpoint=False,
           execution='EXITED_SUCCESSFULLY', error=None):
    """Freeze a bounded trusted-parent candidate, not a caller's success flag."""
    p.need(type(candidate) is p.Candidate,'HANDOFF_TYPE')
    for value in (session,lease,key):p.text(value);p.need('/' not in value and '\\' not in value)
    p.hexid(parent_incarnation,32);p.number(revision,2**63-1);p.need(type(checkpoint) is bool)
    if publication is not None:p.hexid(publication,32)
    if expected_head is not None:p.hexid(expected_head)
    p.need((candidate.provider,candidate.reader)==(p.PROVIDER,p.READER),'UNSUPPORTED')
    p.need(type(candidate.sources) is tuple and len(candidate.sources)<=1024,'HANDOFF_BOUNDS')
    p.need(type(candidate.updates) is tuple and len(candidate.updates)<=1025,'HANDOFF_BOUNDS')
    p.need(all(type(b) is bytes and len(b)<=1<<20 for b in candidate.updates),'MUTABLE_EVIDENCE')
    p.need(sum(map(len,candidate.updates))<=2<<20,'HANDOFF_BOUNDS')
    p.need(type(candidate.result.objects) is tuple and len(candidate.result.objects)<=64,'HANDOFF_BOUNDS')
    p.need(all(type(x) is tuple and len(x)==2 and type(x[1]) is bytes and len(x[1])<=2<<20 for x in candidate.result.objects),'MUTABLE_EVIDENCE')
    for identity,_ in candidate.result.objects:p.text(identity)
    p.need(sum(len(b) for _,b in candidate.result.objects)<=8<<20,'HANDOFF_BOUNDS')
    p.need(candidate.worker_binary=='0'*64 and candidate.policy==p.POLICY,'UNSUPPORTED')
    c.source_check(candidate.sources)
    source=c.canonical(candidate.sources);p.need(len(source)<=512<<10,'HANDOFF_BOUNDS')
    blobs=[];request=_ref(blobs,wire.dumps(candidate.request));base=_ref(blobs,wire.dumps(candidate.result.page))
    source_ref=_ref(blobs,source);updates=[_ref(blobs,b) for b in candidate.updates]
    profiles={e.evidence_id:e.profile for e in candidate.result.page.evidence}
    objects=[{'id':k,'profile':profiles.get(k), 'blob':_ref(blobs,b)} for k,b in candidate.result.objects]
    spec=candidate.attempt
    admission=Admission(spec,[s['token'] for s in candidate.sources])
    for raw in candidate.updates:admission.add(raw)
    tails=[d for d in objects if d['profile']=='d1.5-tail-v1'];p.need(len(tails)==1)
    m={'schema':SCHEMA,'policy':POLICY,'producer':'synthetic','handoff_id':'0'*64,
       'parent_incarnation':parent_incarnation,'request':request,'attempt':asdict(spec),
       'source':{'provider':candidate.provider,'reader':candidate.reader,'cut':spec.cut,'generation':spec.generation,
                 'processing':spec.source_digest,'ledger':p.digest(source),'occurrences':source_ref},
       'worker':{'profile':spec.profile,'executable':'0'*64,'configuration':spec.configuration,
                 'trust':spec.trust_digest,'trust_bytes':spec.trust_bytes,'receipt':'synthetic-execution-not-run'},
       'updates':updates,'composition':{'base':base,'objects':objects},
       'terminal':{'execution':execution,'error':error,'exit_code':0 if execution=='EXITED_SUCCESSFULLY' else None,
                   'reaped':True,'consumed':admission.consumed,'operational':candidate.operational,
                   'finalized':sum(i['finalized'] for i in admission.last.values()),'tail':tails[0]['blob']['sha256'],
                   'stop':decode(candidate.updates[-1])['stop'] if admission.ended else None,'native_error':None},
       'publication':{'id':publication,'key':key,'expected_head':expected_head},
       'authority':{'principal':spec.principal,'session':session,'camera':spec.camera,'binding':spec.binding,
                    'lease':lease,'revision':revision,'intent':'0'*64,'publish':True,'checkpoint':checkpoint}}
    m['authority']['intent']=_intent(m);m['handoff_id']=_digest_record(m)
    h=Handoff(p.canonical(m),tuple(blobs));recognize(h);return h


def recognize(h):
    """Independent byte/reference and full accepted-composition recognition."""
    p.need(type(h) is Handoff,'HANDOFF_TYPE');h.__post_init__()
    try:return _recognize(h)
    except p.PublicationError:raise
    except (ValueError,TypeError,KeyError,IndexError,RecursionError,AttributeError) as exc:
        raise p.PublicationError('HANDOFF_INVALID') from exc


def _recognize(h):
    m=p.load(h.metadata);p.fields(m,('schema','policy','producer','handoff_id','parent_incarnation','request','authority','attempt','source','worker','updates','composition','terminal','publication'))
    p.need((m['schema'],m['policy'],m['producer'])==(SCHEMA,POLICY,'synthetic'),'UNSUPPORTED')
    p.hexid(m['parent_incarnation'],32);p.hexid(m['handoff_id']);p.need(m['handoff_id']==_digest_record(m))
    auth=m['authority'];p.fields(auth,('principal','session','camera','binding','lease','revision','intent','publish','checkpoint'))
    for k in ('principal','session','camera','binding','lease'):p.text(auth[k]);p.need('/' not in auth[k] and '\\' not in auth[k])
    p.number(auth['revision'],2**63-1);p.hexid(auth['intent']);p.need(auth['intent']==_intent(m))
    p.need(type(auth['publish']) is bool and type(auth['checkpoint']) is bool)
    p.fields(m['attempt'],Attempt.__dataclass_fields__);spec=Attempt(**m['attempt'])
    seen=set()
    def blob(d,cap):
        p.fields(d,('index','sha256','size'));p.number(d['index'],len(h.blobs)-1)
        p.need(d['index'] not in seen);seen.add(d['index'])
        raw=h.blobs[d['index']];p.check_bytes(raw,{k:d[k] for k in ('sha256','size')},cap);return raw
    request=wire.loads(blob(m['request'],1<<20));p.need(type(request) is a.Request)
    s=m['source'];p.fields(s,('provider','reader','cut','generation','processing','ledger','occurrences'))
    source=blob(s['occurrences'],512<<10);sources=tuple(p.load(source,512<<10));c.source_check(sources)
    p.need(s['ledger']==p.digest(source) and (s['provider'],s['reader'])==(p.PROVIDER,p.READER))
    p.need((s['cut'],s['generation'],s['processing'])==(spec.cut,spec.generation,spec.source_digest))
    w=m['worker'];p.fields(w,('profile','executable','configuration','trust','trust_bytes','receipt'))
    p.need(w=={'profile':spec.profile,'executable':'0'*64,'configuration':spec.configuration,'trust':spec.trust_digest,'trust_bytes':spec.trust_bytes,'receipt':'synthetic-execution-not-run'},'UNSUPPORTED')
    p.need(spec.configuration==CONFIG_DIGEST and (auth['principal'],auth['camera'],auth['binding'])==(spec.principal,spec.camera,spec.binding))
    p.need(type(m['updates']) is list and len(m['updates'])<=1025,'HANDOFF_BOUNDS')
    p.need(sum(d['size'] for d in m['updates'])<=2<<20,'HANDOFF_BOUNDS')
    raws=tuple(blob(d,1<<20) for d in m['updates'])
    co=m['composition'];p.fields(co,('base','objects'));base=wire.loads(blob(co['base'],1<<20));p.need(type(base) is a.ResultPage)
    p.need(type(co['objects']) is list and len(co['objects'])<=64,'HANDOFF_BOUNDS');objects=[]
    p.need(sum(d['blob']['size'] for d in co['objects'])<=8<<20,'HANDOFF_BOUNDS')
    refs={e.evidence_id:e for e in base.evidence}
    for d in co['objects']:
        p.fields(d,('id','profile','blob'));p.text(d['id']);p.need(d['id'] in refs and d['profile']==refs[d['id']].profile)
        objects.append((d['id'],blob(d['blob'],2<<20)))
    p.need(seen==set(range(len(h.blobs))))
    t=m['terminal'];p.fields(t,('execution','error','exit_code','reaped','consumed','operational','finalized','tail','stop','native_error'))
    mapping={'EXITED_SUCCESSFULLY':(None,None),'CANCELLED':('cancel','cancel'),'RESOURCE_STOPPED':('resource','resource'),'INTERRUPTED':('interrupted','child-exit')}
    p.need(type(t['execution']) is str and t['execution'] in mapping,'UNSUPPORTED')
    p.need((t['operational'],t['error'])==mapping[t['execution']] and t['reaped'] is True)
    p.need(type(t['exit_code']) is int and t['exit_code']==0 if t['execution']=='EXITED_SUCCESSFULLY' else t['exit_code'] is None)
    p.number(t['consumed'],1024);p.number(t['finalized'],1024);p.hexid(t['tail'])
    ad=Admission(spec,[s['token'] for s in sources])
    for raw in raws:ad.add(raw)
    p.need(t['native_error'] is None and t['stop']==(decode(raws[-1])['stop'] if ad.ended else None))
    p.need(t['consumed']==ad.consumed and t['finalized']==sum(i['finalized'] for i in ad.last.values()))
    tails=[k for k,e in refs.items() if e.profile=='d1.5-tail-v1'];p.need(len(tails)==1 and refs[tails[0]].sha256==t['tail'])
    result=c.Composition(base,tuple(objects));candidate=p.Candidate(request,spec,sources,raws,result,t['operational'],s['provider'],s['reader'],w['executable'])
    candidate.validate()
    pub=m['publication'];p.fields(pub,('id','key','expected_head'));p.text(pub['key'])
    if pub['id'] is not None:p.hexid(pub['id'],32)
    if pub['expected_head'] is not None:p.hexid(pub['expected_head'])
    return m,candidate


class SyntheticHandoffAuthority:
    """Named trusted test authority. NOT Companion authentication or a credential.

    Enrollment is an owner-only test action, never a transport-facing method.
    Each grant preserves the exact frozen authority and publication intent.
    """
    def __init__(self):
        self.lock=threading.RLock();self._grants={};self._reads={};self._associations={};self._active=None;self._maintenance=set();self._slot=threading.Lock();self._store=None

    def enroll(self,h,*,seconds=60):
        m,candidate=recognize(h);p.need(type(seconds) in (int,float) and 0<seconds<=3600)
        with self.lock:
            key=m['authority']['lease'];p.need(key in self._grants or len(self._grants)<16,'QUOTA')
            if key in self._grants:p.need(m['authority']['revision']>p.load(self._grants[key][0])['revision'],'STALE_LEASE')
            self._grants[key]=(p.canonical(m['authority']),time.monotonic()+seconds,False,False)
            b=candidate.binding();self._associations[key]=p.canonical({k:b[k] for k in ('owner','camera','camera_binding','request')})

    def revoke(self,lease):
        with self.lock:
            grant=self._grants[lease];self._grants[lease]=(grant[0],grant[1],True,grant[3])

    def cancel_publication(self,lease):
        with self.lock:
            grant=self._grants[lease];self._grants[lease]=(grant[0],grant[1],grant[2],True)

    def _allowed(self,m):
        grant=self._grants.get(m['authority']['lease'])
        return bool(grant and grant[0]==p.canonical(m['authority']) and time.monotonic()<grant[1] and not grant[2] and not grant[3]
                    and m['authority']['publish'] and (m['terminal']['operational'] is None or m['authority']['checkpoint']))

    def authorize_publish(self,association):
        with self.lock:
            if self._active is None:return False
            m,binding=self._active
            return binding==association and self._allowed(m)

    def final_publish_check(self,association):
        """Fresh full-intent/lease decision under the active publication guard.

        The store calls this after control-temp sync, immediately before Head
        replacement. Later clock expiry cannot undo this authorization decision.
        """
        return self.authorize_publish(association)

    @contextmanager
    def publish_guard(self,association):
        with self.lock:
            p.need(self.authorize_publish(association),'ACCESS_DENIED');yield
            # Test injection may revoke reentrantly; real competing revocation
            # cannot pass this lock until the existing Head transition completes.

    def grant_retrieval(self,association,caller,lease):
        p.text(caller)
        with self.lock:
            grant=self._grants.get(lease);p.need(grant is not None and self._associations[lease]==p.canonical(association) and p.load(grant[0])['principal']==caller,'ACCESS_DENIED')
            key=(p.canonical(association),caller);p.need(key in self._reads or len(self._reads)<16,'QUOTA');self._reads[key]=(lease,grant[0])

    def authorize_retrieve(self,association,caller):
        with self.lock:
            permission=self._reads.get((p.canonical(association),caller))
            if permission is None:return False
            grant=self._grants.get(permission[0])
            return bool(grant and grant[0]==permission[1] and time.monotonic()<grant[1] and not grant[2])

    @contextmanager
    def retrieval_guard(self,association,caller):
        with self.lock:
            p.need(self.authorize_retrieve(association,caller),'ACCESS_DENIED');yield

    def revoke_retrieval(self,association,caller):
        with self.lock:self._reads.pop((p.canonical(association),caller),None)

    def grant_maintenance(self,root,publication,generation=None):
        with self.lock:
            p.need(len(self._maintenance)<16,'QUOTA');self._maintenance.add((root,publication,generation))

    def authorize_maintenance(self,context):
        with self.lock:return (context['root'],context['publication'],context['target']) in self._maintenance

    @contextmanager
    def maintenance_guard(self,context):
        with self.lock:
            p.need(self.authorize_maintenance(context),'ACCESS_DENIED');yield


@dataclass(frozen=True)
class PublicationResult:
    state: str
    publication: str | None
    head: bytes | None
    error: str | None


class PublisherHandoff:
    """Trusted in-process owner adapter; one nonqueued write, separate reads."""
    def __init__(self,store,authority):
        p.need(type(authority) is SyntheticHandoffAuthority and store.authority is authority,'ACCESS_DENIED')
        with authority.lock:
            p.need(authority._store is None or authority._store is store,'ACCESS_DENIED');authority._store=store
        self.store=store;self.authority=authority;self._slot=authority._slot

    def publish(self,h):
        p.need(self._slot.acquire(False),'BUSY');identity=None
        try:
            m,candidate=recognize(h);identity=m['publication']['id']
            binding=candidate.binding();association={k:binding[k] for k in ('owner','camera','camera_binding','request')}
            with self.authority.lock:
                p.need(self.authority._active is None and self.authority._allowed(m),'ACCESS_DENIED')
                self.authority._active=(m,association)
            if identity is None:identity=self.store.create(candidate)
            head=self.store.publish(identity,m['publication']['key'],candidate,expected_head=m['publication']['expected_head'])
            return PublicationResult('COMMITTED',identity,p.canonical(head),None)
        except p.PublicationError as exc:
            return PublicationResult('COMMIT_OUTCOME_UNKNOWN' if exc.code=='COMMIT_OUTCOME_UNKNOWN' else 'REFUSED',identity,None,exc.code)
        finally:
            with self.authority.lock:self.authority._active=None
            self._slot.release()
