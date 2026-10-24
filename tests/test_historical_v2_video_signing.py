"""Rule-first metadata fixtures: no media, native execution or target real colors."""
import base64
import copy
import json
import unittest
from dataclasses import replace
from nanexus_trust.historical_v2 import contracts as a, wire
from nanexus_trust.historical_v2.composition import canonical
from nanexus_trust.historical_v2.continuity_protocol import sha
from nanexus_trust.historical_v2.video_signing import recognize as raw_recognize, EvidenceError
from nanexus_trust.companion.window_http import finding as raw_finding
from test_historical_v2_rolling_product import fixture, update, terminal


def recognize(entry,row):
    return raw_recognize(entry,row,owner='owner',camera=a.Camera('logical','camera','binding','Camera'),request_id='rolling-test')


def finding(entry,row):
    return raw_finding(entry,row,owner='owner',camera=a.Camera('logical','camera','binding','Camera'),request_id='rolling-test')


def emit(types='PSv', statuses='.._', partial=False, exceptional=False):
    p,q,loaded,out,r,b = fixture(1,len(types))
    r.register(p.originals[0],*loaded[0])
    for n in range(len(types)):
        # Delay retirement until the entire scripted source has arrived.
        u = json.loads(update(r, statuses if n==len(types)-1 else 'P'*(n+1), types if n==len(types)-1 else 'U'*(n+1)))
        for i in u['items']:
            i['axes'].update(authenticity=3 if partial else 4,combined=1 if partial else 2)
            if exceptional and i['nalu_type']=='v':i['axes'].update(authenticity=1,combined=3)
        r.accept(canonical(u))
    r.commit_source(0)
    if not out:terminal(r)
    return out[0],b.files[0],r,b


def mutate(entry, prefix, fn):
    """Rebind canonical objects as Bundle would, so tests reach inner bindings."""
    e=copy.deepcopy(entry)
    for obj in e['result']['objects']:
        if obj['id'].startswith(prefix+'-'):
            value=json.loads(base64.b64decode(obj['data']));fn(value)
            raw=canonical(value);obj.update(data=base64.b64encode(raw).decode(),sha256=sha(raw),size=len(raw))
            for ref in e['result']['page']['record']['evidence']:
                if ref['evidence_id']==obj['id']:ref.update(sha256=sha(raw),size=len(raw))
    raw=canonical(e['result']['page']);e['result'].update(sha256=sha(raw),size=len(raw))
    return e


class VideoSigningTests(unittest.TestCase):
    def test_qualified_video_and_controls_preserved(self):
        e,row,_,_=emit();before=canonical(e)
        self.assertEqual(recognize(e,row),'GREEN');self.assertEqual(finding(e,row),'GREEN')
        self.assertEqual(row['finalized_counts'],{'VERIFIED_SIGNING':1,'INDETERMINATE':2})
        self.assertEqual(canonical(e),before)

    def test_video_scope_nonempty_and_strict_axes(self):
        for args in [('Sv','._'),('PSv','.._')]:
            e,row,_,_=emit(*args,partial=args[0]=='PSv')
            self.assertEqual(recognize(e,row),'AMBER')
        e,row,_,_=emit('P','.');self.assertEqual(recognize(e,row),'GREEN')

    def test_exceptional_and_unknown_controls_not_ignored(self):
        e,row,_,_=emit(exceptional=True);self.assertEqual(recognize(e,row),'AMBER')
        for typ,status in [('C','U'),('S','U'),('v','N')]:
            e,row,_,_=emit('P'+typ,'.'+status);self.assertEqual(recognize(e,row),'AMBER')

    def test_negative_precedence_and_native_truth(self):
        p,q,ls,out,r,b=fixture(1);r.register(p.originals[0],*ls[0]);r.accept(update(r,'N'));r.commit_source(0)
        self.assertEqual(recognize(out[0],b.files[0]),'RED')

    def test_pending_is_terminal_only(self):
        p,q,ls,out,r,b=fixture(1);r.register(p.originals[0],*ls[0]);r.accept(update(r,'P'));r.commit_source(0)
        self.assertFalse(out);terminal(r)
        self.assertEqual(recognize(out[0],b.files[0]),'AMBER')
        self.assertEqual(b.files[0]['unresolved'],1)

    def test_structural_and_binding_failures_raise(self):
        e,row,_,_=emit()
        changes=[('sources',lambda s:s['occurrences'][0].update(token='wrong')),
                 ('sources',lambda s:s['files'][0]['receipt'].update(sha256='0'*64)),
                 ('sources',lambda s:s['candidate'].update(request_sha256='0'*64)),
                 ('sources',lambda s:s['candidate'].update(owner_sha256='0'*64)),
                 ('sources',lambda s:s['files'][0].update(committed=False)),
                 ('trust',lambda t:t.update(sha256='0'*64)),
                 ('trust',lambda t:t.update(configuration='0'*64)),
                 ('updates',lambda us:us[-1]['items'][0].update(revision=999)),
                 ('updates',lambda us:us[-1].update(generating_feed_ordinal=999)),
                 ('updates',lambda us:us[-1].update(retired_prefix_ordinal=None)),
                 ('updates',lambda us:us[-1]['items'][0]['axes'].update(key_changed=True)),
                 ('updates',lambda us:us[-1]['items'][0]['axes'].update(provenance=0))]
        for prefix,change in changes:
            with self.subTest(prefix=prefix,change=change),self.assertRaises(EvidenceError):recognize(mutate(e,prefix,change),row)
        for key in ('generation','recording'):
            bad=copy.deepcopy(e);bad[key]='wrong'
            with self.assertRaises(EvidenceError):recognize(bad,row)
        bad=copy.deepcopy(e);bad['result']['objects'].pop()
        with self.assertRaises(EvidenceError):recognize(bad,row)
        bad=copy.deepcopy(e);bad['execution']['state']='PROVISIONAL'
        with self.assertRaises(EvidenceError):recognize(bad,row)

    def test_missing_report_and_duplicate_source(self):
        e,row,_,_=emit()
        with self.assertRaises(EvidenceError):recognize(mutate(e,'updates',lambda us:us.clear()),row)
        with self.assertRaises(EvidenceError):recognize(mutate(e,'sources',lambda s:s['files'].append(s['files'][0])),row)

    def test_unknown_profile_keeps_legacy_policy(self):
        e,row,_,_=emit();e['result']['page']['record']['engine_profile']='unknown'
        self.assertIsNone(recognize(e,row));self.assertEqual(finding(e,row),'AMBER')
        e,row,_,_=emit();e=mutate(e,'sources',lambda s:s['candidate'].update(policy='unsupported'))
        self.assertEqual(recognize(e,row),'AMBER')

    def test_continuation_and_cross_file_retirement(self):
        p,q,ls,out,r,b=fixture(2,131);r.register(p.originals[0],*ls[0])
        for n in range(131):
            u=json.loads(update(r,'P'*(n+1)));r.accept(canonical(u))
        r.commit_source(0);self.assertFalse(out)
        r.register(p.originals[1],*ls[1]);u=json.loads(update(r,'.'*132))
        for i in u['items']:i['axes'].update(authenticity=4,combined=2)
        r.accept(canonical(u));r.commit_source(1)
        self.assertEqual(r.cross_file_retirements,131)
        for e,row in zip(out,b.files):self.assertEqual(recognize(e,row),'GREEN')
        self.assertIsNotNone(out[0]['result']['page']['record']['next_cursor'])
        before=copy.deepcopy(b._objects)
        with self.assertRaises(ValueError):r.accept(canonical(u))
        self.assertEqual(before,b._objects)

    def test_required_later_control_context_is_not_ignored(self):
        for raw in ('.','U'):
            p,q,ls,out,r,b=fixture(2)
            r.register(p.originals[0],*ls[0]);r.accept(update(r,'P'));r.commit_source(0)
            self.assertFalse(out)
            r.register(p.originals[1],*ls[1]);r.accept(update(r,'.'+raw,'PS'));r.commit_source(1)
            expected='GREEN' if raw=='.' else 'AMBER'
            self.assertEqual(recognize(out[0],b.files[0]),expected)

    def test_absent_authority_cannot_promote(self):
        e,row,_,_=emit()
        self.assertEqual(raw_recognize(e,row),'AMBER')
        with self.assertRaises(EvidenceError):raw_recognize(e,row,owner='other',camera=a.Camera('logical','camera','binding','Camera'),request_id='rolling-test')

    def test_authorized_camera_and_request_cannot_be_substituted(self):
        e,row,_,_=emit()
        for camera,rid in [(a.Camera('logical','other','binding','Camera'),'rolling-test'),
                           (a.Camera('logical','camera','binding','Camera'),'other-request')]:
            with self.assertRaises(EvidenceError):raw_recognize(e,row,owner='owner',camera=camera,request_id=rid)

    def test_caps_and_invalid_object(self):
        e,row,_,_=emit();e['result']['objects'][0]['data']='A'*((6<<20)+1)
        with self.assertRaises(EvidenceError):recognize(e,row)

    def test_object_count_bound(self):
        e,row,_,_=emit();e['result']['objects']=[e['result']['objects'][0]]*129
        with self.assertRaises(EvidenceError):recognize(e,row)

    def test_shared_report_preserves_released_control_source_receipt(self):
        p,q,ls,out,r,b=fixture(2)
        r.register(p.originals[0],*ls[0]);r.accept(update(r,'P'));r.commit_source(0)
        self.assertFalse(out)
        r.register(p.originals[1],*ls[1]);r.accept(update(r,'..','SP'));r.commit_source(1)
        self.assertFalse(r.files[0]['items'])  # earlier mutable work released
        e=out[1];source=next(json.loads(base64.b64decode(o['data'])) for o in e['result']['objects'] if o['id'].startswith('sources-'))
        self.assertEqual({f['original']['item_id'] for f in source['files']},{i.item_id for i in p.originals})
        self.assertEqual(recognize(e,b.files[1]),'GREEN')
        # Removing/corrupting the actually referenced context is still an error.
        missing=mutate(e,'sources',lambda s:s['files'].pop(0))
        with self.assertRaises(EvidenceError):recognize(missing,b.files[1])
        corrupt=mutate(e,'sources',lambda s:s['files'][0]['receipt'].update(sha256='0'*64))
        with self.assertRaises(EvidenceError):recognize(corrupt,b.files[1])
        token=mutate(e,'updates',lambda us:us[0]['items'][0].update(source_token_hex=b'wrong'.hex()))
        with self.assertRaises(EvidenceError):recognize(token,b.files[1])

    def test_shared_control_ambiguity_still_blocks_positive_scope(self):
        p,q,ls,out,r,b=fixture(2)
        r.register(p.originals[0],*ls[0]);r.accept(update(r,'P'));r.commit_source(0)
        r.register(p.originals[1],*ls[1]);r.accept(update(r,'U.','SP'));r.commit_source(1)
        self.assertEqual(recognize(out[1],b.files[1]),'AMBER')
        self.assertEqual(b.files[1]['finalized_counts'],{'VERIFIED_SIGNING':1})
