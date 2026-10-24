"""Synthetic-only handoff/storage tests. Never launches native B or Reader."""
from dataclasses import replace
import base64,copy,errno,json,os,tempfile,threading,unittest
from pathlib import Path
from unittest.mock import patch
from nanexus_trust.historical_v2 import runtime_handoff as h, publication as p, composition as c, wire
from nanexus_trust.historical_v2.publication_store import PublicationStore
from helpers.historical_v2_publication_faults import fixture,small_fixture

ROOT=Path(__file__).resolve().parents[1]
ART=Path(os.environ.get('HV2_HANDOFF_TEST_ROOT',ROOT/'.local/governance/ai-dlc/evidence/m1-hv2-d-planning/d2.3-a-artifacts'))

class HandoffTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='handoff-',dir=ART);self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'store';self.auth=h.SyntheticHandoffAuthority()
        self.store=PublicationStore(self.path,self.auth,max_bytes=8<<20);self.addCleanup(self.store.close)
        self.publisher=h.PublisherHandoff(self.store,self.auth)
    def frozen(self,candidate=None,**kw):
        candidate=candidate or small_fixture()
        defaults=dict(session='session',lease='lease-'+candidate.attempt.attempt,revision=1,parent_incarnation='a'*32,key='first')
        defaults.update(kw);return h.freeze(candidate,**defaults)
    def commit(self,envelope):
        self.auth.enroll(envelope);r=self.publisher.publish(envelope);self.assertEqual(r.state,'COMMITTED',r)
        association=self.store._record(r.publication)['association'];lease=json.loads(envelope.metadata)['authority']['lease']
        self.auth.grant_retrieval(association,'owner',lease);return r
    def error(self,fn):
        with self.assertRaises((p.PublicationError,ValueError)):fn()
    def mutate(self,envelope,change,blobs=None):
        m=json.loads(envelope.metadata);change(m)
        m['authority']['intent']=h._intent(m);m['handoff_id']=h._digest_record(m)
        return h.Handoff(p.canonical(m),envelope.blobs if blobs is None else blobs)
    def test_fixture_canonical_complete_handoff(self):
        f=json.loads((ROOT/'tests/fixtures/historical-v2-runtime-handoff.json').read_text())
        self.assertEqual(f['label'],'synthetic-no-worker-execution')
        envelope=h.Handoff(p.canonical(f['metadata']),tuple(base64.b64decode(b,validate=True) for b in f['blobs_base64']))
        m,candidate=h.recognize(envelope);self.assertEqual(m['schema'],h.SCHEMA)
        self.assertEqual(m['terminal']['finalized'],2);self.assertEqual(len(set(s['token'] for s in candidate.sources)),4)
        self.assertEqual(len({s['sha256'] for s in candidate.sources}),1)
        r=self.commit(envelope);page=wire.loads(self.store.page(r.publication,'owner'))
        self.assertEqual(len(page.observations),2);self.assertEqual(len(page.unresolved_refs),2)
        self.assertFalse(page.output_complete);self.assertTrue(all(o.placement.status.value=='UNPLACED' for o in page.observations))
    def test_finalized_tail_continuation_preserved(self):
        envelope=self.frozen(fixture());r=self.commit(envelope)
        page=wire.loads(self.store.page(r.publication,'owner'));other=wire.loads(self.store.page(r.publication,'owner',next_cursor=page.next_cursor))
        self.assertEqual((len(page.observations),len(other.observations)),(128,60));self.assertEqual(len(page.unresolved_refs),16)
        m,candidate=h.recognize(envelope);self.assertEqual(m['terminal']['finalized'],188)
        self.assertEqual(page.unresolved_refs,candidate.result.page.unresolved_refs)
    def test_closed_fields_versions_producer_and_exact_types(self):
        envelope=self.frozen()
        mutations=[lambda m:m.update(extra=1),lambda m:m.update(schema='unknown'),lambda m:m.update(policy='native-policy'),lambda m:m.update(producer='native-b'),lambda m:m['authority'].update(revision=True),lambda m:m['authority'].update(extra=1),lambda m:m['terminal'].update(finalized=True),lambda m:m['worker'].update(executable='f'*64),lambda m:m['worker'].update(receipt='native-executed'),lambda m:m['publication'].update(extra=1)]
        for mutation in mutations:
            with self.subTest(mutation=mutations.index(mutation)):self.error(lambda:h.recognize(self.mutate(envelope,mutation)))
        raw=envelope.metadata.replace(b'"schema":',b'"schema":"duplicate","schema":',1)
        self.error(lambda:h.recognize(h.Handoff(raw,envelope.blobs)))
        m=json.loads(envelope.metadata);m.pop('source');self.error(lambda:h.recognize(h.Handoff(p.canonical(m),envelope.blobs)))
    def test_identity_and_source_bindings(self):
        envelope=self.frozen()
        mutations=[lambda m:m['source'].update(generation='different'),lambda m:m['source'].update(cut='f'*64),lambda m:m['source'].update(processing='f'*64),lambda m:m['source'].update(ledger='f'*64),lambda m:m['source'].update(provider='other'),lambda m:m['attempt'].update(run='other'),lambda m:m['attempt'].update(binding='other'),lambda m:m['attempt'].update(camera='other'),lambda m:m['worker'].update(trust='f'*64),lambda m:m['worker'].update(configuration='f'*64),lambda m:m['authority'].update(principal='other')]
        for i,mutation in enumerate(mutations):
            with self.subTest(i=i):self.error(lambda:h.recognize(self.mutate(envelope,mutation)))
    def test_mutability_bounds_and_digest_rejection(self):
        envelope=self.frozen();self.error(lambda:h.Handoff(bytearray(envelope.metadata),envelope.blobs))
        self.error(lambda:h.Handoff(envelope.metadata,list(envelope.blobs)))
        self.error(lambda:h.Handoff(envelope.metadata,(bytearray(b'x'),)))
        self.error(lambda:h.Handoff(b'x'*65537,()))
        self.error(lambda:h.Handoff(b'{}',(b'x'*(2<<20),)*7))
        bad=list(envelope.blobs);bad[0]=bad[0][:-1];self.error(lambda:h.recognize(h.Handoff(envelope.metadata,tuple(bad))))
        candidate=small_fixture();self.error(lambda:self.frozen(replace(candidate,updates=(bytearray(b'{}'),))))
        self.error(lambda:self.frozen(replace(candidate,worker_binary='f'*64)))
    def test_owned_source_snapshot(self):
        candidate=copy.deepcopy(small_fixture());envelope=self.frozen(candidate)
        candidate.sources[0]['token']='changed';m,recognized=h.recognize(envelope)
        self.assertEqual(recognized.sources[0]['token'],'occurrence-0')
    def test_update_missing_extra_truncated_and_conflict(self):
        envelope=self.frozen()
        for change in (lambda m:m['updates'].pop(),lambda m:m['updates'].append(m['updates'][0]),lambda m:m['updates'].reverse()):
            self.error(lambda:h.recognize(self.mutate(envelope,change)))
        m=json.loads(envelope.metadata);index=m['updates'][0]['index'];blobs=list(envelope.blobs)
        blobs[index]=b'{';self.error(lambda:h.recognize(self.mutate(envelope,lambda m:m['updates'][0].update(p.descriptor(b'{')),tuple(blobs))))
        self.error(lambda:h.recognize(self.mutate(envelope,lambda m:m['terminal'].update(execution='FAILED',error='protocol-or-startup'))))
    def test_missing_tail_and_continuation(self):
        envelope=self.frozen(fixture())
        self.error(lambda:h.recognize(self.mutate(envelope,lambda m:m['composition']['objects'].pop())))
        self.error(lambda:h.recognize(self.mutate(envelope,lambda m:m['terminal'].update(tail='f'*64))))
    def test_session_expiry_revocation_and_stale_revision(self):
        envelope=self.frozen();self.auth.enroll(envelope)
        with patch('nanexus_trust.historical_v2.runtime_handoff.time.monotonic',return_value=float('inf')):
            self.assertEqual(self.publisher.publish(envelope).error,'ACCESS_DENIED')
        self.auth.revoke('lease-small-1');self.assertEqual(self.publisher.publish(envelope).error,'ACCESS_DENIED')
        self.error(lambda:self.auth.enroll(envelope));self.assertIsNone(self.auth._active)
    def test_cancellation_requires_explicit_checkpoint(self):
        candidate=small_fixture();raws=candidate.updates[:-1]
        candidate=replace(candidate,updates=raws,operational='cancel',result=c.compose(candidate.request,candidate.attempt,candidate.sources,raws,operational='cancel'))
        denied=self.frozen(candidate,execution='CANCELLED',error='cancel');self.auth.enroll(denied)
        self.assertEqual(self.publisher.publish(denied).error,'ACCESS_DENIED')
        allowed=self.frozen(candidate,execution='CANCELLED',error='cancel',checkpoint=True,lease='checkpoint')
        r=self.commit(allowed);self.assertEqual(wire.loads(self.store.page(r.publication,'owner')).lifecycle.value,'CANCELLED')
        self.assertEqual(len(h.recognize(allowed)[1].updates),4)
    def test_resource_and_interrupted_prefixes(self):
        for op,state,error in [('resource','RESOURCE_STOPPED','resource'),('interrupted','INTERRUPTED','child-exit')]:
            candidate=fixture(op,request_id=op,size=4,finalized=2);raws=candidate.updates[:-1]
            candidate=replace(candidate,updates=raws,operational=op,result=c.compose(candidate.request,candidate.attempt,candidate.sources,raws,operational=op))
            env=self.frozen(candidate,key=op,execution=state,error=error,checkpoint=True)
            self.auth.enroll(env);r=self.publisher.publish(env)
            self.assertEqual(r.state,'COMMITTED',r)
            with self.store._publication(r.publication) as fd:
                manifest=self.store._control(fd,r.publication)['head'];self.assertIsNotNone(manifest)
    def test_idempotency_conflict_and_authority_context_cleanup(self):
        env=self.frozen();first=self.commit(env)
        self.assertEqual(self.publisher.publish(env).head,first.head)
        other=self.frozen(small_fixture('different'));self.auth.enroll(other)
        self.assertEqual(self.publisher.publish(other).error,'CONFLICT');self.assertIsNone(self.auth._active)
        self.assertFalse(self.auth.authorize_publish(self.store._record(first.publication)['association']))
    def test_prehead_failure_preserves_and_unknown_resolves(self):
        first=self.commit(self.frozen());old=self.store.page(first.publication,'owner');head=json.loads(first.head)
        env=self.frozen(small_fixture('second'),key='second',publication=first.publication,expected_head=head['sha256']);self.auth.enroll(env)
        def fault(point):
            if point=='validated':raise OSError(errno.EIO,'synthetic')
        self.store._fault=fault;self.assertEqual(self.publisher.publish(env).error,'IO_FAILURE')
        self.assertEqual(self.store.page(first.publication,'owner'),old);self.assertIsNone(self.auth._active)
        # Separate new store for the post-head fault; no key rotation/recovery loop.
    def test_unknown_outcome_same_input_resolution(self):
        env=self.frozen();self.auth.enroll(env)
        def fault(point):
            if point=='head-transition':raise OSError(errno.EIO,'synthetic')
        self.store._fault=fault;unknown=self.publisher.publish(env)
        self.assertEqual(unknown.state,'COMMIT_OUTCOME_UNKNOWN');self.assertIsNone(self.auth._active)
        self.store._fault=lambda _:None;resolved=self.publisher.publish(env)
        self.assertEqual(resolved.state,'COMMITTED');self.assertEqual(unknown.publication,resolved.publication)
        with self.store._publication(resolved.publication) as fd:self.assertEqual(len(self.store._control(fd,resolved.publication)['attempts']),1)
    def transition_denial(self,operation):
        env=self.frozen(small_fixture(operation),key=operation);self.auth.enroll(env)
        def fault(point):
            if point=='validated':getattr(self.auth,operation)('lease-'+operation)
        self.store._fault=fault;r=self.publisher.publish(env)
        self.assertEqual(r.error,'ACCESS_DENIED');self.assertIsNone(self.auth._active)
        with self.store._publication(r.publication) as fd:self.assertIsNone(self.store._control(fd,r.publication)['head'])
    def test_publication_cancel_before_commit(self):self.transition_denial('cancel_publication')
    def test_revocation_before_commit(self):self.transition_denial('revoke')
    def test_revocation_loses_to_guarded_head_transition(self):
        env=self.frozen();self.auth.enroll(env);requested=threading.Event();done=threading.Event();threads=[]
        def revoke():requested.set();self.auth.revoke('lease-small-1');done.set()
        def fault(point):
            if point=='before-head':
                t=threading.Thread(target=revoke);threads.append(t);t.start()
                self.assertTrue(requested.wait(1));self.assertFalse(done.is_set())
        self.store._fault=fault;r=self.publisher.publish(env)
        for t in threads:t.join(2)
        self.assertTrue(done.is_set());self.assertEqual(r.state,'COMMITTED')
        self.assertEqual(self.publisher.publish(env).error,'ACCESS_DENIED')
        with self.store._publication(r.publication) as fd:self.assertEqual(self.store._control(fd,r.publication)['head'],json.loads(r.head))
    def test_independent_retrieval_and_one_nonqueued_slot(self):
        first=self.commit(self.frozen());ready=threading.Event();release=threading.Event();results=[]
        env=self.frozen(small_fixture('second'),key='second',publication=first.publication,expected_head=json.loads(first.head)['sha256']);self.auth.enroll(env)
        def fault(point):
            if point=='validated':ready.set();release.wait(2)
        self.store._fault=fault;thread=threading.Thread(target=lambda:results.append(self.publisher.publish(env)));thread.start()
        try:
            self.assertTrue(ready.wait(2));self.assertTrue(self.store.page(first.publication,'owner'))
            self.error(lambda:h.PublisherHandoff(self.store,self.auth).publish(env))
        finally:release.set();thread.join(3)
        self.assertFalse(thread.is_alive());self.assertEqual(results[0].state,'COMMITTED')
    def test_quota_orphan_and_cleanup_pending_refusal(self):
        first=self.commit(self.frozen());head=json.loads(first.head);old=self.store.page(first.publication,'owner')
        env=self.frozen(small_fixture('second'),key='second',publication=first.publication,expected_head=head['sha256']);self.auth.enroll(env)
        self.store.max_bytes=65536;self.assertEqual(self.publisher.publish(env).error,'QUOTA');self.store.max_bytes=8<<20
        stage=self.path/'publications'/first.publication/'staging'/'foreign';stage.mkdir(mode=0o700)
        self.assertEqual(self.publisher.publish(env).error,'RECOVERY_PENDING');self.assertTrue(stage.exists());self.assertEqual(self.store.page(first.publication,'owner'),old)
    def test_expiry_and_pending_cleanup(self):
        first=self.commit(self.frozen());head=json.loads(first.head)
        second=self.frozen(small_fixture('second'),key='second',publication=first.publication,expected_head=head['sha256']);r=self.commit(second)
        self.auth.grant_maintenance(self.store.root_id,first.publication)
        with self.store._publication(first.publication) as fd:revision=self.store._control(fd,first.publication)['revision']
        self.store.upgrade(first.publication,revision);self.auth.grant_maintenance(self.store.root_id,first.publication,head['generation'])
        with self.store._publication(first.publication) as fd:revision=self.store._control(fd,first.publication)['revision']
        self.store.retire(first.publication,head['generation'],revision)
        self.assertEqual(self.publisher.publish(self.frozen()).error,'EXPIRED')
        third=self.frozen(small_fixture('third'),key='third',publication=first.publication,expected_head=json.loads(r.head)['sha256']);self.auth.enroll(third)
        self.assertEqual(self.publisher.publish(third).error,'CLEANUP_PENDING')
        self.assertTrue(self.store.page(first.publication,'owner'));self.assertIsNotNone(self.store._pending())

    def test_negative_evidence_preserved(self):
        candidate=small_fixture();raws=list(candidate.updates);u=json.loads(raws[-2]);item=u['items'][0]
        item.update(raw_status='N',state=1);item['axes']['authenticity']=2;raws[-2]=c.canonical(u);raws=tuple(raws)
        candidate=replace(candidate,updates=raws,result=c.compose(candidate.request,candidate.attempt,candidate.sources,raws))
        r=self.commit(self.frozen(candidate));page=wire.loads(self.store.page(r.publication,'owner'))
        self.assertEqual(page.observations[0].state.value,'INTEGRITY_NEGATIVE')

    def test_tampered_occurrence_report_and_revision_fail_closed(self):
        envelope=self.frozen();m=json.loads(envelope.metadata)
        def changed_blob(ref,raw):
            blobs=list(envelope.blobs);blobs[ref['index']]=raw
            def change(meta):
                # Locate the same descriptor in its exact typed position.
                if ref['index']==meta['source']['occurrences']['index']:
                    meta['source']['occurrences'].update(p.descriptor(raw));meta['source']['ledger']=p.digest(raw)
                else:
                    for d in meta['updates']:
                        if d['index']==ref['index']:d.update(p.descriptor(raw))
            return self.mutate(envelope,change,tuple(blobs))
        ref=m['source']['occurrences'];sources=json.loads(envelope.blobs[ref['index']]);sources[1]['token']=sources[0]['token']
        self.error(lambda:h.recognize(changed_blob(ref,p.canonical(sources))))
        ref=m['updates'][-2]
        for field,value in [('revision',7),('report_generation',7),('raw_status','N')]:
            u=json.loads(envelope.blobs[ref['index']]);u['items'][0][field]=value
            self.error(lambda:h.recognize(changed_blob(ref,p.canonical(u))))
    def test_full_intent_cannot_be_substituted_under_lease(self):
        envelope=self.frozen();self.auth.enroll(envelope)
        for mutation in (lambda m:m['authority'].update(session='different'),lambda m:m['authority'].update(revision=2),lambda m:m['publication'].update(key='different'),lambda m:m['publication'].update(expected_head='f'*64),lambda m:m.update(parent_incarnation='b'*32)):
            changed=self.mutate(envelope,mutation);r=self.publisher.publish(changed)
            self.assertEqual(r.error,'ACCESS_DENIED');self.assertIsNone(self.auth._active)
    def test_retrieval_denies_after_session_revocation(self):
        envelope=self.frozen();r=self.commit(envelope);self.auth.revoke('lease-small-1')
        self.error(lambda:self.store.page(r.publication,'owner'))
    def test_early_source_update_object_and_metadata_limits(self):
        candidate=small_fixture()
        self.error(lambda:self.frozen(candidate,session='x'*257))
        self.error(lambda:self.frozen(replace(candidate,sources=candidate.sources*257)))
        self.error(lambda:self.frozen(replace(candidate,updates=candidate.updates*206)))
        self.error(lambda:self.frozen(replace(candidate,result=replace(candidate.result,objects=candidate.result.objects*17))))
        self.error(lambda:h.Handoff(b'{}',(b'x',)*1094))
        bad=self.mutate(self.frozen(),lambda m:m['terminal'].update(reaped=False));self.error(lambda:h.recognize(bad))

    def test_session_expiry_during_control_write_prevents_head(self):
        # A session deadline advances independently of the revocation lock.
        # Require current lease validity at Head replacement, not just entry to
        # the guard before control temp creation/write/fsync.
        clock=[1000.0]
        with patch('nanexus_trust.historical_v2.runtime_handoff.time.monotonic',side_effect=lambda:clock[0]):
            first=self.commit(self.frozen());old=json.loads(first.head)
            env=self.frozen(small_fixture('deadline'),key='deadline',publication=first.publication,expected_head=old['sha256'])
            self.auth.enroll(env,seconds=1)
            def fault(point):
                if point=='before-head':clock[0]=1002.0
            self.store._fault=fault;result=self.publisher.publish(env)
            with self.store._publication(first.publication) as fd:actual=self.store._control(fd,first.publication)['head']
            self.assertEqual((result.state,actual),('REFUSED',old),
                             'lease expired before Head replacement but a new Head was committed')

    def boundary_refusal(self,action):
        clock=[1000.0]
        with patch('nanexus_trust.historical_v2.runtime_handoff.time.monotonic',side_effect=lambda:clock[0]):
            first=self.commit(self.frozen());old=json.loads(first.head);old_bytes=self.store.page(first.publication,'owner')
            candidate=small_fixture('boundary')
            env=self.frozen(candidate,key='boundary',publication=first.publication,expected_head=old['sha256'])
            self.auth.enroll(env,seconds=1)
            before=self.store._usage()[0]
            def fault(point):
                if point=='before-head':action(clock,env)
            self.store._fault=fault;r=self.publisher.publish(env)
            self.assertEqual((r.state,r.error),('REFUSED','ACCESS_DENIED'));self.assertIsNone(self.auth._active)
            with self.store._publication(first.publication) as fd:
                control=self.store._control(fd,first.publication)
            self.assertEqual(control['head'],old)
            self.assertEqual([x['status'] for x in control['attempts']],['COMMITTED','RESERVED'])
            base=self.path/'publications'/first.publication
            self.assertTrue((base/'control.json.next').exists())
            self.assertEqual(len(list((base/'generations').iterdir())),2)
            self.assertGreater(self.store._usage()[0],before)
            self.assertEqual(self.store.page(first.publication,'owner'),old_bytes)
    def test_expiry_final_refusal_preserves_association_and_charged_orphan(self):
        self.boundary_refusal(lambda clock,env:clock.__setitem__(0,1002.0))
    def test_revocation_inside_guard_before_final_check_refuses(self):
        self.boundary_refusal(lambda clock,env:self.auth.revoke('lease-boundary'))
    def test_lease_revision_changes_before_final_check_refuse(self):
        def renew(clock,env):
            changed=self.mutate(env,lambda m:m['authority'].update(revision=2))
            self.auth.enroll(changed)
        self.boundary_refusal(renew)
    def test_expiry_after_final_authorization_does_not_undo_commit(self):
        clock=[1000.0]
        with patch('nanexus_trust.historical_v2.runtime_handoff.time.monotonic',side_effect=lambda:clock[0]):
            env=self.frozen();self.auth.enroll(env,seconds=1);check=self.auth.final_publish_check;decisions=[]
            def final(association):
                allowed=check(association);decisions.append(allowed)
                # Authority test seam only: move time after the decision. The
                # filesystem replacement itself is never monkeypatched.
                clock[0]=1002.0;return allowed
            with patch.object(self.auth,'final_publish_check',side_effect=final):r=self.publisher.publish(env)
            self.assertEqual(decisions,[True]);self.assertEqual(r.state,'COMMITTED')
            with self.store._publication(r.publication) as fd:
                control=self.store._control(fd,r.publication)
            self.assertEqual(control['head'],json.loads(r.head));self.assertEqual(control['attempts'][0]['status'],'COMMITTED')
            self.assertEqual(self.publisher.publish(env).error,'ACCESS_DENIED')
    def test_valid_final_check_commits_once_with_exact_active_intent(self):
        env=self.frozen();self.auth.enroll(env);check=self.auth.final_publish_check;calls=[]
        def final(association):
            self.assertEqual(self.auth._active[0]['handoff_id'],json.loads(env.metadata)['handoff_id'])
            calls.append(dict(association));return check(association)
        with patch.object(self.auth,'final_publish_check',side_effect=final):r=self.publisher.publish(env)
        self.assertEqual(r.state,'COMMITTED');self.assertEqual(len(calls),1);self.assertIsNone(self.auth._active)
