"""D2.1 synthetic publication recognition; no IO, native verifier or public API."""
from dataclasses import asdict, dataclass, replace
import hashlib
import json
import re
from . import contracts as a, wire, composition
from .worker_protocol import Attempt, CONFIG_DIGEST

FORMAT='nanexus-hv2-generation/1'
POLICY='d2.1-synthetic-offline-v1'
PROVIDER='frigate-0.18.2-cfa08c5-h264-mp4-offline-v1'
READER='h264-avc1-single-file-v1'
MAX_META=65536
MAX_OBJECT=2<<20
MAX_GENERATION=8<<20

class PublicationError(ValueError):
    def __init__(self, code): self.code=code;super().__init__(code)

def need(ok, code='CORRUPT'):
    if not ok: raise PublicationError(code)

def digest(data): return hashlib.sha256(data).hexdigest()
def canonical(value): return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def fields(value, names): need(type(value) is dict and set(value)==set(names))
def number(value, maximum, minimum=0): need(type(value) is int and minimum<=value<=maximum)
def text(value): need(type(value) is str and 0<len(value)<=256 and all(ord(c)>=32 for c in value))
def hexid(value, size=64): need(type(value) is str and re.fullmatch('[0-9a-f]{'+str(size)+'}',value) is not None)

def _pairs(values):
    d={}
    for k,v in values:
        need(k not in d);d[k]=v
    return d

def load(raw, cap=MAX_META):
    need(type(raw) is bytes and 0<len(raw)<=cap)
    try:
        v=json.loads(raw,object_pairs_hook=_pairs,parse_constant=lambda _:need(False))
        need(canonical(v)==raw);return v
    except (ValueError,TypeError,UnicodeError,RecursionError) as e:
        raise PublicationError('CORRUPT') from e

def descriptor(raw): return {'sha256':digest(raw),'size':len(raw)}
def check_descriptor(d, cap):
    fields(d,('sha256','size'));hexid(d['sha256']);number(d['size'],cap,1)
def check_bytes(raw,d,cap):
    check_descriptor(d,cap);need(type(raw) is bytes and len(raw)==d['size'] and digest(raw)==d['sha256'])

def cursor(publication,generation,index):
    return 'd2p1.'+generation+'.'+str(index)+'.'+digest(canonical([publication,generation,index,'ordered-pages-v1']))

def predecessor(d):
    if d is not None:
        fields(d,('generation','sequence','sha256','size'));hexid(d['generation']);number(d['sequence'],2**63-1,1)
        check_descriptor({k:d[k] for k in ('sha256','size')},MAX_META)

def split_updates(raw):
    """Preserve exact original B object bytes, without canonicalizing reports."""
    need(type(raw) is bytes and len(raw)<=MAX_OBJECT)
    try:
        s=raw.decode();need(s.startswith('[') and s.endswith(']'));at=1;out=[]
        decoder=json.JSONDecoder(object_pairs_hook=_pairs)
        while at<len(s)-1:
            need(len(out)<1025 and s[at]=='{')
            _,end=decoder.raw_decode(s,at);item=s[at:end].encode();need(len(item)<=1<<20);out.append(item);at=end
            if at<len(s)-1:need(s[at]==',');at+=1
        need(at==len(s)-1);return tuple(out)
    except (ValueError,UnicodeError,RecursionError) as e:raise PublicationError('CORRUPT') from e

@dataclass(frozen=True)
class Candidate:
    request: a.Request
    attempt: Attempt
    sources: tuple
    updates: tuple
    result: composition.Composition
    operational: str | None = None
    provider: str = PROVIDER
    reader: str = READER
    worker_binary: str = '0'*64  # Explicit synthetic model, not executable attestation.
    policy: str = POLICY

    def validate(self):
        need(self.policy==POLICY and self.provider==PROVIDER and self.reader==READER,'UNSUPPORTED')
        hexid(self.worker_binary)
        need(all('/' not in v and '\\' not in v for v in (self.attempt.source,self.attempt.generation,self.attempt.cut)),'UNSUPPORTED')
        need(type(self.attempt) is Attempt and self.attempt.profile=='native-hv2-d1.5-v1' and self.attempt.policy=='native-single-source-v1','UNSUPPORTED')
        need(self.result.page.lifecycle is not a.Lifecycle.COMPLETE and not self.result.page.output_complete,'UNSUPPORTED')
        need(type(self.sources) is tuple and len(self.sources)<=1024 and type(self.updates) is tuple and len(self.updates)<=1025)
        need(sum(len(r) for r in self.updates)<=MAX_OBJECT)
        need(len(self.result.objects)<=64 and sum(len(b) for _,b in self.result.objects)<=MAX_GENERATION)
        try: composition.validate(self.result,self.request,self.attempt,self.sources,self.updates,operational=self.operational)
        except (ValueError,KeyError,TypeError) as e:raise PublicationError('CORRUPT') from e
        need(all(o.placement.status is a.PlacementStatus.UNPLACED for o in self.result.page.observations))
        need(all(c.state is a.CoverageState.UNKNOWN for c in self.result.page.coverage))
        return self

    def identity(self,*,validate=True):
        # Expired idempotency compares canonical input without recomposition.
        if validate:self.validate()
        return digest(canonical({'base':descriptor(wire.dumps(self.result.page)),
            'objects':[[k,descriptor(v)] for k,v in self.result.objects],
            'operational':self.operational,'provider':self.provider,'reader':self.reader,
            'worker_binary':self.worker_binary,'policy':self.policy}))

    def binding(self):
        s=self.attempt
        return {'request':digest(wire.dumps(self.request)), 'request_id':s.request,
            'result_job':self.result.page.job_id,'run':s.run,'attempt':s.attempt,
            'owner':s.principal,'camera':s.camera,'camera_binding':s.binding,
            'provider':self.provider,'reader':self.reader,'worker':s.profile,
            'worker_binary':self.worker_binary,'configuration':s.configuration,
            'source':s.source,'source_generation':s.generation,'processing_digest':s.source_digest,
            'cut':s.cut,'ledger_digest':digest(canonical(self.sources)),
            'trust':s.trust_digest,'trust_profile':'public-ca-'+s.trust_digest,'policy':self.policy}

@dataclass(frozen=True)
class Generation:
    manifest: bytes
    pages: tuple[bytes,...]
    objects: tuple[tuple[str,bytes],...]


def _observations(candidate):
    page=candidate.result.page;obs=list(page.observations);objects=dict(candidate.result.objects)
    if page.next_cursor is not None:
        more=load(objects[page.next_cursor],MAX_OBJECT)
        fields(more,('run','offset','observations'));need(more['run']==candidate.attempt.run and more['offset']==128)
        need(type(more['observations']) is list and len(more['observations'])<=1024)
        for o in more['observations']:obs.append(wire.loads(canonical(o)))
    need(len(obs)<=2048 and len({o.observation_id for o in obs})==len(obs))
    return obs


def build(publication,sequence,previous,key,candidate):
    hexid(publication,32);number(sequence,2**63-1,1);predecessor(previous);hexid(key)
    need(sequence==(1 if previous is None else previous['sequence']+1))
    input_digest=candidate.identity()
    gid=digest(canonical([publication,sequence,previous,input_digest]))
    base=candidate.result.page;obs=_observations(candidate);pages=[]
    count=max(1,(len(obs)+127)//128);need(count<=16,'QUOTA')
    for index in range(count):
        page=replace(base,observations=tuple(obs[index*128:(index+1)*128]),
            next_cursor=cursor(publication,gid,index+1) if index+1<count else None)
        pages.append(wire.dumps(page))
    objects=tuple(candidate.result.objects)+(('composition-base',wire.dumps(base)),)
    need(len(objects)<=64)
    entries=[]
    profiles={e.evidence_id:e.profile for e in base.evidence}
    for identity,raw in objects:
        need(len(raw)<=MAX_OBJECT,'QUOTA')
        entries.append({'id':identity,**descriptor(raw),'profile':profiles.get(identity,'a-original-result-page-v1')})
    tails=[x['id'] for x in entries if x['profile']=='d1.5-tail-v1'];need(len(tails)==1)
    m={'schema':FORMAT,'publication':publication,'generation':gid,'sequence':sequence,
        'previous':previous,'input':input_digest,'key':key,'binding':candidate.binding(),
        'operational':candidate.operational,'lifecycle':base.lifecycle.value,'output_complete':False,
        'policy':POLICY,'pagination':'ordered-pages-v1','all_pages_present':True,
        'base':'composition-base','tail':tails[0], 'objects':entries,
        'pages':[{'ordinal':i,**descriptor(raw),'observations':len(wire.loads(raw).observations),
                  'next':wire.loads(raw).next_cursor} for i,raw in enumerate(pages)],'finalized':len(obs)}
    raw=canonical(m);need(len(raw)<=MAX_META,'QUOTA')
    need(len(raw)+sum(map(len,pages))+sum(len(v) for _,v in objects)<=MAX_GENERATION,'QUOTA')
    return Generation(raw,tuple(pages),objects)


def manifest(raw):
    m=load(raw);fields(m,('schema','publication','generation','sequence','previous','input','key','binding','operational','lifecycle','output_complete','policy','pagination','all_pages_present','base','tail','objects','pages','finalized'))
    need(m['schema']==FORMAT and m['policy']==POLICY and m['pagination']=='ordered-pages-v1','UNSUPPORTED')
    hexid(m['publication'],32)
    for k in ('generation','input','key'):hexid(m[k])
    number(m['sequence'],2**63-1,1);predecessor(m['previous'])
    need(m['sequence']==(1 if m['previous'] is None else m['previous']['sequence']+1))
    need(type(m['all_pages_present']) is bool and m['all_pages_present'] and type(m['output_complete']) is bool and not m['output_complete'])
    need(m['operational'] in (None,'cancel','revoked','resource','failure','interrupted'))
    need(m['lifecycle'] in ('INCOMPLETE','STOPPED','FAILED','CANCELLED','INTERRUPTED'))
    b=m['binding'];fields(b,('request','request_id','result_job','run','attempt','owner','camera','camera_binding','provider','reader','worker','worker_binary','configuration','source','source_generation','processing_digest','cut','ledger_digest','trust','trust_profile','policy'))
    for v in b.values():text(v)
    for k in ('request','worker_binary','configuration','processing_digest','ledger_digest','trust'):hexid(b[k])
    need(b['provider']==PROVIDER and b['reader']==READER and b['worker']=='native-hv2-d1.5-v1' and b['policy']==POLICY and b['configuration']==CONFIG_DIGEST,'UNSUPPORTED')
    need(type(m['objects']) is list and 1<=len(m['objects'])<=64 and type(m['pages']) is list and 1<=len(m['pages'])<=16)
    ids=set();total=len(raw)
    for d in m['objects']:
        fields(d,('id','sha256','size','profile'));text(d['id']);text(d['profile']);need('/' not in d['id'] and '\\' not in d['id']);need(d['id'] not in ids);ids.add(d['id'])
        check_descriptor({k:d[k] for k in ('sha256','size')},MAX_OBJECT);total+=d['size']
    text(m['base']);text(m['tail']);need(m['base'] in ids and m['tail'] in ids)
    for i,d in enumerate(m['pages']):
        fields(d,('ordinal','sha256','size','observations','next'));number(d['ordinal'],15);need(d['ordinal']==i)
        check_descriptor({k:d[k] for k in ('sha256','size')},wire.MAX_DOCUMENT_BYTES);number(d['observations'],128)
        need(d['next']==(cursor(m['publication'],m['generation'],i+1) if i+1<len(m['pages']) else None));total+=d['size']
    number(m['finalized'],2048);need(sum(p['observations'] for p in m['pages'])==m['finalized']);need(total<=MAX_GENERATION,'QUOTA')
    need(m['generation']==digest(canonical([m['publication'],m['sequence'],m['previous'],m['input']])))
    return m


def recognize(raw, read_object, read_page):
    """Resolvers are trusted store-only bounded readers, never client callbacks."""
    m=manifest(raw);objects={}
    for d in m['objects']:
        value=read_object(d);check_bytes(value,{k:d[k] for k in ('sha256','size')},MAX_OBJECT);objects[d['id']]=value
    try:
        base=wire.loads(objects[m['base']]);need(type(base) is a.ResultPage)
        refs={e.evidence_id:e for e in base.evidence};need(set(objects)==set(refs)|{m['base']})
        for d in m['objects']:
            if d['id']!=m['base']:
                r=refs[d['id']];need((d['sha256'],d['size'],d['profile'])==(r.sha256,r.size,r.profile))
        def by_profile(p):
            ids=[k for k,e in refs.items() if e.profile==p];need(len(ids)==1);return objects[ids[0]]
        source=load(by_profile('d1.5-sources-v1'),MAX_OBJECT);fields(source,('attempt','sources'))
        spec=Attempt(**source['attempt']);sources=tuple(source['sources'])
        raws=split_updates(by_profile('d1.5-updates-v1'))
        result=composition.Composition(base,tuple((e.evidence_id,objects[e.evidence_id]) for e in base.evidence))
        candidate=Candidate(base.request,spec,sources,raws,result,m['operational'],m['binding']['provider'],m['binding']['reader'],m['binding']['worker_binary'],m['policy'])
        expected=build(m['publication'],m['sequence'],m['previous'],m['key'],candidate)
        need(expected.manifest==raw)
        for i,d in enumerate(m['pages']):
            page=read_page(d);check_bytes(page,{k:d[k] for k in ('sha256','size')},wire.MAX_DOCUMENT_BYTES)
            need(page==expected.pages[i]);need(wire.dumps(wire.loads(page))==page)
        return m
    except (ValueError,TypeError,KeyError,RecursionError) as e:
        if isinstance(e,PublicationError):raise
        raise PublicationError('CORRUPT') from e

CONTROL_V1='nanexus-hv2-control/1'
CONTROL_V2='nanexus-hv2-control/2'


def deletion_files(m):
    """Physical generation-local files, never predecessor history targets."""
    objects={d['sha256']:d for d in m['objects']}
    files=[{'kind':'object','sha256':h,'size':objects[h]['size'],'ordinal':None}
           for h in sorted(objects)]
    files += [{'kind':'page','sha256':d['sha256'],'size':d['size'],'ordinal':d['ordinal']} for d in m['pages']]
    return files


def deletion_plan(value):
    fields(value,('root','publication','target','key','input','incarnation','operation','protected','files','directories'))
    hexid(value['root'],32);hexid(value['publication'],32);predecessor(value['target']);need(value['target'] is not None)
    for k in ('key','input','operation'):hexid(value[k])
    hexid(value['incarnation'],32)
    need(type(value['protected']) is list and 1<=len(value['protected'])<=2)
    for d in value['protected']:predecessor(d);need(d is not None and d['generation']!=value['target']['generation'])
    fs=value['files'];need(type(fs) is list and 1<=len(fs)<=81)
    objects=[];pages=[];total=0
    for i,d in enumerate(fs):
        fields(d,('kind','sha256','size','ordinal','inode'));hexid(d['sha256']);number(d['size'],MAX_OBJECT,1);number(d['inode'],2**63-1,1);total+=d['size']
        need(d['kind'] in ('object','page','manifest'))
        if d['kind']=='object':need(not pages and i<len(fs)-1 and d['ordinal'] is None);objects.append(d['sha256'])
        elif d['kind']=='page':number(d['size'],wire.MAX_DOCUMENT_BYTES,1);number(d['ordinal'],15);need(d['ordinal']==len(pages) and i<len(fs)-1);pages.append(d)
        else:need(i==len(fs)-1 and d['ordinal'] is None and d['sha256']==value['target']['sha256'] and d['size']==value['target']['size'])
    need(fs[-1]['kind']=='manifest' and objects==sorted(set(objects)) and len(objects)<=64 and 1<=len(pages)<=16 and total<=MAX_GENERATION)
    fields(value['directories'],('generation','objects','pages'))
    for n in value['directories'].values():number(n,2**63-1,1)
    need(len(set(value['directories'].values()))==3)
    return value


def control(c,root,publication):
    """Closed v1/v2 recognition. Opening a store never changes its version."""
    need(type(c) is dict and c.get('schema') in (CONTROL_V1,CONTROL_V2),'UNSUPPORTED')
    v2=c['schema']==CONTROL_V2
    fields(c,('schema','root','publication','revision','head','retained','attempts','reserved','checksum')+(('maintenance',) if v2 else ()))
    hexid(c['checksum']);need(c['checksum']==digest(canonical({k:v for k,v in c.items() if k!='checksum'})))
    need(c['root']==root and c['publication']==publication);number(c['revision'],2**63-1)
    need(type(c['retained']) is list and len(c['retained'])<=3 and type(c['attempts']) is list and len(c['attempts'])<=16)
    gids=set()
    for d in c['retained']:
        predecessor(d);need(d is not None and d['generation'] not in gids);gids.add(d['generation'])
    predecessor(c['head']);need(c['head'] is None or c['head'] in c['retained']);need((not c['retained'])==(c['head'] is None))
    if c['head']:need(c['head']['sequence']==max(d['sequence'] for d in c['retained']))
    keys=set();reserved=[];expired={}
    for entry in c['attempts']:
        need(type(entry) is dict)
        is_expired=entry.get('status')=='EXPIRED'
        fields(entry,('key','input','generation','status','head')+(('retired_revision',) if is_expired and v2 else ()))
        for k in ('key','input','generation'):hexid(entry[k])
        need(entry['key'] not in keys);keys.add(entry['key'])
        need(entry['status'] in (('RESERVED','INTERRUPTED','COMMITTED','EXPIRED') if v2 else ('RESERVED','INTERRUPTED','COMMITTED')))
        predecessor(entry['head'])
        if entry['status']=='COMMITTED':need(entry['head'] in c['retained'] and entry['head']['generation']==entry['generation'])
        elif is_expired:
            need(entry['head'] is not None and entry['head']['generation']==entry['generation'] and entry['generation'] not in gids and entry['generation'] not in expired)
            number(entry['retired_revision'],c['revision'],1);expired[entry['generation']]=entry
        else:need(entry['head'] is None)
        if entry['status']=='RESERVED':reserved.append(entry['generation'])
    need(len(reserved)<=1 and c['reserved']==(reserved[0] if reserved else None))
    need(len({d['sequence'] for d in c['retained']})==len(c['retained']))
    need(all(sum(e['status']=='COMMITTED' and e['head']==d for e in c['attempts'])==1 for d in c['retained']))
    if v2 and c['maintenance'] is not None:
        maintenance=c['maintenance'];fields(maintenance,('plan','digest','next','phase'));plan=deletion_plan(maintenance['plan'])
        hexid(maintenance['digest']);need(maintenance['digest']==digest(canonical(plan)))
        need(plan['root']==root and plan['publication']==publication and plan['target']['generation'] in expired)
        e=expired[plan['target']['generation']]
        need((e['key'],e['input'],e['head'])==(plan['key'],plan['input'],plan['target']))
        need(plan['protected']==c['retained'] and c['head'] in plan['protected'] and c['reserved'] is None)
        number(maintenance['next'],len(plan['files'])+3)
        need(maintenance['phase']==('PENDING' if maintenance['next']==0 else 'DELETING'))
    return c
