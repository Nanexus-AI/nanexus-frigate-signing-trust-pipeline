"""Bounded offline A composition from admitted B evidence. No durable publisher."""
from dataclasses import asdict, dataclass
import json
from . import contracts as a, wire
from .worker_protocol import sha, CONFIG_DIGEST
from .worker_updates import Admission, decode, require, token_hex


def canonical(v):
    return json.dumps(v,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode()


def source_check(sources):
    require(type(sources) in (tuple,list) and 0<len(sources)<=1024, 'source-count')
    for s in sources:
        require(type(s) is dict and set(s)=={'token','bytes','sha256'}, 'source-fields')
        token_hex(s['token'])
        require(type(s['bytes']) is int and 0<s['bytes']<=1<<20, 'source-size')
        require(type(s['sha256']) is str and len(s['sha256'])==64 and all(c in '0123456789abcdef' for c in s['sha256']), 'source-digest')
    require(len({s['token'] for s in sources})==len(sources) and sum(s['bytes'] for s in sources)<=8<<20, 'source-total')


def run_identity(request, spec, sources):
    source_check(sources)
    binding=asdict(spec);binding.pop('run')
    return 'run-'+sha(canonical({'request':json.loads(wire.dumps(request)), 'attempt':binding,'sources':sources}))


@dataclass(frozen=True)
class Composition:
    page: a.ResultPage
    # Immutable tuples of immutable bytes. Recognition is local, not crash-atomic IO.
    objects: tuple[tuple[str,bytes], ...]


def compose(request, spec, sources, raws, *, operational=None):
    source_check(sources)
    require(request.request_id==spec.request and request.camera.native_key==spec.camera and request.camera.binding_generation==spec.binding, 'request-binding')
    require(spec.run==run_identity(request,spec,sources) and spec.configuration==CONFIG_DIGEST, 'run-binding')
    require(len(sources)==spec.nal_count and sum(s['bytes'] for s in sources)==spec.source_bytes, 'source-plan')
    require(type(raws) is tuple and len(raws)<=1025, 'update-count')
    admission=Admission(spec,[s['token'] for s in sources])
    for raw in raws: admission.add(raw)
    require(operational in (None,'cancel','revoked','resource','failure','interrupted'), 'operational')
    if operational is None: require(admission.ended, 'missing-terminal')
    lifecycle=a.Lifecycle.INCOMPLETE;reason=None
    if operational:
        lifecycle={'cancel':a.Lifecycle.CANCELLED,'revoked':a.Lifecycle.CANCELLED,'resource':a.Lifecycle.STOPPED,'failure':a.Lifecycle.FAILED,'interrupted':a.Lifecycle.INTERRUPTED}[operational]
        if operational=='resource':reason=a.StopReason.RESOURCE_POLICY
    if raws:
        final=decode(raws[-1])
        if final['phase']=='error':
            lifecycle=a.Lifecycle.STOPPED if final['stop'] in (1,2) else a.Lifecycle.FAILED
            reason={1:a.StopReason.RESOURCE_POLICY,2:a.StopReason.INPUT_QUALIFICATION}.get(final['stop'])
    objects={};evidence=[]
    def put(name,raw):
        require(type(raw) is bytes and len(raw)<=2<<20, 'evidence-cap')
        identity=name+'-'+sha(raw);objects[identity]=raw
        evidence.append(a.EvidenceReference(identity,sha(raw),len(raw),'d1.5-'+name+'-v1'));return identity
    source_ref=put('sources',canonical({'attempt':asdict(spec),'sources':sources}))
    update_ref=put('updates',b'['+b','.join(raws)+b']')
    trust_ref=put('trust',canonical({'public_ca_sha256':spec.trust_digest,'configuration':spec.configuration}))
    refs=(source_ref,update_ref,trust_ref);obs=[]
    for n,i in sorted(admission.last.items()):
        if not i['finalized']:continue
        report=admission.reports.get(i['report_generation']);require(report is not None, 'report-reference')
        token=sources[n]['token'];state=(a.SigningState.VERIFIED_SIGNING,a.SigningState.INTEGRITY_NEGATIVE,a.SigningState.NO_RECOGNIZED_SIGNING_EVIDENCE,a.SigningState.INDETERMINATE)[i['state']]
        obs.append(a.Observation('obs-'+sha(canonical([spec.run,n,i['revision']])),spec.run,state,
            a.Support('sample',(token,),sample_ref=token),a.Placement(a.PlacementStatus.UNPLACED,None,None),
            a.Quality.SATISFIED if i['state'] in (0,1) else a.Quality.UNKNOWN,refs,a.Finality.FINALIZED,
            a.ReportReference(str(i['report_generation']),report['generating_feed_ordinal'],i['revision'],(token,),report['retired_prefix_ordinal']),
            'public-ca-'+spec.trust_digest if i['state']==0 else None,refs,('source-authenticity-not-established',)))
    # Missing placeholders are retained losslessly in update evidence. Only an
    # actually finalized containing report scope can support an A order claim.
    # Repeated reports/ambiguous missing membership are kept unresolved, not deduced.
    missing=[]
    for generation,report in admission.reports.items():
        for index,m in enumerate(report['missing']):
            missing.append({'generation':generation,'index':index,**m})
    unresolved=[sources[n]['token'] for n in range(admission.consumed)
                if n not in admission.last or not admission.last[n]['finalized']]
    unprocessed=[s['token'] for s in sources[admission.consumed:]]
    tail=put('tail',canonical({'unresolved_tokens':unresolved,'unprocessed_tokens':unprocessed,'missing':missing}))
    def bounded_refs(values):return tuple(values) if len(values)<=128 else (tail,)
    cursor=None
    if len(obs)>128:
        cursor=put('continuation',canonical({'run':spec.run,'offset':128,'observations':[json.loads(wire.dumps(o)) for o in obs[128:]]}))
    page=a.ResultPage(request,'job-'+sha(canonical([spec.run,spec.attempt])),'native-worker-offline-v1',lifecycle,reason,
        a.Progress(lifecycle,'counts-only',sha(canonical(sources)),admission.consumed,sum(i['finalized'] for i in admission.last.values()),None),
        refs,(source_ref,) if admission.consumed else (),((tail,) if missing and len(unresolved)>=128 else bounded_refs(unresolved)+((tail,) if missing else ())),bounded_refs(unprocessed),False,
        (a.Coverage(request.interval,a.CoverageState.UNKNOWN,'inventory-unqualified',False),),tuple(obs[:128]),tuple(evidence),cursor)
    require(len(wire.dumps(page))<=1<<20 and wire.loads(wire.dumps(page))==page, 'a-wire')
    require(len(set(page.unresolved_refs))==len(page.unresolved_refs), 'unresolved-duplicates')
    return Composition(page,tuple(objects.items()))


def validate(result, request, spec, sources, raws, *, operational=None):
    """Re-recognize independently; no silent fallback to an earlier publication."""
    expected=compose(request,spec,sources,raws,operational=operational)
    require(result==expected, 'composition-binding')
    ids={e.evidence_id:e for e in result.page.evidence};objects=dict(result.objects)
    require(len(objects)==len(result.objects) and set(objects)==set(ids), 'evidence-set')
    for key,raw in objects.items(): require(sha(raw)==ids[key].sha256 and len(raw)==ids[key].size, 'evidence-binding')
    return True
