"""Explicit four-original experimental branch; never selected by existing launchers."""
from dataclasses import asdict, replace
from .windows import WindowCoordinator, windows
from .contracts import Rational
from .prototype import PrototypeFailure, DirectCapturedProfile
from ..frigate.historical_v2 import PROFILE_0182, PIN_0182
from .continuity_protocol import sha
from .media import ReaderLimits
from .continuity import Plan, require
from .continuity_worker import run_context


class RollingWindowCoordinator(WindowCoordinator):
    max_files=4
    product_mode=False
    context_seconds=45
    resource_profile='qualified-rolling-four-originals-experimental-v1'

    execution_profile='qualified-rolling-four-originals-experimental-v1'

    def __init__(self,profile,resolve_plan,**kwargs):
        if type(profile) is not DirectCapturedProfile or profile.provider_profile!=PROFILE_0182 or profile.snapshot.source_pin!=PIN_0182:
            raise TypeError('pinned direct captured profile required')
        if not callable(resolve_plan):raise TypeError('private trusted Plan resolver required')
        super().__init__(profile,**kwargs);self._resolve_plan=resolve_plan

    def _verify_many(self,provider,items,bounds,cancel,request,camera,request_id,permission,event,deadline,check,response):
        import time,uuid
        bundle=self._record['bundle'];ordered=tuple(items)
        permission();check()
        require(provider.snapshot==self.profile.snapshot,'qualified-snapshot')
        with open(self.profile.public_ca,'rb') as f:trust=f.read(32769)
        require(0<len(trust)<=32768,'context-trust')
        if self.product_mode:
            self._record['planned_count']=len(ordered)
            from .rolling_product import ProductPlan, WORKER_SHA256
            require(time.monotonic()<=deadline-self.core_seconds+30,'product-preparation-deadline')
            with open(self.profile.worker,'rb') as f:
                worker=f.read((8<<20)+1)
            require(len(worker)<=8<<20 and sha(worker)==WORKER_SHA256,'product-worker-identity')
            plan=ProductPlan(request,self._record['owner'],ordered,provider.snapshot,sha(trust),
                tuple(str(provider._items[i.item_id][1]) for i in ordered))
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
        seen=set()
        for wi,members in enumerate(windows(len(ordered))):
            response['windows'].append({'members':[ordered[i].item_id for i in members],'overlap':ordered[members[0]].item_id if wi else None})
            for index in members[1:] if wi else members:
                require(index not in seen,'duplicate-window-feed');seen.add(index)
        require(seen==set(range(len(ordered))),'window-plan')
        if self._observer:self._observer('plan',ordered,None)
        def load(index,guard):
            item=ordered[index];guard()
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
        outcome=run_context(self.profile.worker,plan,request,self._record['owner'],uuid.uuid4().hex,trust,load,publish,check,timeout=min(self.context_seconds,max(.05,deadline-time.monotonic())))
        if self.product_mode:self._record['native_metrics']=outcome.metrics
        response['execution']={'state':outcome.state,'error':outcome.error,'pid':outcome.pid,'reaped':outcome.reaped,'consumed':outcome.consumed}
        if outcome.state!='EXITED_SUCCESSFULLY':
            raise PrototypeFailure('CANCELLED' if event.is_set() else 'EXECUTION_FAILED')
        response['status']='RESULT'


class ProductRollingWindowCoordinator(RollingWindowCoordinator):
    """Explicit assembly only: qualified CONT09 worker, unchanged consumer APIs."""
    max_files=182
    product_mode=True
    context_seconds=270
    resource_profile='candidate-rolling-product-v1'
    execution_profile='candidate-rolling-product-v1'

    def __init__(self,profile,**kwargs):
        super().__init__(profile,lambda *args:None,**kwargs)
