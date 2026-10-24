"""Opt-in chronological backend. Synchronous caller; one volatile owner slot.

WindowOperations reuses the existing Frigate Access.grant authority and the
Companion camera resolver. No listener, scheduler, persistence or new authority.
"""
from decimal import Decimal
import json
import threading
import time
import uuid
from .prototype import Coordinator, DirectCapturedProfile, DirectApiProfile, PrototypeFailure
from .window_results import Bundle, ResultLimit, summary_bytes
from ..frigate.historical_v2_api import WindowApiProvider
from ..frigate.historical_v2 import FrigateHistoricalProvider
from .window_inventory import window_inventory


class WindowCapturedProvider(FrigateHistoricalProvider):
    inventory_page = staticmethod(window_inventory)
    max_inventory_items = 257

MAX_NALS = 262144
MAX_BYTES = 2 << 30
TTL = 600
OPERATION_SECONDS = 615


def windows(count):
    if type(count) is not int or not 0 <= count <= 256: raise ValueError('window plan cap')
    start = 0
    while start < count:
        end = min(start+30,count)
        yield tuple(range(start,end))
        if end == count: break
        start = end-1


class WindowCoordinator(Coordinator):
    max_interval = 1800
    max_files = 256
    core_seconds = 600
    resource_profile = 'window-direct-experimental-v1'
    execution_profile = 'direct-independent-window-experimental-v1'
    captured_provider = WindowCapturedProvider
    api_provider = WindowApiProvider
    window_mode = True

    def __init__(self,profile,*,clock=time.monotonic,observer=None):
        if type(profile) not in (DirectCapturedProfile,DirectApiProfile):
            raise TypeError('explicit direct window profile required')
        super().__init__(profile)
        self._clock=clock; self._result_lock=threading.RLock(); self._record=None
        self._observer=observer

    def verify(self,*args,**kwargs):
        raise TypeError('window profile requires owner-bound run')

    def _drop(self,record):
        timer=record.get('timer')
        if timer:timer.cancel()
        record['bundle'].clear();record['summary']=None
        if self._record is record:self._record=None

    def _expire(self):
        if self._record and not self._record['running'] and self._clock() >= self._record['expires']:
            self._drop(self._record)

    def _timer_expire(self,record):
        with self._result_lock:
            if self._record is record:self._expire()

    def run(self,owner,camera,start,end,request_id,permission):
        permission()
        if type(owner) is not str or not 0<len(owner)<=256: raise PrototypeFailure('UNAUTHORIZED')
        with self._result_lock:
            self._expire()
            if self._record is not None: raise PrototypeFailure('BUSY')
            record=dict(owner=owner,camera=camera,request_id=request_id,running=True,
                        bundle=Bundle(),summary=None,expires=None,deadline=self._clock()+OPERATION_SECONDS)
            self._record=record
        try:
            response=super().verify(camera,start,end,request_id,permission)
            permission()
            if response['status']!='RESULT' and ('reason' not in response or response['reason']=='SELECTION_TOO_LARGE'):
                response['reason']={'LIMIT':'Verification exceeded an input, result or execution limit.',
                    'CANCELLED':'Verification was cancelled; no further files were processed.',
                    'NO_MATCHING_RECORDING':'No recordings were selected in this bounded catalog observation.'
                    }.get(response['status'],'Verification stopped after an operational failure.')
            with self._result_lock:
                if self._closed or self._record is not record:raise PrototypeFailure('UNAVAILABLE')
            if self._clock() >= record['deadline']:
                response.update(status='LIMIT',reason='Operation exceeded its delivery deadline.')
            response.update(schema='nanexus-hv2-window-summary/1',profile=self.execution_profile,
                            files=record['bundle'].files,retained_bytes=record['bundle'].bytes,
                            summary_only=True,origin='HOLD',continuity='UNKNOWN',coverage='UNKNOWN')
            try: raw=summary_bytes(response)
            except ResultLimit:
                record['bundle'].clear()
                raw=summary_bytes(dict(schema='nanexus-hv2-window-summary/1',request_id=request_id,camera=camera.native_key,
                                       status='LIMIT',reason='Result exceeded its summary limit.',files=[],summary_only=True,
                                       origin='HOLD',continuity='UNKNOWN',coverage='UNKNOWN'))
            with self._result_lock:
                if self._closed or self._record is not record:raise PrototypeFailure('UNAVAILABLE')
                record.update(running=False,summary=raw,expires=self._clock()+TTL)
                timer=threading.Timer(TTL,self._timer_expire,args=(record,));timer.daemon=True
                record['timer']=timer;timer.start()
            return json.loads(raw)
        except MemoryError:
            with self._result_lock:self._drop(record)
            return dict(schema='nanexus-hv2-window-summary/1',request_id=request_id,camera=camera.native_key,
                        status='LIMIT',reason='Verification exceeded its memory limit.',files=[],summary_only=True)
        except Exception as exc:
            # The rolling product may already have published immutable local
            # findings. Cancellation ends execution, not their request lifetime.
            if (getattr(self,'product_mode',False) and isinstance(exc,PrototypeFailure)
                    and exc.code=='CANCELLED' and not self._closed):
                with self._result_lock:
                    response=dict(schema='nanexus-hv2-window-summary/1',profile=self.execution_profile,
                        request_id=request_id,camera=camera.native_key,status='CANCELLED',
                        reason='Verification cancelled; earlier scoped findings remain available.',
                        files=record['bundle'].files,processed=len(record['bundle'].files),
                        unprocessed=record.get('planned_count',0)-len(record['bundle'].files),
                        retained_bytes=record['bundle'].bytes,summary_only=True,
                        origin='HOLD',continuity='UNKNOWN',coverage='UNKNOWN')
                    raw=summary_bytes(response)
                    record.update(running=False,summary=raw,expires=self._clock()+TTL)
                    timer=threading.Timer(TTL,self._timer_expire,args=(record,));timer.daemon=True
                    record['timer']=timer;timer.start()
                    return json.loads(raw)
            with self._result_lock:self._drop(record)
            if isinstance(exc,PrototypeFailure):raise
            return dict(schema='nanexus-hv2-window-summary/1',request_id=request_id,camera=camera.native_key,
                        status='EXECUTION_FAILED',reason='Verification stopped after an operational failure.',files=[],summary_only=True)
        except BaseException:
            with self._result_lock:
                self._drop(record)
            raise

    def _admit(self,owner,camera,request_id,permission):
        permission()
        self._expire(); record=self._record
        if record is None or record['request_id']!=request_id: raise PrototypeFailure('UNAVAILABLE')
        if record['owner']!=owner or record['camera']!=camera: raise PrototypeFailure('UNAUTHORIZED')
        return record

    def summary(self,owner,camera,request_id,permission):
        with self._result_lock:
            record=self._admit(owner,camera,request_id,permission)
            if record['running']: return dict(request_id=request_id,state='RUNNING')
            return json.loads(record['summary'])

    def evidence(self,owner,camera,request_id,identity,offset,count,permission):
        with self._result_lock:
            record=self._admit(owner,camera,request_id,permission)
            if record['running']: raise PrototypeFailure('BUSY')
            try: return record['bundle'].chunk(identity,offset,count)
            except KeyError as exc: raise PrototypeFailure('UNAVAILABLE') from exc
            except ValueError as exc: raise PrototypeFailure('UNSUPPORTED') from exc

    def release(self,owner,camera,request_id,permission):
        with self._result_lock:
            record=self._admit(owner,camera,request_id,permission)
            if record['running']: raise PrototypeFailure('BUSY')
            self._drop(record)
        if record.get('timer'):record['timer'].join(1)
        return dict(request_id=request_id,released=True)

    def cancel_owned(self,owner,camera,request_id,permission):
        with self._result_lock:
            self._admit(owner,camera,request_id,permission)
            return super().cancel(camera.native_key,request_id)

    def close(self):
        super().close()
        with self._result_lock:
            record=self._record
            if record:self._drop(record)
        if record and record.get('timer'):record['timer'].join(1)

    def _verify_many(self,provider,items,bounds,cancel,request,camera,request_id,permission,event,deadline,check,response):
        response.update(windows=[],processed=0,unprocessed=len(items),input={'nal_occurrences':0,'annex_b_bytes':0})
        bundle=self._record['bundle']
        ordered=tuple(items)
        plan=tuple(windows(len(ordered)))
        if self._observer:self._observer('plan',ordered,None)
        for wi,members in enumerate(plan):
            check()
            overlap=ordered[members[0]].item_id if wi else None
            response['windows'].append(dict(members=[ordered[i].item_id for i in members],overlap=overlap))
            for index in members[1:] if wi else members:
                check(); item=ordered[index]
                entry=dict(recording=item.item_id,generation=item.generation,
                           metadata_interval=dict(start=item.metadata_interval.start,end=item.metadata_interval.end),
                           status='UNPROCESSED',result=None,execution=None)
                self._verify_file(provider,item,bounds,cancel,request,camera,request_id,permission,event,deadline,check,entry)
                check()
                if entry['status']!='RESULT': raise PrototypeFailure(entry['status'])
                for key,limit in (('nal_occurrences',MAX_NALS),('annex_b_bytes',MAX_BYTES)):
                    response['input'][key]+=entry['input'][key]
                    if response['input'][key]>limit: raise PrototypeFailure('LIMIT')
                try: bundle.add_file(entry,request,provider.snapshot.sha256)
                except ResultLimit as exc: raise PrototypeFailure('LIMIT') from exc
                if self._observer:self._observer('file',entry,dict(bundle.files[-1]))
                response['processed']+=1; response['unprocessed']-=1
                del entry
        check()
        response['status']='RESULT'


class WindowOperations:
    """Trusted backend facade, using existing Access.grant and camera resolver.

    Caller supplies the existing authority, never browser-provided principal or
    guard. No credentials are retained; every operation obtains a fresh grant.
    Methods return (bounded document, refreshed cookie) for a future adapter.
    """
    def __init__(self,access,coordinator,resolve_camera):
        self.access=access; self.coordinator=coordinator; self.resolve_camera=resolve_camera

    def _grant(self,cookie,camera):
        owner,guard,refresh=self.access.grant(cookie,camera)
        admitted=self.resolve_camera(camera,guard)
        def permission():
            # Native session grant at each facade access; frequent in-flight
            # Reader checks use the accepted local binding guard, not HTTP per NAL.
            if self.resolve_camera(camera,guard)!=admitted:
                raise PrototypeFailure('UNAUTHORIZED')
        return owner,admitted,refresh,permission

    def verify(self,cookie,camera,start,end):
        owner,admitted,refresh,permission=self._grant(cookie,camera)
        document=self.coordinator.run(owner,admitted,start,end,uuid.uuid4().hex,permission)
        current,actual,refresh,_=self._grant(cookie,camera)
        if current!=owner or actual!=admitted:raise PrototypeFailure('UNAUTHORIZED')
        return document,refresh

    def summary(self,cookie,camera,request_id):
        owner,admitted,refresh,permission=self._grant(cookie,camera)
        return self.coordinator.summary(owner,admitted,request_id,permission),refresh

    def evidence(self,cookie,camera,request_id,identity,offset,count):
        owner,admitted,refresh,permission=self._grant(cookie,camera)
        return self.coordinator.evidence(owner,admitted,request_id,identity,offset,count,permission),refresh

    def release(self,cookie,camera,request_id):
        owner,admitted,refresh,permission=self._grant(cookie,camera)
        return self.coordinator.release(owner,admitted,request_id,permission),refresh

    def cancel(self,cookie,camera,request_id):
        owner,admitted,refresh,permission=self._grant(cookie,camera)
        return self.coordinator.cancel_owned(owner,admitted,request_id,permission),refresh
