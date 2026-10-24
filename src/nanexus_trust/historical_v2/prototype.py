"""Opt-in bounded captured-file prototype. Independent real B; volatile delivery."""
from contextlib import closing, contextmanager
from dataclasses import dataclass, replace
from decimal import Decimal
import base64
import json
from pathlib import Path
import threading
import time
import uuid
from . import contracts as a, composition as c, wire
from .provider import Bounds, DiscoveryRequest
from .media import read_nals, ReaderLimits, SourceLedger, MediaFailure
from .placement import admit_single_file, AdmissionFailure
from .worker_protocol import Attempt, sha
from .worker_supervisor import Supervisor
from ..frigate.historical_v2_api import ApiProvider, API_PROFILE, endpoint
from ..frigate.historical_v2 import FrigateHistoricalProvider, Snapshot, PROFILE_0182, PIN_0182, ProviderFailure

SCHEMA='nanexus-hv2-prototype/1'
MAX_RESPONSE=4<<20

class PrototypeFailure(ValueError):
    def __init__(self,code):self.code=code;super().__init__(code)

@dataclass(frozen=True)
class CapturedProfile:
    snapshot: Snapshot
    media_root: Path
    scratch_root: Path
    worker: Path
    public_ca: Path
    ffprobe: Path
    provider_profile: str = PROFILE_0182
    def __post_init__(self):
        if self.provider_profile!=PROFILE_0182 or self.snapshot.source_pin!=PIN_0182:
            raise ValueError('prototype captured profile unsupported')

@dataclass(frozen=True)
class ApiProfile:
    origin: str
    token_file: Path | None
    media_root: Path
    scratch_root: Path
    worker: Path
    public_ca: Path
    ffprobe: Path
    provider_profile: str = API_PROFILE
    def __post_init__(self):
        endpoint(self.origin)
        if self.provider_profile!=API_PROFILE:raise ValueError('unsupported API profile')

@dataclass(frozen=True)
class DirectCapturedProfile(CapturedProfile):
    """Explicit experimental input choice; staged defaults remain unchanged."""

@dataclass(frozen=True)
class DirectApiProfile(ApiProfile):
    """Same trusted API inventory; direct read-only original media input."""

class _Cancel:
    def __init__(self,event):self.event=event
    def cancelled(self):return self.event.is_set()

class _Source:
    def __init__(self,nals,generation,source):
        self.nals=tuple(nals);self.generation=generation;self.source=source
        self.digest=sha(b''.join(n.annex_b for n in nals))
    def describe(self):return dict(source=self.source,generation=self.generation,digest=self.digest,bytes=sum(len(n.annex_b) for n in self.nals),count=len(self.nals))
    def occurrences(self):return tuple((n.token,len(n.annex_b),sha(n.annex_b)) for n in self.nals)
    def read(self,i):return self.generation,self.nals[i].token,self.nals[i].annex_b

class _Authority:
    def __init__(self,spec,check):self.spec=spec;self.check=check
    def authorize(self,spec,checkpoint):
        try:self.check();return spec==self.spec
        except (ValueError,RuntimeError):return False

class Coordinator:
    """One nonqueued Reader+Worker slot, no Live/legacy lock or durable state."""
    captured_provider=FrigateHistoricalProvider
    api_provider=ApiProvider
    window_mode=False
    max_interval=900
    max_files=8
    core_seconds=60
    resource_profile='prototype-multi-file-v1'

    def __init__(self,profile):
        if type(profile) not in (CapturedProfile,ApiProfile,DirectCapturedProfile,DirectApiProfile):raise TypeError('explicit prototype profile required')
        self.profile=profile;self._slot=threading.Lock();self._lock=threading.Lock()
        self._active=None;self._closed=False;self._done=threading.Event();self._done.set()
    def cancel(self,camera,request_id):
        with self._lock:
            if self._active is None or self._active[:2]!=(camera,request_id):raise PrototypeFailure('UNAVAILABLE')
            self._active[2].set()
        return {'schema':SCHEMA,'status':'CANCELLATION_REQUESTED','request_id':request_id,'camera':camera}
    def close(self):
        with self._lock:
            self._closed=True
            if self._active is not None:self._active[2].set()
        if not self._done.wait(23):raise RuntimeError('HV2 shutdown incomplete')
    def verify(self,camera,start,end,request_id,permission):
        # Authority and the one-slot lifecycle are identical for both envelopes.
        request=a.Request(request_id,camera,a.Interval(start,end),'host-metadata-unix-seconds')
        if Decimal(end)-Decimal(start)>self.max_interval:raise PrototypeFailure('LIMIT')
        if not isinstance(request_id,str) or len(request_id)!=32 or any(x not in '0123456789abcdef' for x in request_id):raise PrototypeFailure('UNSUPPORTED')
        permission()
        if not self._slot.acquire(False):raise PrototypeFailure('BUSY')
        event=threading.Event();deadline=time.monotonic()+self.core_seconds
        response={'schema':SCHEMA,'profile':'captured-single-file-native-b-v1','request_id':request_id,
                  'camera':camera.native_key,'status':'EXECUTION_FAILED','selection':None,'execution':None,'result':None,
                  'limitations':['captured-catalog-not-current-retention','whole-file-not-request-clipped',
                                 'historical-placement-unqualified','requested-coverage-UNKNOWN','source-authenticity-not-established']}
        def check():
            permission()
            if event.is_set():raise PrototypeFailure('CANCELLED')
            if time.monotonic()>=deadline:raise PrototypeFailure('LIMIT')
        try:
            with self._lock:
                if self._closed:raise PrototypeFailure('UNAVAILABLE')
                self._active=(camera.native_key,request_id,event);self._done.clear()
            check();profile=self.profile
            if self._direct:response['media_access']='direct-readonly-original-experimental-v1'
            # One overflow sentinel, no discovery paging or context acquisition.
            bounds=Bounds(self.max_files+1,8<<20,0,0,self.max_files,250000,a.Rational(int(deadline*1000000),1000000),self.resource_profile)
            cancel=_Cancel(event)
            def authorized(cam):
                try:permission();return cam==camera
                except (ValueError,RuntimeError):return False
            if type(profile) in (ApiProfile,DirectApiProfile):
                source=self.api_provider(camera,profile.origin,profile.token_file,profile.media_root,profile.scratch_root,native_authorize=authorized)
            else:
                source=self.captured_provider(camera,profile.snapshot,profile.media_root,profile.scratch_root,
                                                native_authorize=authorized,profile=profile.provider_profile)
            with source as provider:
                inventory=provider.discover(DiscoveryRequest(camera,request.interval,request.coordinate,bounds,None),cancel)
                response['selection']={'snapshot':provider.snapshot.sha256,'cut':provider.snapshot.cut_id,'provider':profile.provider_profile,
                    'requested':{'start':start,'end':end},'count':len(inventory.items),'count_exact':inventory.completeness is a.Quality.SATISFIED,'completeness':inventory.completeness.value,
                    'consistency':inventory.consistency.value,'ambiguous':list(inventory.ambiguous_refs),'whole_file':True}
                if type(profile) in (ApiProfile,DirectApiProfile):
                    response['profile']='api-whole-single-file-native-b-v1'
                    response['catalog']={'mode':'recordings-api','capability':provider.profile,
                        'source_pin':PIN_0182,'instance':provider.instance_id,'cut':provider.snapshot.cut_id,
                        'observation':'single-api-response-not-frozen-archive','path_policy':'pinned-standard-UTC-retained-layout'}
                    response['selection']['api_response_sha256']=provider.response_sha256
                    response['limitations'][0]='request-local-api-list-not-retention-lock'
                if len(inventory.items)>self.max_files:
                    response['reason']='SELECTION_TOO_LARGE'
                    response['selection']['count_exact']=inventory.completeness is a.Quality.SATISFIED
                    raise PrototypeFailure('LIMIT')
                if (inventory.completeness is not a.Quality.SATISFIED or inventory.consistency is not a.Quality.SATISFIED
                        or not inventory.terminal or inventory.next_cursor is not None):raise PrototypeFailure('SELECTION_INCOMPLETE')
                if not inventory.items:raise PrototypeFailure('NO_MATCHING_RECORDING')
                if any(i.metadata_interval is None for i in inventory.items):raise PrototypeFailure('SELECTION_INCONSISTENT')
                items=tuple(sorted(inventory.items,key=lambda i:(Decimal(i.metadata_interval.start),i.item_id)))
                if len({i.item_id for i in items})!=len(items):raise PrototypeFailure('SELECTION_INCONSISTENT')
                # Detect alias rows without confusing repeated identical payloads
                # with identical physical files. The pinned adapter owns these
                # registered relative paths; they never enter the response.
                identities=set()
                for item in items:
                    check()
                    if (item.camera!=camera or item.coordinate!=request.coordinate or item.metadata_interval is None
                            or Decimal(item.metadata_interval.start)>=Decimal(end)
                            or Decimal(item.metadata_interval.end)<=Decimal(start)):
                        raise PrototypeFailure('SELECTION_INCONSISTENT')
                    relative=provider._items[item.item_id][1]
                    identity=('path',str(relative))
                    if identity in identities:raise PrototypeFailure('DUPLICATE_RECORDING')
                    identities.add(identity)
                    try:
                        st=(profile.media_root/relative).stat(follow_symlinks=False)
                    except OSError as exc:
                        if self._direct:raise ProviderFailure('source-unavailable-or-unsafe') from exc
                        continue # Staged profile retains file-local disposition.
                    identity=('inode',st.st_dev,st.st_ino)
                    if identity in identities:raise PrototypeFailure('DUPLICATE_RECORDING')
                    identities.add(identity)
                if len(items)==1 and not self.window_mode:
                    item=admit_single_file(inventory,(items[0].item_id,))
                    response['selection'].update(recording=item.item_id,generation=item.generation,
                        metadata_interval={'start':item.metadata_interval.start,'end':item.metadata_interval.end})
                    self._verify_file(provider,item,bounds,cancel,request,camera,request_id,permission,event,deadline,check,response)
                else:
                    self._verify_many(provider,items,bounds,cancel,request,camera,request_id,permission,event,deadline,check,response)
        except PrototypeFailure as exc:response['status']=exc.code
        except ProviderFailure as exc:
            response['status']=self._provider_status(exc)
            if self._direct:response['reason']='Original recording could not be read reliably; verification stopped.'
        except (MediaFailure,AdmissionFailure) as exc:
            response['status']='CANCELLED' if event.is_set() else 'LIMIT' if 'cap' in str(exc) or self._direct and 'deadline' in str(exc) else 'UNSUPPORTED'
            if self._direct:response['reason']='Media inspection failed or exceeded its limits; verification stopped.'
        except (OSError,ValueError,RuntimeError):response['status']='EXECUTION_FAILED';response['result']=None
        finally:
            with self._lock:self._active=None;self._done.set()
            self._slot.release()
        permission() # Binding/revocation failure prohibits returning evidence.
        if len(json.dumps(response,separators=(',',':'),allow_nan=False).encode())>MAX_RESPONSE:
            # No incomplete evidence closure is ever returned as a result.
            response['status']='LIMIT';response['reason']='RESPONSE_TOO_LARGE';response['result']=None
            if 'files' in response:
                response['files']=[];response['evidence_withheld']=True
        return response

    @staticmethod
    def _provider_status(exc):
        if exc.reason.startswith('API_'):
            return 'LIMIT' if exc.reason in ('API_RESPONSE_LIMIT','API_DEADLINE') else exc.reason
        return ('CANCELLED' if exc.reason=='cancelled' else 'UNAUTHORIZED' if 'revoked' in exc.reason
                else 'LIMIT' if any(s in exc.reason for s in ('cap','size-or-type')) else 'SOURCE_UNAVAILABLE')

    def _verify_many(self,provider,items,bounds,cancel,request,camera,request_id,permission,event,deadline,check,response):
        response.update(schema='nanexus-hv2-prototype-window/1',profile=('api-independent-files-native-b-v1' if type(self.profile) in (ApiProfile,DirectApiProfile) else 'captured-independent-files-native-b-v1'),
            files=[],processed=0,unprocessed=len(items),coverage='UNKNOWN',continuity='UNKNOWN')
        response['limitations'].append('independent-files-no-cross-file-signing-continuity')
        selection=response['selection']
        selection['order']=[i.item_id for i in items]
        selection['recordings']=[dict(recording=i.item_id,generation=i.generation,
            metadata_interval=dict(start=i.metadata_interval.start,end=i.metadata_interval.end)) for i in items]
        gaps=[];overlaps=[];cursor=Decimal(request.interval.start)
        for pos,item in enumerate(items):
            left=max(Decimal(request.interval.start),Decimal(item.metadata_interval.start))
            right=min(Decimal(request.interval.end),Decimal(item.metadata_interval.end))
            if left>cursor:gaps.append(dict(start=str(cursor),end=str(left)))
            cursor=max(cursor,right)
            for previous in items[:pos]:
                lo=max(left,Decimal(previous.metadata_interval.start))
                hi=min(right,Decimal(previous.metadata_interval.end))
                if lo<hi:overlaps.append(dict(recordings=[previous.item_id,item.item_id],start=str(lo),end=str(hi)))
        if cursor<Decimal(request.interval.end):gaps.append(dict(start=str(cursor),end=request.interval.end))
        selection['catalog_gaps']=gaps;selection['catalog_overlaps']=overlaps
        selection['relationship_scope']='captured-catalog-only-not-signing-or-source-continuity'
        stop=None;total_nals=0;total_bytes=0
        for item in items:
            entry=dict(recording=item.item_id,generation=item.generation,
                       metadata_interval=dict(start=item.metadata_interval.start,end=item.metadata_interval.end),
                       status='UNPROCESSED',reason=stop,execution=None,result=None)
            if stop is None:
                try:
                    check()
                except PrototypeFailure as exc:
                    if self._direct:raise
                    stop=exc.code;entry['reason']=stop
                if stop is None:
                    response['processed']+=1;response['unprocessed']-=1
                    try:
                        self._verify_file(provider,item,bounds,cancel,request,camera,request_id,permission,event,deadline,check,entry)
                    except PrototypeFailure as exc:
                        if self._direct:raise
                        entry['status']=exc.code
                    except ProviderFailure as exc:
                        if self._direct:raise
                        entry['status']=self._provider_status(exc)
                    except (MediaFailure,AdmissionFailure) as exc:
                        if self._direct:raise
                        entry['status']='CANCELLED' if event.is_set() else 'LIMIT' if 'cap' in str(exc) else 'UNSUPPORTED'
                    except (OSError,ValueError,RuntimeError):
                        if self._direct:raise
                        entry['status']='EXECUTION_FAILED';entry['result']=None
                    if 'input' in entry:
                        total_nals+=entry['input']['nal_occurrences']
                        total_bytes+=entry['input']['annex_b_bytes']
                    if total_nals>8192 or total_bytes>64<<20:raise PrototypeFailure('LIMIT')
                    if self._direct and entry['status']!='RESULT':raise PrototypeFailure(entry['status'])
                    if entry['status'] in ('CANCELLED','LIMIT','INTERRUPTED','UNAUTHORIZED'):stop=entry['status']
            # Each independently recognizable closure binds both catalog and
            # native evidence. No shared Attempt/counters/revisions/tail state.
            entry['closure_sha256']=sha(c.canonical(dict(request_id=request_id,camera=camera.native_key,
                snapshot=provider.snapshot.sha256,cut=provider.snapshot.cut_id,file=entry)))
            response['files'].append(entry)
        permission()
        response['status']=stop or ('RESULT' if all(f['status']=='RESULT' for f in response['files']) else 'PARTIAL')
        response['input']={'nal_occurrences':total_nals,'annex_b_bytes':total_bytes}

    @property
    def _direct(self):
        return type(self.profile) in (DirectCapturedProfile,DirectApiProfile)

    @contextmanager
    def _input(self,provider,item,bounds,cancel,check,limits):
        if self._direct:
            from .direct_media import direct_input
            with direct_input(provider,item,bounds,cancel,check,ffprobe=self.profile.ffprobe,limits=limits) as value:
                yield value
        else:
            handle=provider.acquire(item,bounds,cancel)
            try:
                with closing(read_nals(handle,lambda o,n:provider.read(handle,o,n,cancel),check,
                        ffprobe=self.profile.ffprobe,scratch_root=self.profile.scratch_root,limits=limits)) as reader:
                    yield handle,reader
            finally:provider.release(handle)

    def _verify_file(self, provider, item, bounds, cancel, request, camera, request_id,
                     permission, event, deadline, check, response):
        profile=self.profile
        limits=ReaderLimits(max_bytes=8<<20,max_nals=1024,timeout_seconds=min(5,max(.01,deadline-time.monotonic())))
        with self._input(provider,item,bounds,cancel,check,limits) as (handle,reader):
            nals=[];total=0;ledger=SourceLedger(1024)
            for nal in reader:
                check();total+=len(nal.annex_b)
                if total>8<<20:raise PrototypeFailure('LIMIT')
                ledger.add(nal);nals.append(nal)
            check();facts=tuple({'token':n.token,'packet':n.packet,'nal':n.nal_in_packet,'length_offset':n.length_offset,
                'payload_offset':n.payload_offset,'payload_bytes':n.payload_bytes,'payload_sha256':n.payload_sha256,
                'pts':n.pts,'dts':n.dts,'duration':n.duration,'time_base':str(n.time_base)} for n in nals)
            processing=handle.sha256
            processing_bytes=handle.actual_bytes
        if not nals:raise PrototypeFailure('UNSUPPORTED')
        response['input']={'nal_occurrences':len(nals),'annex_b_bytes':total}
        source=_Source(nals,sha(c.canonical(facts)),item.item_id)
        trust_path=Path(profile.public_ca)
        with trust_path.open('rb') as file:trust=file.read(32769)
        if not 0<len(trust)<=32768:raise PrototypeFailure('UNSUPPORTED')
        sources=tuple(dict(token=t,bytes=n,sha256=h) for t,n,h in source.occurrences())
        desc=source.describe();attempt=uuid.uuid4().hex
        spec=Attempt(attempt,request_id,'placeholder','companion-local',camera.native_key,camera.binding_generation,
                     item.item_id,source.generation,source.digest,desc['bytes'],desc['count'],sha(trust),len(trust),
                     profile='native-hv2-d1.5-v1',cut=provider.snapshot.sha256,policy='native-single-source-v1')
        spec=replace(spec,run=c.run_identity(request,spec,sources));check()
        outcome=Supervisor(profile.worker,_Authority(spec,permission),timeout=min(5,max(.05,deadline-time.monotonic())),no_progress=1).run(spec,source,trust,event)
        response['execution']={'state':outcome.state,'error':outcome.error,'consumed':outcome.consumed,'exit_code':outcome.exit_code,
                               'reaped':outcome.reaped,'updates':len(outcome.updates),'attempt':attempt,'run':spec.run}
        permission()
        if outcome.pid is None or not outcome.reaped:raise PrototypeFailure('EXECUTION_FAILED')
        op={'EXITED_SUCCESSFULLY':None,'CANCELLED':'cancel','RESOURCE_STOPPED':'resource','INTERRUPTED':'interrupted'}.get(outcome.state,'failure')
        if outcome.error in ('protocol-or-startup','adapter-failure') or outcome.state in ('REVOKED','FAILED'):raise PrototypeFailure('EXECUTION_FAILED')
        composed=c.compose(request,spec,sources,outcome.updates,operational=op)
        c.validate(composed,request,spec,sources,outcome.updates,operational=op)
        base=wire.dumps(composed.page)
        response['result']={'page':json.loads(base),'sha256':sha(base),'size':len(base),
            'objects':[{'id':k,'sha256':sha(raw),'size':len(raw),'encoding':'base64','data':base64.b64encode(raw).decode()} for k,raw in composed.objects],
            'source_ledger':facts,'original_file_sha256':processing}
        if type(profile) in (ApiProfile,DirectApiProfile):response['result']['original_file_bytes']=processing_bytes
        response['status']='RESULT' if op is None else {'cancel':'CANCELLED','resource':'LIMIT','interrupted':'INTERRUPTED','failure':'EXECUTION_FAILED'}[op]
