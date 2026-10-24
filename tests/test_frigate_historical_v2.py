"""New exact-profile foundation controls; mutations use owned scratch only."""
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

from nanexus_trust.frigate.historical_v2 import (
    FrigateHistoricalProvider, Snapshot, ProviderFailure, PIN, PROFILE,
)
from nanexus_trust.historical_v2.contracts import Camera, Interval, Quality, Rational
from nanexus_trust.historical_v2.provider import Bounds, DiscoveryRequest

FIXTURE = json.loads((Path(__file__).parent / 'fixtures/historical-v2-provider.json').read_text())


class Cancel:
    def __init__(self, value=False): self.value = value
    def cancelled(self): return self.value


def bounds(**changes):
    b = Bounds(16, 1024 * 1024, 0, 0, 1, 20000,
               Rational(int((time.monotonic() + 30) * 1000000), 1000000), 'offline-test')
    return replace(b, **changes)


def request(default_camera, **changes):
    r = DiscoveryRequest(default_camera, Interval('10', '16'), 'host-metadata-unix-seconds', bounds(), None)
    return replace(r, **changes)


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.media = self.root / 'recordings'; (self.media / 'owned').mkdir(parents=True)
        for name in ('a', 'b'): (self.media / f'owned/{name}.mp4').write_bytes(b'owned' * 20000)
        self.scratch = self.root / 'scratch'; self.scratch.mkdir(mode=0o700)
        self.db = self.root / 'snapshot.db'
        with sqlite3.connect(self.db) as db:
            cols = ','.join('"' + k + '" ' + v + (' PRIMARY KEY' if k == 'id' else '')
                            for k, v in FIXTURE['columns'].items())
            db.execute('CREATE TABLE recordings (' + cols + ')')
            db.execute('CREATE INDEX recordings_camera_start_time_end_time ON recordings(camera,start_time,end_time)')
            db.executemany('INSERT INTO recordings(id,camera,path,start_time,end_time,duration) VALUES(?,?,?,?,?,?)', FIXTURE['rows'])
        self.camera = Camera('offline', 'cam', 'binding-1', 'Camera')
        self.authority = {'allowed': True, 'generation': 'binding-1'}
        self.provider = self.make_provider()
        self.addCleanup(self.provider.close)
        self.cancel = Cancel()

    def snapshot(self, **changes):
        return replace(Snapshot(self.db, hashlib.sha256(self.db.read_bytes()).hexdigest(),
                                'owned-cut', PIN, True, True), **changes)

    def make_provider(self, **changes):
        return FrigateHistoricalProvider(self.camera, self.snapshot(**changes), self.media, self.scratch,
             native_authorize=lambda c: self.authority['allowed'] and c.binding_generation == self.authority['generation'])

    def page(self, **changes): return self.provider.discover(request(self.camera, **changes), self.cancel)

    def test_profile_complete_unknown_and_overflow(self):
        cap = self.provider.capabilities(self.camera)
        self.assertEqual(cap.provider_profile, PROFILE)
        self.assertFalse(cap.qualified_historical_anchor)
        page = self.page()
        self.assertEqual([i.item_id for i in page.items], ['a', 'b'])
        self.assertEqual(page.completeness, Quality.SATISFIED)
        self.assertTrue(all(i.anchor is None and i.origin_policy is None and not i.size_exact for i in page.items))
        self.assertEqual(self.page(bounds=bounds(max_items=1)).completeness, Quality.INCOMPLETE)
        with self.make_provider(complete=False) as p:
            self.assertEqual(p.discover(request(self.camera), self.cancel).completeness, Quality.UNKNOWN)
        empty = self.page(requested=Interval('20', '21'))
        self.assertFalse(empty.items)
        self.assertEqual(empty.completeness, Quality.SATISFIED)
        with self.make_provider(self_contained=False) as p:
            with self.assertRaises(ProviderFailure): p.discover(request(self.camera), self.cancel)

    def test_schema_pin_hash_and_work_deadline(self):
        with self.assertRaises(ProviderFailure): self.snapshot(source_pin='wrong')
        with self.make_provider(sha256='0' * 64) as p:
            with self.assertRaises(ProviderFailure): p.discover(request(self.camera), self.cancel)
        with self.assertRaises(ProviderFailure): self.page(bounds=bounds(max_work_units=1))
        with self.assertRaisesRegex(ProviderFailure, 'deadline'):
            self.page(bounds=bounds(deadline_monotonic=Rational(1, 1)))
        with sqlite3.connect(self.db) as db: db.execute('DROP INDEX recordings_camera_start_time_end_time')
        with self.make_provider() as p:
            with self.assertRaisesRegex(ProviderFailure, 'schema-index'): p.discover(request(self.camera), self.cancel)
        with sqlite3.connect(self.db) as db: db.execute('ALTER TABLE recordings ADD COLUMN unexpected INTEGER')
        with self.make_provider() as p:
            with self.assertRaisesRegex(ProviderFailure, 'schema'): p.discover(request(self.camera), self.cancel)

    def test_scope_context_overlap_and_invalid_requests(self):
        precise = self.page(requested=Interval('9.999999999', '10.000000001'))
        self.assertEqual([i.item_id for i in precise.items], ['a'])
        p = self.page(requested=Interval('12', '14'), bounds=bounds(max_context_items=2))
        self.assertEqual(p.predecessor_refs, ('a',)); self.assertEqual(p.successor_refs, ('b',))
        self.assertEqual(self.page(requested=Interval('12', '14'), bounds=bounds(max_context_items=1)).completeness, Quality.INCOMPLETE)
        for changes in ({'coordinate': 'other'}, {'cursor': 'untrusted'}, {'camera': replace(self.camera, binding_generation='bad')}):
            with self.assertRaises(ProviderFailure): self.page(**changes)
        with sqlite3.connect(self.db) as db: db.execute('UPDATE recordings SET start_time=11 WHERE id="b"')
        with self.make_provider() as provider:
            self.assertEqual(set(provider.discover(request(self.camera), self.cancel).ambiguous_refs), {'a', 'b'})

    def test_auth_revocation_binding_cancellation(self):
        item = self.page().items[0]
        self.authority['allowed'] = False
        for operation in (lambda: self.provider.capabilities(self.camera), self.page,
                          lambda: self.provider.acquire(item, bounds(), self.cancel)):
            with self.assertRaisesRegex(ProviderFailure, 'access-denied'): operation()
        self.authority['allowed'] = True
        self.authority['generation'] = 'changed'
        with self.assertRaises(ProviderFailure): self.page()
        self.authority['generation'] = 'binding-1'
        self.cancel.value = True
        with self.assertRaisesRegex(ProviderFailure, 'cancelled'): self.page()
        self.cancel.value = False
        calls = 0
        def guard(camera):
            nonlocal calls
            calls += 1
            return calls < 20
        with patch.object(self.provider, '_authorize', guard):
            with self.assertRaisesRegex(ProviderFailure, 'access-denied'): self.page()

    def test_stage_identity_read_and_cleanup_after_revocation(self):
        item = self.page().items[0]
        h = self.provider.acquire(item, bounds(), self.cancel)
        source = self.media / 'owned/a.mp4'
        self.assertEqual(h.sha256, hashlib.sha256(source.read_bytes()).hexdigest())
        source.unlink()
        self.assertEqual(self.provider.read(h, 0, 5, self.cancel), b'owned')
        for off, amount in ((-1, 1), (0, 65537), (h.actual_bytes + 1, 1)):
            with self.assertRaises(ProviderFailure): self.provider.read(h, off, amount, self.cancel)
        with self.assertRaises(ProviderFailure): self.provider.read(replace(h, generation='forged'), 0, 1, self.cancel)
        with self.assertRaises(ProviderFailure): self.provider.acquire(item, bounds(), self.cancel)
        self.authority['allowed'] = False
        with self.assertRaises(ProviderFailure): self.provider.read(h, 0, 1, self.cancel)
        self.provider.release(h); self.provider.release(h)
        self.assertEqual(list(self.provider._root.iterdir()), [])

    def test_unsafe_missing_mutated_replaced_and_cancelled_copy(self):
        item = self.page().items[0]
        source = self.media / 'owned/a.mp4'
        with self.assertRaises(ProviderFailure): self.provider.acquire(replace(item, generation='bad'), bounds(), self.cancel)
        with self.assertRaises(ProviderFailure): self.provider.acquire(item, bounds(max_item_bytes=1), self.cancel)
        original = os.read
        for case in ('mutate', 'unlink', 'replace', 'cancel', 'revoke'):
            source.write_bytes(b'x' * 100000)
            done = False
            def reading(fd, n):
                nonlocal done
                result = original(fd, n)
                if not done:
                    done = True
                    if case == 'mutate':
                        with source.open('ab') as f: f.write(b'changed')
                    if case == 'unlink': source.unlink()
                    if case == 'replace':
                        other = self.media / 'replacement'; other.write_bytes(b'new'); other.replace(source)
                    if case == 'cancel': self.cancel.value = True
                    if case == 'revoke': self.authority['allowed'] = False
                return result
            with patch('nanexus_trust.frigate.historical_v2.os.read', reading):
                with self.assertRaises(ProviderFailure): self.provider.acquire(item, bounds(), self.cancel)
            self.assertEqual(list(self.provider._root.iterdir()), [])
            self.cancel.value = False; self.authority['allowed'] = True
        source.unlink(missing_ok=True)
        with self.assertRaises(ProviderFailure): self.provider.acquire(item, bounds(), self.cancel)
        source.symlink_to(self.media / 'owned/b.mp4')
        with self.assertRaises(ProviderFailure): self.provider.acquire(item, bounds(), self.cancel)
        source.unlink(); source.write_bytes(b'owned')
        (self.media / 'owned').rename(self.media / 'real')
        (self.media / 'owned').symlink_to(self.media / 'real', target_is_directory=True)
        with self.assertRaises(ProviderFailure): self.provider.acquire(item, bounds(), self.cancel)


class Profile0182Tests(unittest.TestCase):
    """Exact new target; all schema mutations are in owned synthetic copies."""
    setUp = ProviderTests.setUp
    snapshot = ProviderTests.snapshot
    make_provider = ProviderTests.make_provider

    def target(self, **changes):
        from nanexus_trust.frigate.historical_v2 import PIN_0182, PROFILE_0182
        snap = self.snapshot(source_pin=PIN_0182)
        return FrigateHistoricalProvider(self.camera, snap, self.media, self.scratch,
            native_authorize=lambda c: self.authority['allowed'],
            profile=changes.get('profile', PROFILE_0182))

    def schema(self, *, change=None, reverse=False, descending=False):
        cols = dict(FIXTURE['columns'])
        if change == 'missing': cols.pop('motion_heatmap')
        if change == 'extra': cols['unexpected'] = 'TEXT'
        if change == 'type': cols['regions'] = 'TEXT'
        items = list(cols.items())
        if reverse: items.reverse()
        definitions = ['"'+k+'" '+v+(' PRIMARY KEY' if k=='id' and change not in ('pk','composite') else '')
                       for k,v in items]
        if change == 'composite': definitions.append('PRIMARY KEY(id,camera)')
        with sqlite3.connect(self.db) as db:
            db.execute('DROP TABLE recordings')
            db.execute('CREATE TABLE recordings ('+','.join(definitions)+')')
            if change != 'index_missing':
                names = 'start_time,camera,end_time' if change == 'index_order' else 'camera,start_time,end_time'
                if descending: names='camera,start_time DESC,end_time DESC'
                db.execute('CREATE INDEX recordings_camera_start_time_end_time ON recordings('+names+')')
            db.executemany('INSERT INTO recordings(id,camera,path,start_time,end_time,duration) VALUES(?,?,?,?,?,?)',FIXTURE['rows'])

    def test_exact_profile_admission_and_old_default(self):
        from nanexus_trust.frigate.historical_v2 import PROFILE_0182
        for reverse, descending in ((False,False),(True,True)):
            self.schema(reverse=reverse,descending=descending)
            with self.target() as p:
                page=p.discover(request(self.camera),self.cancel)
                self.assertEqual(page.provider_profile,PROFILE_0182)
                self.assertEqual(p.capabilities(self.camera).provider_profile,PROFILE_0182)
                self.assertEqual([x.item_id for x in page.items],['a','b'])
                self.assertTrue(all(x.anchor is None for x in page.items))
            with self.make_provider() as old:
                self.assertEqual(old.discover(request(self.camera),self.cancel).provider_profile,PROFILE)

    def test_pin_selector_mismatch_unknown_and_removed_0171(self):
        from nanexus_trust.frigate.historical_v2 import PROFILE_0182, PIN_0182
        for profile,pin in ((PROFILE_0182,PIN),(PROFILE,PIN_0182),('frigate-0.18.x',PIN_0182),
                            ('frigate-0.17.1-416a9b7-h264-mp4-offline-v1',PIN)):
            with self.subTest(profile=profile),self.assertRaisesRegex(ProviderFailure,'profile'):
                FrigateHistoricalProvider(self.camera,self.snapshot(source_pin=pin),self.media,self.scratch,
                                         native_authorize=lambda c:True,profile=profile)
        for pin in ('unknown','416a9b7692e052be98ad503704d26c7ef7a4c88d'):
            with self.assertRaisesRegex(ProviderFailure,'profile'):self.snapshot(source_pin=pin)

    def test_schema_pk_index_drift_rejected(self):
        for change in ('missing','extra','type','pk','composite','index_missing','index_order'):
            self.schema(change=change)
            with self.subTest(change=change),self.target() as p,self.assertRaisesRegex(ProviderFailure,'schema'):
                p.discover(request(self.camera),self.cancel)

    def test_authority_snapshot_and_resource_controls(self):
        with self.target() as p:
            self.authority['allowed']=False
            with self.assertRaisesRegex(ProviderFailure,'access'):p.discover(request(self.camera),self.cancel)
            self.authority['allowed']=True
            with self.assertRaises(ProviderFailure):p.discover(request(self.camera,bounds=bounds(max_work_units=1)),self.cancel)
            with self.assertRaisesRegex(ProviderFailure,'deadline'):
                p.discover(request(self.camera,bounds=bounds(deadline_monotonic=Rational(1,1))),self.cancel)
            self.cancel.value=True
            with self.assertRaisesRegex(ProviderFailure,'cancel'):p.discover(request(self.camera),self.cancel)
            self.cancel.value=False
            p.snapshot=replace(p.snapshot,sha256='0'*64)
            with self.assertRaisesRegex(ProviderFailure,'digest'):p.discover(request(self.camera),self.cancel)
        with self.target() as p:
            p.snapshot=replace(p.snapshot,self_contained=False)
            with self.assertRaisesRegex(ProviderFailure,'self-contained'):p.discover(request(self.camera),self.cancel)
