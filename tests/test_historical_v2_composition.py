"""Synthetic complete B transport/composition tests. No native build or IO."""
import copy,json,unittest
from dataclasses import replace
from nanexus_trust.historical_v2 import contracts as a,wire
from nanexus_trust.historical_v2.worker_protocol import Attempt,CONFIG_DIGEST,ProtocolError,sha,encode,Decoder
from nanexus_trust.historical_v2.worker_updates import Admission,Reassembly,decode,token_hex
from nanexus_trust.historical_v2.composition import compose,validate,run_identity,canonical

class CompositionTests(unittest.TestCase):
 def setUp(self):
  self.req=a.Request('request',a.Camera('test','camera','binding','Camera'),a.Interval('1','2'),'host-metadata-unix-seconds')
  self.sources=[dict(token='same-payload-'+str(i),bytes=5,sha256='a'*64) for i in range(2)]
  self.spec=Attempt('attempt','request','placeholder','principal','camera','binding','source','generation','a'*64,10,2,'b'*64,100,profile='native-hv2-d1.5-v1',policy='native-single-source-v1')
  self.spec=replace(self.spec,run=run_identity(self.req,self.spec,self.sources))
  axes=dict(authenticity=4,provenance=3,combined=2,key_changed=False,raw_signing_start=-9,raw_signing_end=20)
  def item(n,final,state):return dict(received_ordinal=n,source_token_hex=token_hex(self.sources[n]['token']),nalu_type='P',raw_status='.' if final else 'P',revision=0,report_generation=1,finalized=final,state=state,axes=axes.copy())
  def u(phase,feed,gen,fresh,items,retired):return dict(run_id=self.spec.run,attempt=self.spec.attempt,phase=phase,trust=self.spec.trust_digest,configuration=CONFIG_DIGEST,stop=4 if phase=='finish' else 0,error=0,consumed=feed+1,generating_feed_ordinal=feed,report_generation=gen,fresh_report=fresh,retired_prefix_ordinal=retired,items=items,missing=[])
  self.updates=[u('feed',0,0,False,[],None),u('feed',1,1,True,[item(0,True,0),item(1,False,3)],0),u('finish',1,1,False,[item(1,False,3)],0)]
 def raws(self,u=None):return tuple(canonical(x) for x in (self.updates if u is None else u))
 def call(self,u=None,**kw):return compose(self.req,self.spec,self.sources,self.raws(u),**kw)
 def test_full_fields_and_conservative_result(self):
  r=self.call();self.assertEqual(self.call(),r);self.assertTrue(validate(r,self.req,self.spec,self.sources,self.raws()))
  self.assertEqual(r.page.lifecycle,a.Lifecycle.INCOMPLETE);self.assertFalse(r.page.output_complete);self.assertEqual(r.page.unresolved_refs,('same-payload-1',))
  o=r.page.observations[0];self.assertEqual(o.state,a.SigningState.VERIFIED_SIGNING);self.assertEqual(o.placement.status,a.PlacementStatus.UNPLACED);self.assertEqual(o.report.generating_feed_ordinal,1)
  self.assertEqual(wire.loads(wire.dumps(r.page)),r.page);self.assertEqual(decode(self.raws()[1])['items'][0]['axes']['raw_signing_start'],-9)
 def test_negative_preserved_and_key_change_denies_positive(self):
  u=copy.deepcopy(self.updates);i=u[1]['items'][0];i['state']=1;i['raw_status']='N';i['axes']['authenticity']=2
  self.assertEqual(self.call(u).page.observations[0].state,a.SigningState.INTEGRITY_NEGATIVE)
  for k,v in [('key_changed',True),('provenance',0),('combined',0)]:
   u=copy.deepcopy(self.updates);u[1]['items'][0]['axes'][k]=v
   with self.assertRaises(ProtocolError):self.call(u)
 def test_closed_typed_nested_schema(self):
  for path,value in [(('extra',),1),(('fresh_report',),1),(('items',0,'state'),True),(('items',0,'axes','key_changed'),0),(('items',0,'axes','raw_signing_start'),1.5),(('items',0,'axes','combined'),99)]:
   u=copy.deepcopy(self.updates);obj=u[1]
   for k in path[:-1]:obj=obj[k]
   obj[path[-1]]=value
   with self.assertRaises(ProtocolError):self.call(u)
  raw=self.raws()[1].replace(b'"fresh_report":true',b'"fresh_report":true,"fresh_report":true')
  with self.assertRaises(ProtocolError):decode(raw)
 def test_identity_and_revision_and_retirement_conflicts(self):
  for field,value in [('run_id','other'),('attempt','other'),('trust','c'*64),('configuration','c'*64),('report_generation',2),('consumed',1),('retired_prefix_ordinal',1)]:
   u=copy.deepcopy(self.updates);u[1][field]=value
   with self.assertRaises(ProtocolError):self.call(u)
  for field,value in [('revision',2),('source_token_hex','bad'),('received_ordinal',1),('finalized',False)]:
   u=copy.deepcopy(self.updates);u[1]['items'][0][field]=value
   with self.assertRaises(ProtocolError):self.call(u)
  u=copy.deepcopy(self.updates);u[-1]['items'][0]['raw_status']='.'
  with self.assertRaises(ProtocolError):self.call(u)
 def test_missing_anchors_preserved_without_inventing_scope(self):
  u=copy.deepcopy(self.updates);u[1]['missing']=[dict(left_received=0,right_received=1,report_generation=1)]
  r=self.call(u);self.assertEqual(len(r.page.observations),1);self.assertTrue(any(key.startswith('tail-') for key in r.page.unresolved_refs))
  for values in [(None,None),(1,0),(0,2)]:
   bad=copy.deepcopy(u);bad[1]['missing'][0].update(left_received=values[0],right_received=values[1])
   with self.assertRaises(ProtocolError):self.call(bad)
 def test_atomic_rejection_prior_evidence_and_interrupted_prefix(self):
  first=self.call();before=wire.dumps(first.page);bad=copy.deepcopy(self.updates);bad[1]['items'][0]['revision']=9
  ad=Admission(self.spec,[s['token'] for s in self.sources]);ad.add(self.raws()[0]);snapshot=ad.raws
  with self.assertRaises(ProtocolError):ad.add(canonical(bad[1]))
  self.assertEqual(ad.raws,snapshot);self.assertEqual(wire.dumps(first.page),before)
  for op in ('cancel','revoked','resource','failure','interrupted'):
   r=compose(self.req,self.spec,self.sources,self.raws()[:2],operational=op);self.assertEqual(len(r.page.observations),1);self.assertNotEqual(r.page.lifecycle,a.Lifecycle.COMPLETE)
  with self.assertRaises(ProtocolError):compose(self.req,self.spec,self.sources,self.raws()[:2])
  with self.assertRaises(ProtocolError):validate(replace(first,objects=first.objects[:-1]),self.req,self.spec,self.sources,self.raws())
 def test_error_consumed_count_and_provisional_terminal(self):
  u=copy.deepcopy(self.updates);u[-1].update(phase='error',stop=1,error=2)
  r=self.call(u);self.assertEqual(r.page.lifecycle,a.Lifecycle.STOPPED);self.assertEqual(r.page.reason,a.StopReason.RESOURCE_POLICY)
  u[-1]['consumed']=3
  with self.assertRaises(ProtocolError):self.call(u)
 def test_frame_reassembly_and_version_rejection(self):
  raw=self.raws()[1];digest=sha(raw)
  begin=dict(type='UPDATE_BEGIN',update=0,size=len(raw),digest=digest,items=2,missing=0)
  chunk=dict(type='UPDATE_CHUNK',update=0,offset=0);end=dict(type='UPDATE_END',update=0,digest=digest)
  r=Reassembly();r.accept(begin,b'');r.accept(chunk,raw);self.assertEqual(r.accept(end,b''),raw)
  for fault in ('oversize','interleave','offset','truncated','digest','duplicate','counts'):
   r=Reassembly()
   with self.subTest(fault=fault),self.assertRaises(ProtocolError):
    if fault=='oversize':r.accept(dict(begin,size=(1<<20)+1),b'')
    else:
     r.accept(dict(begin,items=1) if fault=='counts' else begin,b'')
     if fault=='interleave':r.accept(begin,b'')
     elif fault=='offset':r.accept(dict(chunk,offset=1),raw)
     elif fault=='truncated':r.accept(end,b'')
     else:
      r.accept(chunk,raw);r.accept(dict(end,digest='0'*64) if fault=='digest' else end,b'')
      if fault=='duplicate':r.accept(end,b'')
  b=encode('FINISH_INPUT','attempt',0).replace(b'hv2-d1.5/1',b'hv2-d1/1  ');d=Decoder();d.feed(b)
  with self.assertRaises(ProtocolError):d.pop()
 def test_parser_caps_and_token_canonicality(self):
  with self.assertRaises(ProtocolError):decode(b'x'*((1<<20)+1))
  for token in ('AA','0','zz','ff'):
   u=copy.deepcopy(self.updates);u[1]['items'][0]['source_token_hex']=token
   with self.assertRaises(ProtocolError):self.call(u)
  ad=Admission(self.spec,[s['token'] for s in self.sources]);ad.total=2<<20
  with self.assertRaises(ProtocolError):ad.add(self.raws()[0])
  with self.assertRaises(ProtocolError):compose(self.req,self.spec,self.sources*1024,self.raws())
 def test_raw_negative_cannot_be_downgraded(self):
  u=copy.deepcopy(self.updates);i=u[1]['items'][0];i.update(raw_status='N',state=3);i['axes']['authenticity']=2
  with self.assertRaises(ProtocolError):self.call(u)
 def test_valid_revision_then_retirement_and_emitted_cap(self):
  # First fresh report leaves both active. Second changes pending raw state;
  # only the changed item increments revision, and both retire atomically.
  u=copy.deepcopy(self.updates);first=u[0];first.update(fresh_report=True,report_generation=1,items=[copy.deepcopy(u[1]['items'][0])]);i=first['items'][0];i.update(raw_status='P',finalized=False,state=3)
  second=u[1];second['report_generation']=2;second['retired_prefix_ordinal']=1
  for item in second['items']:item['report_generation']=2;item.update(finalized=True,state=0,raw_status='.')
  second['items'][0]['revision']=1
  u[-1].update(report_generation=2,items=[],retired_prefix_ordinal=1)
  r=self.call(u);self.assertEqual(len(r.page.observations),2);self.assertEqual(r.page.observations[0].report.revision,1);self.assertFalse(r.page.unresolved_refs);self.assertEqual(r.page.lifecycle,a.Lifecycle.INCOMPLETE)
  ad=Admission(self.spec,[s['token'] for s in self.sources]);ad.emitted=8192
  ad.add(self.raws()[0])
  with self.assertRaises(ProtocolError):ad.add(self.raws()[1])
