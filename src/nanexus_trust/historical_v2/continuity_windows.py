"""Explicit four-original experimental branch; never selected by existing launchers."""
from dataclasses import asdict, replace
from pathlib import Path
from .windows import WindowCoordinator, windows
from .contracts import Rational
from .prototype import PrototypeFailure, DirectCapturedProfile, DirectSessionApiProfile
from ..frigate.historical_v2 import PROFILE_0182, PIN_0182, ProviderFailure
from ..frigate.historical_v2_api import ApiCut
from .continuity_protocol import ProtocolError, sha
from .media import ReaderLimits
from .continuity import Plan, require
from .continuity_worker import admit_worker_executable, run_context


class RollingWindowCoordinator(WindowCoordinator):
    max_files=4
    product_mode=False
    context_seconds=45
    resource_profile='qualified-rolling-four-originals-experimental-v1'

    execution_profile='qualified-rolling-four-originals-experimental-v1'

    def __init__(self,profile,resolve_plan,**kwargs):
        captured=type(profile) is DirectCapturedProfile and profile.provider_profile==PROFILE_0182 and profile.snapshot.source_pin==PIN_0182
        # The candidate product plan binds whichever catalog observation the
        # request used; the qualified non-product Plan resolver is captured-only.
        if not captured and not (type(profile) is DirectSessionApiProfile and self.product_mode):
            raise TypeError('pinned direct captured profile required')
        if not callable(resolve_plan):raise TypeError('private trusted Plan resolver required')
        super().__init__(profile,**kwargs);self._resolve_plan=resolve_plan

    def _verify_many(self,provider,items,bounds,cancel,request,camera,request_id,permission,event,deadline,check,response):
        import time,uuid
        bundle=self._record['bundle'];ordered=tuple(items)
        unavailable=set(getattr(self,'_unreadable_sources',()))
        native_items=tuple(i for i in ordered if i.item_id not in unavailable)
        permission();check()
        if type(self.profile) is DirectSessionApiProfile:
            require(type(provider.snapshot) is ApiCut and provider.snapshot.source_pin==PIN_0182 and provider.observed,'qualified-snapshot')
        else:
            require(provider.snapshot==self.profile.snapshot,'qualified-snapshot')
        with open(self.profile.public_ca,'rb') as f:trust=f.read(32769)
        require(0<len(trust)<=32768,'context-trust')
        if self.product_mode:
            self._record['planned_count']=len(ordered)
            from .rolling_product import ProductPlan, WORKER_SHA256
            if not native_items:
                if self._observer:self._observer('plan',ordered,None)
                raise ProviderFailure('source-unavailable-or-unsafe')
            require(time.monotonic()<=deadline-self.core_seconds+30,'product-preparation-deadline')
            admit_worker_executable(self.profile.worker, self.worker_sha256 if self.worker_sha256 is not None else WORKER_SHA256)
            plan=ProductPlan(request,self._record['owner'],native_items,provider.snapshot,sha(trust),
                tuple(str(provider._items[i.item_id][1]) for i in native_items))
            execution_deadline=min(deadline,time.monotonic()+self.context_seconds)
            bounds=replace(bounds,deadline_monotonic=Rational(int(execution_deadline*1000000),1000000))
            self.execution_profile='candidate-rolling-product-v1'
        else:
            # Only trusted assembly supplies this resolver; no browser field or catalog inference.
            plan=self._resolve_plan(request,self._record['owner'],ordered,provider.snapshot,sha(trust))
            require(type(plan) is Plan,'qualified-plan-required')
            require(plan.trust_sha256==sha(trust),'qualified-trust')
            require(tuple((i.item_id,i.generation) for i in ordered)==tuple((i.item_id,i.generation) for i in plan.originals),'qualified-selection')
            if plan.candidate:
                plan.bind_selection(request,self._record['owner'],ordered,provider.snapshot,tuple(str(provider._items[i.item_id][1]) for i in ordered))
            self.execution_profile='candidate-rolling-four-originals-experimental-v1' if plan.candidate else type(self).execution_profile
        permission();check()
        response.update(windows=[],processed=0,unprocessed=len(ordered),input={'nal_occurrences':0,'annex_b_bytes':0})
        # Same 30/29 membership, reference overlap is never re-fed.
        # Native receives only originals that opened. The published plan keeps
        # every catalog overlap so an absent file is not an empty selection.
        seen=set()
        for wi,members in enumerate(windows(len(native_items))):
            response['windows'].append({'members':[native_items[i].item_id for i in members],'overlap':native_items[members[0]].item_id if wi else None})
            for index in members[1:] if wi else members:
                require(index not in seen,'duplicate-window-feed');seen.add(index)
        require(seen==set(range(len(native_items))),'window-plan')
        if self._observer:self._observer('plan',ordered,None)
        def load(index,guard):
            item=native_items[index];guard()
            limits=ReaderLimits(max_bytes=8<<20,max_nals=1024,timeout_seconds=min(5,max(.01,deadline-time.monotonic())))
            with self._input(provider,item,bounds,cancel,check,limits) as (handle,reader):
                nals=[];size=0
                for n in reader:
                    check();size+=len(n.annex_b)
                    if size>8<<20:raise PrototypeFailure('LIMIT')
                    nals.append(n)
                receipt=asdict(handle)
            # Feed only after the direct Reader's final consistency checks.
            response['input']['nal_occurrences']+=len(nals);response['input']['annex_b_bytes']+=size
            return receipt,tuple(nals),{'start':item.metadata_interval.start,'end':item.metadata_interval.end}
        def publish(entry):
            check();bundle.add_file(entry,request,provider.snapshot.sha256)
            response['processed']+=1;response['unprocessed']-=1
            if self._observer:self._observer('file',entry,dict(bundle.files[-1]))
        outcome=run_context(self.profile.worker,plan,request,self._record['owner'],uuid.uuid4().hex,trust,load,publish,check,timeout=min(self.context_seconds,max(.05,deadline-time.monotonic())),worker_sha256=self.worker_sha256 if self.product_mode else None)
        if self.product_mode:self._record['native_metrics']=outcome.metrics
        response['execution']={'state':outcome.state,'error':outcome.error,'pid':outcome.pid,'reaped':outcome.reaped,'consumed':outcome.consumed}
        if outcome.state!='EXITED_SUCCESSFULLY':
            raise PrototypeFailure('CANCELLED' if event.is_set() else 'EXECUTION_FAILED')
        if unavailable:
            response['status']='SOURCE_UNAVAILABLE'
            response['reason']='Selected catalog recordings include originals that could not be read. Readable originals were verified whole; unavailable sources have no signing finding.'
        else:response['status']='RESULT'


class ProductRollingWindowCoordinator(RollingWindowCoordinator):
    """Explicit assembly only: qualified CONT09 worker, unchanged consumer APIs."""
    max_files=182
    product_mode=True
    context_seconds=270
    resource_profile='candidate-rolling-product-v1'
    execution_profile='candidate-rolling-product-v1'

    def __init__(self,profile,*,worker_sha256=None,**kwargs):
        super().__init__(profile,lambda *args:None,**kwargs)
        self.worker_sha256=worker_sha256


def isolated_product_coordinator(profile,worker,sha256,**kwargs):
    """Bind one absolute executable path to one expected SHA-256.

    The default product coordinator leaves ``worker_sha256`` unset and continues
    to require ``WORKER_SHA256``. This function does not search a directory,
    read an environment variable, or select another executable.
    """
    path=Path(worker)
    if (type(sha256) is not str or len(sha256)!=64 or any(c not in '0123456789abcdef' for c in sha256)
            or not path.is_absolute()):
        raise ProtocolError('product-worker-identity')
    return ProductRollingWindowCoordinator(replace(profile,worker=path),worker_sha256=sha256,**kwargs)
