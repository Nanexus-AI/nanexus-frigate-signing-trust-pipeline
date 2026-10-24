"""Synthetic models and controlled, owned crash helpers. No native B/media."""
from contextlib import contextmanager
from dataclasses import replace
from functools import lru_cache
import os,signal,sys,threading
from nanexus_trust.historical_v2 import contracts as a,composition as c
from nanexus_trust.historical_v2.worker_protocol import Attempt,sha
from nanexus_trust.historical_v2.worker_updates import token_hex
from nanexus_trust.historical_v2.publication import Candidate,PublicationError

class SyntheticAuthority:
    """A named trusted test adapter with linearized revocation and handoff."""
    def __init__(self):
        self.lock=threading.RLock();self.active=True;self.reads=True;self.epoch=0;self.maintenance_scopes=set()
    def revoke(self):
        with self.lock:self.active=False;self.epoch+=1
    def authorize_publish(self,b):
        with self.lock:return self.active and b['owner']=='owner' and b['camera']=='camera' and b['camera_binding']=='binding'
    @contextmanager
    def publish_guard(self,b):
        with self.lock:
            if not self.authorize_publish(b):raise PublicationError('ACCESS_DENIED')
            yield self.epoch
    def authorize_retrieve(self,b,caller):
        with self.lock:return self.reads and caller=='owner' and self.authorize_publish(b)
    @contextmanager
    def retrieval_guard(self,b,caller):
        with self.lock:
            if not self.authorize_retrieve(b,caller):raise PublicationError('ACCESS_DENIED')
            yield self.epoch

    def grant_maintenance(self,root,publication,generation=None):
        with self.lock:self.maintenance_scopes.add((root,publication,generation))
    def revoke_maintenance(self):
        with self.lock:self.maintenance_scopes.clear()
    def authorize_maintenance(self,context):
        with self.lock:
            return ((context['root'],context['publication'],context['target']) in self.maintenance_scopes
                    and context['owner']=='owner' and context['camera']=='camera' and context['camera_binding']=='binding')
    @contextmanager
    def maintenance_guard(self,context):
        with self.lock:
            if not self.authorize_maintenance(context):raise PublicationError('ACCESS_DENIED')
            yield self.epoch

@lru_cache(maxsize=12)
def fixture(attempt='attempt-1',request_id='request',operational=None,*,size=204,finalized=188):
    request=a.Request(request_id,a.Camera('synthetic','camera','binding','Camera'),a.Interval('1','2'),'host-metadata-unix-seconds')
    sources=tuple(dict(token=f'occurrence-{i}',bytes=5,sha256='a'*64) for i in range(size))
    spec=Attempt(attempt,request_id,'placeholder','owner','camera','binding','source',sha(c.canonical(sources)),'a'*64,size*5,size,'b'*64,100,profile='native-hv2-d1.5-v1',cut='c'*64,policy='native-single-source-v1')
    spec=replace(spec,run=c.run_identity(request,spec,sources))
    def update(n):return dict(run_id=spec.run,attempt=spec.attempt,phase='feed',trust=spec.trust_digest,configuration=spec.configuration,stop=0,error=0,consumed=n+1,generating_feed_ordinal=n,report_generation=0,fresh_report=False,retired_prefix_ordinal=None,items=[],missing=[])
    updates=[update(i) for i in range(size)]
    items=[]
    for n in range(size):
        final=n<finalized
        items.append(dict(received_ordinal=n,source_token_hex=token_hex(sources[n]['token']),nalu_type='P',raw_status='.' if final else 'P',revision=0,report_generation=1,finalized=final,state=0 if final else 3,axes=dict(authenticity=4,provenance=3,combined=2,key_changed=False,raw_signing_start=None,raw_signing_end=None)))
    updates[-1].update(report_generation=1,fresh_report=True,retired_prefix_ordinal=finalized-1,items=items)
    tail=update(size-1);tail.update(phase='finish',stop=4,report_generation=1,retired_prefix_ordinal=finalized-1,items=items[finalized:]);updates.append(tail)
    raws=tuple(c.canonical(u) for u in updates)
    result=c.compose(request,spec,sources,raws,operational=operational)
    return Candidate(request,spec,sources,raws,result,operational)

def crash(root,publication,expected,phase):
    from nanexus_trust.historical_v2.publication_store import PublicationStore
    def fault(point):
        if point==phase:os.kill(os.getpid(),signal.SIGKILL)
    with PublicationStore(root,SyntheticAuthority(),max_bytes=8<<20,_fault=fault) as store:
        store.publish(publication,'candidate-2',fixture('attempt-2'),expected_head=expected)



def small_fixture(attempt='small-1'):
    return fixture(attempt,size=4,finalized=2)

def gc_crash(root,publication,target,phase):
    from nanexus_trust.historical_v2.publication_store import PublicationStore
    authority=SyntheticAuthority()
    def fault(point):
        if point==phase:os.kill(os.getpid(),signal.SIGKILL)
    with PublicationStore(root,authority,max_bytes=8<<20,_fault=fault) as store:
        authority.grant_maintenance(store.root_id,publication,target)
        with store._publication(publication) as fd:revision=store._control(fd,publication)['revision']
        store.retire(publication,target,revision)
        for _ in range(9):
            if store.cleanup_step(publication)['status']=='DONE':return
        raise RuntimeError('bounded cleanup did not finish')

if __name__=='__main__':
    if sys.argv[1]=='--gc':gc_crash(*sys.argv[2:])
    else:crash(*sys.argv[1:])
