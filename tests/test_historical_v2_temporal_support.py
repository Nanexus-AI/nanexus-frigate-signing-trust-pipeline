"""Synthetic contracts only: no media, crypto bridge, storage or rendering."""
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import unittest

from nanexus_trust.historical_v2 import contracts as a, wire
from nanexus_trust.historical_v2 import temporal_support as t

FIXTURE_PATH = Path(__file__).parent / 'fixtures/historical-v2-temporal-support.json'
FIXTURE = json.loads(FIXTURE_PATH.read_bytes())


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def rational(n, d=1):
    return {'numerator': n, 'denominator': d}


def descriptor(identity, data, profile):
    return {'evidence_id': identity, 'sha256': hashlib.sha256(data).hexdigest(),
            'size': len(data), 'profile': profile}


def observation_bytes(record):
    return encode({'schema': wire.SCHEMA, 'version': 1, 'type': 'Observation', 'record': record})


def request_bytes(record):
    return encode({'schema': wire.SCHEMA, 'version': 1, 'type': 'Request', 'record': record})


def assemble(*, page=None, bounds=None, proof=None, rebind=False, edit_manifest=None, proof_profile=t.SYNTHETIC_PROOF_PROFILE):
    """Rehash fixture artifacts independently; invalid semantics are not repaired."""
    page = deepcopy(FIXTURE['base_page'] if page is None else page)
    bounds = deepcopy(FIXTURE['bounds'] if bounds is None else bounds)
    proof = deepcopy(FIXTURE['proof'] if proof is None else proof)
    p, b = page['record'], bounds['record']
    if rebind:
        obs = p['observations'][0]
        b['parent'] = dict(job_id=p['job_id'], request_sha256=t.sha256(request_bytes(p['request'])),
                           run_id=obs['run_id'], observation_id=obs['observation_id'],
                           observation_sha256=t.sha256(observation_bytes(obs)))
        proof['observation_sha256'] = b['parent']['observation_sha256']
    proof_data = encode(proof)
    pref = descriptor('proof-1', proof_data, proof_profile)
    b['evidence'] = [pref if r['evidence_id'] == 'proof-1' else r for r in b['evidence']]
    bounds_data = encode(bounds)
    bref = descriptor(b['attachment_id'], bounds_data, t.BOUNDS_PROFILE)
    p['evidence'] = [pref if r['evidence_id'] == 'proof-1' else
                     bref if r['evidence_id'] == b['attachment_id'] else r for r in p['evidence']]
    base_data = encode(page)
    manifest = deepcopy(FIXTURE['publication'])
    manifest['record']['base_page'] = descriptor('base-page', base_data, t.BASE_PROFILE)
    manifest['record']['attachments'] = [bref]
    if rebind:
        manifest['record']['job_id'] = p['job_id']
        manifest['record']['request_sha256'] = b['parent']['request_sha256']
    if edit_manifest is not None: edit_manifest(manifest)
    return encode(manifest), {'base-page': base_data, b['attachment_id']: bounds_data, 'proof-1': proof_data}


def resolve(blobs):
    def read(ref, max_bytes):
        data = blobs[ref.evidence_id]
        if len(data) > max_bytes: raise OSError('bounded resolver overflow')
        return data
    return read


class TemporalSupportTests(unittest.TestCase):
    def recognize(self, **changes):
        root, blobs = assemble(**changes)
        return t.recognize(root, resolve(blobs))

    def test_fixture_canonical_roundtrip_and_A_unchanged(self):
        for name, cls in (('bounds', t.TemporalSupportBounds), ('publication', t.TemporalPublication)):
            encoded = encode(FIXTURE[name])
            value = t.loads(encoded)
            self.assertIs(type(value), cls)
            self.assertEqual(t.dumps(value), encoded)
            self.assertEqual(t.loads(t.dumps(value)), value)
            with self.assertRaises(ValueError): wire.loads(encoded)
        base = encode(FIXTURE['base_page'])
        page = wire.loads(base)
        self.assertEqual(wire.dumps(page), base)
        self.assertEqual(page.observations[0].placement.status, a.PlacementStatus.AMBIGUOUS)
        self.assertIsNone(page.observations[0].support.interval)
        self.assertIsNone(page.observations[0].support.point)
        self.assertEqual(hashlib.sha256((FIXTURE_PATH.parent/'historical-v2-contracts.json').read_bytes()).hexdigest(),
                         FIXTURE['A_v1_fixture_sha256'])
        root, blobs = assemble()
        self.assertEqual(root, encode(FIXTURE['publication']))
        self.assertEqual(blobs['base-page'], base)
        recognized = t.recognize(root, resolve(blobs))
        self.assertEqual(recognized.resolver_calls, 3)
        with self.assertRaises(t.TemporalError): t.recognize(blobs['bounds-1'], resolve(blobs))

    def test_exact_bounds_core_envelope_and_no_inferred_probabilities(self):
        value = self.recognize().attachments[0]
        self.assertEqual(value.definite_core, (Fraction(1), Fraction(9)))
        self.assertEqual(value.possible_envelope, (Fraction(0), Fraction(10)))
        self.assertEqual(value.uncertainty_bands, ((Fraction(0),Fraction(1)),(Fraction(9),Fraction(10))))
        exact = replace(value, start_min=a.Rational(-9007199254740997, 90000),
                        start_max=a.Rational(-9007199254740997,90000),end_min=a.Rational(1,3),end_max=a.Rational(1,3))
        self.assertEqual(t.loads(t.dumps(exact)).possible_envelope, exact.possible_envelope)
        self.assertEqual(exact.uncertainty_bands, ())
        with self.assertRaises(FrozenInstanceError): value.policy = 'changed'

    def test_empty_core_valid_and_impossible_bounds_rejected(self):
        b, proof = deepcopy(FIXTURE['bounds']), deepcopy(FIXTURE['proof'])
        b['record'].update(start_min=rational(5),start_max=rational(7),end_min=rational(6),end_max=rational(8))
        proof['cases'] = [dict(start=rational(5),end=rational(6),alternative_ref=None),
                          dict(start=rational(7),end=rational(8),alternative_ref=None)]
        value = self.recognize(bounds=b,proof=proof).attachments[0]
        self.assertIsNone(value.definite_core)
        self.assertEqual(value.uncertainty_bands, ((Fraction(5),Fraction(8)),))
        equal = replace(value,start_max=a.Rational(6,1))
        self.assertIsNone(equal.definite_core)
        for changes in ({'start_min':a.Rational(8,1)}, {'end_min':a.Rational(9,1)},
                        {'start_min':a.Rational(8,1),'start_max':a.Rational(8,1)}):
            with self.subTest(changes=changes), self.assertRaises(t.TemporalError): replace(value,**changes)

    def test_closed_schema_versions_scalar_and_rational_rules(self):
        for name in ('bounds','publication'):
            base = FIXTURE[name]
            mutations = []
            for key,value in (('version',True),('version',2),('schema',wire.SCHEMA),('type','Unexpected')):
                obj=deepcopy(base);obj[key]=value;mutations.append(obj)
            obj=deepcopy(base);obj['extra']=None;mutations.append(obj)
            obj=deepcopy(base);obj['record']['extra']=None;mutations.append(obj)
            obj=deepcopy(base);del obj['record'][next(iter(obj['record']))];mutations.append(obj)
            for obj in mutations:
                with self.subTest(obj=obj), self.assertRaises(t.TemporalError): t.loads(encode(obj))
        for value in (rational(0,2),rational(2,2),rational(1,0),rational(1,-1),rational(True),rational(1.0)):
            obj=deepcopy(FIXTURE['bounds']);obj['record']['start_min']=value
            with self.subTest(value=value), self.assertRaises(t.TemporalError): t.loads(encode(obj))
        for data in (b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":1e2}', b'{"x":999999999999999999999999}',
                     b'[[]]'*200, b'x'*(t.MAX_DOCUMENT_BYTES+1)):
            with self.assertRaises(t.TemporalError): t.loads(data)
        deep = b'['*14+b'0'+b']'*14
        with self.assertRaises(t.TemporalError): t.loads(deep)
        obj=deepcopy(FIXTURE['bounds']);obj['record']['parent']['extra']=1
        with self.assertRaises(t.TemporalError): t.loads(encode(obj))

    def test_parent_content_digest_and_report_revision_binding(self):
        for mutate in (lambda o:o['report'].update(revision=1), lambda o:o.update(run_id='other'),
                       lambda o:o['report'].update(generation='other'),lambda o:o.update(trust_profile='changed'),
                       lambda o:o['support'].update(sample_ref='changed')):
            page=deepcopy(FIXTURE['base_page']);mutate(page['record']['observations'][0])
            with self.assertRaisesRegex(t.TemporalError,'binding mismatch|orphan attachment'): self.recognize(page=page)
        page=deepcopy(FIXTURE['base_page']);page['record']['observations'][0]['report']['revision']=1
        self.assertEqual(self.recognize(page=page,rebind=True).base_page.observations[0].report.revision,1)
        # Report-null stays null; temporal content version remains the digest.
        page['record']['observations'][0].update(state='INDETERMINATE',report=None,trust_profile=None)
        self.assertIsNone(self.recognize(page=page,rebind=True).base_page.observations[0].report)

    def test_request_job_and_camera_binding(self):
        for modify in (lambda p:p['request']['camera'].update(binding_generation='changed'),
                       lambda p:p['request'].update(interval={'start':'1','end':'3600'}),
                       lambda p:p.update(job_id='other')):
            page=deepcopy(FIXTURE['base_page']);modify(page['record'])
            with self.assertRaises(t.TemporalError): self.recognize(page=page)
        bounds=deepcopy(FIXTURE['bounds']);bounds['record']['parent']['job_id']='other'
        with self.assertRaises(t.TemporalError): self.recognize(bounds=bounds)

    def test_parent_unplaced_provisional_numeric_and_complete_rejected(self):
        mutations = [lambda o:o['placement'].update(status='UNPLACED',coordinate=None,policy=None),
                     lambda o:o.update(state='INDETERMINATE',finality='PROVISIONAL'),
                     lambda o:o['placement'].update(pts=0),
                     lambda o:o['placement'].update(coordinate='other'),
                     lambda o:o['support'].update(kind='unplaced',sample_ref=None)]
        for mutate in mutations:
            page=deepcopy(FIXTURE['base_page']);mutate(page['record']['observations'][0])
            with self.subTest(mutate=mutate), self.assertRaises(t.TemporalError): self.recognize(page=page,rebind=True)
        for kind in ('interval','sample'):
            page=deepcopy(FIXTURE['base_page']);obs=page['record']['observations'][0]
            obs['placement'].update(status='QUALIFIED_METADATA_TIME')
            obs['support'].update(kind=kind,sample_ref=None)
            obs['support']['interval' if kind=='interval' else 'point'] = {'start':'0','end':'10'} if kind=='interval' else rational(1)
            with self.assertRaises(t.TemporalError): self.recognize(page=page,rebind=True)
        page=deepcopy(FIXTURE['base_page']);p=page['record'];p['lifecycle']='COMPLETE';p['progress']['phase']='COMPLETE';p['unresolved_refs']=[]
        with self.assertRaisesRegex(t.TemporalError,'cannot be COMPLETE'): self.recognize(page=page)

    def test_source_dependency_and_proof_scope_binding(self):
        for field,value in (('source_refs',['received-1']),('dependency_refs',['origin-proof'])):
            bounds=deepcopy(FIXTURE['bounds']);bounds['record'][field]=value
            with self.assertRaises(t.TemporalError): self.recognize(bounds=bounds)
        for field,value in (('source_refs',['other']),('dependency_refs',[]),('coordinate','other'),
                            ('observation_sha256','0'*64),('model','source-order-only'),('cases',[])):
            proof=deepcopy(FIXTURE['proof']);proof[field]=value
            with self.subTest(field=field), self.assertRaises(t.TemporalError): self.recognize(proof=proof)
        # Even a structurally valid tighter or wider envelope needs actual proof.
        b=deepcopy(FIXTURE['bounds']);b['record']['end_max']=rational(11)
        with self.assertRaisesRegex(t.TemporalError,'not supported'): self.recognize(bounds=b)
        page=deepcopy(FIXTURE['base_page']);page['record']['actual_context_refs']=[]
        with self.assertRaises(t.TemporalError): self.recognize(page=page)
        page=deepcopy(FIXTURE['base_page']);page['record']['observations'][0]['evidence_refs'].append('unresolved-proof')
        with self.assertRaises(t.TemporalError): self.recognize(page=page,rebind=True)
        with self.assertRaisesRegex(t.TemporalError,'unsupported evidence profile'):
            self.recognize(proof_profile='unknown-proof')

    def test_qualified_alternatives_and_source_order_not_automatic_extents(self):
        page,b,proof=deepcopy(FIXTURE['base_page']),deepcopy(FIXTURE['bounds']),deepcopy(FIXTURE['proof'])
        support=page['record']['observations'][0]['support'];support.update(kind='ambiguous',sample_ref=None,alternatives=['alt-1','alt-2'])
        b['record']['dependency_refs'] += ['alt-1','alt-2'];proof['dependency_refs'] += ['alt-1','alt-2']
        proof['cases'][0]['alternative_ref']='alt-1';proof['cases'][1]['alternative_ref']='alt-2'
        self.assertEqual(len(self.recognize(page=page,bounds=b,proof=proof,rebind=True).attachments),1)
        proof['cases'][1]['alternative_ref']='alt-1'
        with self.assertRaisesRegex(t.TemporalError,'unresolved alternatives'): self.recognize(page=page,bounds=b,proof=proof,rebind=True)
        page=deepcopy(FIXTURE['base_page']);b=deepcopy(FIXTURE['bounds']);proof=deepcopy(FIXTURE['proof'])
        page['record']['observations'][0]['support'].update(kind='order',sample_ref=None,left_ref='received-1')
        b['record']['dependency_refs'].append('received-1');proof['dependency_refs'].append('received-1')
        proof['model']='source-order-only'
        with self.assertRaises(t.TemporalError): self.recognize(page=page,bounds=b,proof=proof,rebind=True)

    def test_unknown_policy_and_profile_rejected(self):
        page,b,proof=deepcopy(FIXTURE['base_page']),deepcopy(FIXTURE['bounds']),deepcopy(FIXTURE['proof'])
        page['record']['observations'][0]['placement']['policy']='unknown-policy'
        b['record']['policy']='unknown-policy';proof['policy']='unknown-policy'
        with self.assertRaisesRegex(t.TemporalError,'unsupported temporal policy'): self.recognize(page=page,bounds=b,proof=proof,rebind=True)
        for field in ('base_page','attachments'):
            def mutate(m):
                target=m['record'][field]
                if type(target) is list: target=target[0]
                target['profile']='unknown'
            with self.assertRaises(t.TemporalError): self.recognize(edit_manifest=mutate)

    def test_missing_extra_orphan_duplicate_and_conflicting_attachments(self):
        root,blobs=assemble();m=json.loads(root)
        for attachments in ([],m['record']['attachments']*2):
            bad=deepcopy(m);bad['record']['attachments']=attachments
            with self.assertRaises(t.TemporalError): t.recognize(encode(bad),resolve(blobs))
        with self.assertRaisesRegex(t.TemporalError,'unavailable'):
            t.recognize(root,resolve({k:v for k,v in blobs.items() if k!='bounds-1'}))
        for field in ('observations','evidence'):
            page=deepcopy(FIXTURE['base_page']);page['record'][field]=[]
            with self.assertRaises(t.TemporalError): self.recognize(page=page)
        page=deepcopy(FIXTURE['base_page']);page['record']['observations'][0]['evidence_refs'].remove('bounds-1')
        with self.assertRaises(t.TemporalError): self.recognize(page=page,rebind=True)
        page=deepcopy(FIXTURE['base_page']);page['record']['evidence'].append(deepcopy(page['record']['evidence'][0]))
        with self.assertRaises(t.TemporalError): self.recognize(page=page)
        page=deepcopy(FIXTURE['base_page']);page['record']['observations'].append(deepcopy(page['record']['observations'][0]))
        with self.assertRaises(t.TemporalError): self.recognize(page=page)
        # Two distinct descriptor IDs bound to the same parent are not two slots.
        root,blobs=assemble();base=json.loads(blobs['base-page']);obs=base['record']['observations'][0]
        obs['evidence_refs'].append('bounds-2')
        parent_sha=t.sha256(observation_bytes(obs))
        proof=deepcopy(FIXTURE['proof']);proof['observation_sha256']=parent_sha;proofdata=encode(proof)
        pref=descriptor('proof-1',proofdata,t.SYNTHETIC_PROOF_PROFILE)
        refs=[]
        for identity in ('bounds-1','bounds-2'):
            b=deepcopy(FIXTURE['bounds']);b['record']['attachment_id']=identity;b['record']['parent']['observation_sha256']=parent_sha
            b['record']['evidence']=[pref];data=encode(b);blobs[identity]=data;refs.append(descriptor(identity,data,t.BOUNDS_PROFILE))
        blobs['proof-1']=proofdata;base['record']['evidence']=refs+[pref];blobs['base-page']=encode(base)
        m=json.loads(root);m['record']['base_page']=descriptor('base-page',blobs['base-page'],t.BASE_PROFILE);m['record']['attachments']=refs
        with self.assertRaises(t.TemporalError): t.recognize(encode(m),resolve(blobs))

    def test_atomic_recognition_generations_and_previous_evidence_preserved(self):
        root,blobs=assemble();prior=t.recognize(root,resolve(blobs));snapshot=t.dumps(prior.manifest)
        self.assertEqual(t.recognize(root,resolve(blobs),previous=prior),prior)
        next_root=json.loads(root);next_root['record']['generation']=1
        later=t.recognize(encode(next_root),resolve(blobs),previous=prior)
        self.assertEqual(later.manifest.generation,1)
        for generation in (0,2):
            obj=deepcopy(next_root);obj['record']['generation']=generation
            if generation==0: obj['record']['base_page']['sha256']='0'*64
            with self.assertRaises(t.TemporalError): t.recognize(encode(obj),resolve(blobs),previous=prior)
        with self.assertRaises(t.TemporalError): t.recognize(root,resolve(blobs),previous=later)
        with self.assertRaises(t.TemporalError): t.recognize(encode(next_root),resolve(blobs))
        for key,value in (('publication_id','other'),('request_sha256','0'*64),('job_id','other')):
            obj=deepcopy(next_root);obj['record'][key]=value
            with self.assertRaises(t.TemporalError): t.recognize(encode(obj),resolve(blobs),previous=prior)
        damaged=dict(blobs);damaged['proof-1']=damaged['proof-1'][:-1]+b'!'
        with self.assertRaises(t.TemporalError): t.recognize(encode(next_root),resolve(damaged),previous=prior)
        self.assertEqual(t.dumps(prior.manifest),snapshot)
        self.assertEqual(prior.attachments[0].definite_core,(Fraction(1),Fraction(9)))
        self.assertEqual(prior.base_page.observations[0].state,a.SigningState.VERIFIED_SIGNING)

    def test_immutable_artifact_ids_not_reused_for_changed_bytes(self):
        root,blobs=assemble();prior=t.recognize(root,resolve(blobs))
        page=deepcopy(FIXTURE['base_page']);page['record']['observations'][0]['report']['revision']=1
        newroot,newblobs=assemble(page=page,rebind=True,edit_manifest=lambda m:m['record'].update(generation=1))
        with self.assertRaisesRegex(t.TemporalError,'immutable evidence ID conflict'):
            t.recognize(newroot,resolve(newblobs),previous=prior)

    def test_resource_parser_resolver_caps_and_no_partial_return(self):
        root,blobs=assemble();calls=[]
        def limited(ref,max_bytes): calls.append((ref.evidence_id,max_bytes));return resolve(blobs)(ref,max_bytes)
        for limits in (t.ValidationLimits(max_resolver_calls=1),t.ValidationLimits(max_bytes=len(root)),t.ValidationLimits(max_cases=1)):
            with self.assertRaises(t.TemporalError): t.recognize(root,limited,limits=limits)
        self.assertTrue(all(0<n<=t.MAX_PUBLICATION_BYTES for _,n in calls))
        with self.assertRaises(t.TemporalError): t.ValidationLimits(max_bytes=t.MAX_PUBLICATION_BYTES+1)
        with self.assertRaises(t.TemporalError): t.ValidationLimits(max_resolver_calls=True)
        def oversized(ref,max_bytes): return b'x'*(max_bytes+1)
        with self.assertRaises(t.TemporalError): t.recognize(root,oversized)
        def cancelled(): raise RuntimeError('cancelled by trusted caller')
        with self.assertRaisesRegex(RuntimeError,'cancelled'): t.recognize(root,limited,check=cancelled)
        doc=deepcopy(FIXTURE['bounds']);doc['record']['source_refs']=['x']*129
        with self.assertRaises(t.TemporalError): t.loads(encode(doc))
        doc=deepcopy(FIXTURE['bounds']);doc['record']['source_refs']=['x','x']
        with self.assertRaises(t.TemporalError): t.loads(encode(doc))
        # A source/profile change cannot be swallowed as a fallback base result.
        missing=dict(blobs);missing.pop('proof-1')
        with self.assertRaises(t.TemporalError): t.recognize(root,resolve(missing))
