"""Synthetic reader boundaries plus the authorized archived single-file/B witness."""
from dataclasses import replace
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from nanexus_trust.frigate.historical_v2 import FrigateHistoricalProvider, Snapshot, PIN
from nanexus_trust.historical_v2.contracts import Camera, Rational
from nanexus_trust.historical_v2.media import (
    read_nals, _nals, MediaFailure, ReaderLimits, SourceLedger,
)
from nanexus_trust.historical_v2.provider import StableHandle
from nanexus_trust.historical_v2.contracts import Quality
from test_frigate_historical_v2 import Cancel, bounds, request

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = json.loads((Path(__file__).parent / 'fixtures/historical-v2-media.json').read_text())


class MediaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.payloads = [b'\x67sps', b'\x68pps', b'\x65same', b'\x65same']
        self.data = b''.join(len(p).to_bytes(4, 'big') + p for p in self.payloads)
        self.path = self.root / 'synthetic'; self.path.write_bytes(self.data)
        self.probe = {'streams': [{'index': 0, 'codec_name': 'h264', 'codec_type': 'video',
                       'codec_tag_string': 'avc1', 'nal_length_size': '4', 'time_base': '1/90000'}],
                      'packets': [{'stream_index': 0, 'pos': '0', 'size': str(len(self.data)),
                                   'pts': 0, 'dts': 0, 'duration': 6000}]}
        self.handle = StableHandle(Camera('p', 'cam', 'g', 'Camera'), 'item', 'generation', 'h',
                                   len(self.data), hashlib.sha256(self.data).hexdigest(), Quality.SATISFIED, 'owned')

    def nals(self, probe=None, limits=ReaderLimits(), check=lambda: None):
        with self.path.open('rb') as f:
            return list(_nals(f, self.probe if probe is None else probe, 'owned', limits,
                              check, time.monotonic() + 5))

    def test_missing_and_nonzero_pts_preserve_local_facts(self):
        for case in FIXTURE['timing_controls']:
            p = self.probe['packets'][0]; p['pts'] = case['pts']; p['dts'] = case['dts']
            ns = self.nals()
            expected = None if case['local_pts'] is None else Fraction(case['local_pts'])
            self.assertTrue(all(n.local_pts == expected for n in ns))
            self.assertEqual(ns[0].pts, case['pts'])
        self.assertNotEqual(ns[2].token, ns[3].token)
        self.assertEqual(ns[2].payload_sha256, ns[3].payload_sha256)
        self.assertEqual([n.ordinal for n in ns], list(range(4)))
        self.assertEqual(b''.join(n.annex_b[4:] for n in ns), b''.join(self.payloads))

    def test_profile_extents_reorder_integer_and_caps(self):
        import copy
        variants = []
        for key, value in (('codec_name', 'hevc'), ('nal_length_size', '2'), ('time_base', '0/1')):
            p = copy.deepcopy(self.probe); p['streams'][0][key] = value; variants.append(p)
        for key, value in (('pts', 1), ('pos', -1), ('size', len(self.data) + 1),
                           ('duration', -1), ('stream_index', 1), ('pts', True)):
            p = copy.deepcopy(self.probe); p['packets'][0][key] = value; variants.append(p)
        for p in variants:
            with self.subTest(p=p), self.assertRaises(MediaFailure): self.nals(p)
        with self.assertRaises(MediaFailure): self.nals(limits=ReaderLimits(max_nals=1))
        with self.assertRaises(MediaFailure): self.nals(limits=ReaderLimits(max_nal_bytes=1))
        self.path.write_bytes(b'\0\0\0\0' + self.data[4:])
        with self.assertRaises(MediaFailure): self.nals()
        self.path.write_bytes(self.data[:-1])
        with self.assertRaises(MediaFailure): self.nals()

    def test_reader_copies_checks_hash_and_cleans(self):
        def read(off, count): return self.data[off:off+count]
        with patch('nanexus_trust.historical_v2.media._probe', return_value=self.probe):
            ns = list(read_nals(self.handle, read, lambda: None, ffprobe='unused', scratch_root=self.root))
            self.assertEqual(len(ns), 4)
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ['synthetic'])
        with self.assertRaises(MediaFailure):
            list(read_nals(replace(self.handle, sha256='0' * 64), read, lambda: None,
                           ffprobe='unused', scratch_root=self.root))
        with self.assertRaises(MediaFailure):
            list(read_nals(self.handle, lambda *_: b'', lambda: None, ffprobe='unused', scratch_root=self.root))
        def cancelled(): raise RuntimeError('cancelled')
        with self.assertRaisesRegex(RuntimeError, 'cancelled'):
            list(read_nals(self.handle, read, cancelled, ffprobe='unused', scratch_root=self.root))
        self.assertEqual(len(list(self.root.iterdir())), 1)

    def test_probe_failure_cap_cancel_and_deadline_cleanup(self):
        # Owned executable controls emit bytes or wait; no media processing.
        script = self.root / 'probe-control'
        script.write_text('#!/bin/sh\nprintf "01234567890123456789"\n'); script.chmod(0o700)
        with self.assertRaisesRegex(MediaFailure, 'probe-output-cap'):
            list(read_nals(self.handle, lambda o,n:self.data[o:o+n], lambda:None,
                 ffprobe=script, scratch_root=self.root, limits=ReaderLimits(max_probe_bytes=8)))
        script.write_text('#!/bin/sh\nexit 1\n')
        with self.assertRaisesRegex(MediaFailure, 'probe-failed'):
            list(read_nals(self.handle, lambda o,n:self.data[o:o+n], lambda:None,
                          ffprobe=script, scratch_root=self.root))
        script.write_text('#!/bin/sh\nwhile :; do :; done\n')
        with self.assertRaisesRegex(MediaFailure, 'reader-deadline'):
            list(read_nals(self.handle, lambda o,n:self.data[o:o+n], lambda:None,
                 ffprobe=script, scratch_root=self.root, limits=ReaderLimits(timeout_seconds=.05)))
        calls = 0
        def cancelled_in_probe():
            nonlocal calls
            calls += 1
            if calls >= 4: raise RuntimeError('cancel-in-probe')
        with self.assertRaisesRegex(RuntimeError, 'cancel-in-probe'):
            list(read_nals(self.handle, lambda o,n:self.data[o:o+n], cancelled_in_probe,
                          ffprobe=script, scratch_root=self.root))
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ['probe-control', 'synthetic'])

    def test_ledger_retirement_dependencies_cap_and_no_payload_retention(self):
        ns = self.nals(); ledger = SourceLedger(2)
        ledger.add(ns[0]); ledger.add(ns[1])
        with self.assertRaisesRegex(MediaFailure, 'cap'): ledger.add(ns[2])
        ledger.retire(1, (ns[1].token,))
        self.assertEqual(set(ledger.records), {1})
        ledger.add(ns[2]); ledger.retire(1)
        self.assertEqual(set(ledger.records), {2})
        ledger.add(ns[3]); ledger.retire(3)
        self.assertFalse(ledger.records)
        with self.assertRaises(MediaFailure): ledger.retire(2)
        with self.assertRaises(MediaFailure): SourceLedger().add(ns[1])


class ArchivedReaderBTests(unittest.TestCase):
    def test_actual_reader_and_accepted_B_source_join(self):
        capture = ROOT / '.local/runtime/task-34/rerun-02/evidence'
        c1 = ROOT / '.local/governance/ai-dlc/evidence/m1-hv2-c1-provider-preflight/artifacts'
        archived = capture / 'retained'
        dbpath = archived / 'frigate.db'
        proof = json.loads((archived / 'snapshot-proof.json').read_text())
        self.assertTrue(proof['self_contained'] and proof['backup_rows_equal_source_transaction'])
        self.assertEqual(proof['backup_integrity_check'], 'ok')
        self.assertEqual(hashlib.sha256(dbpath.read_bytes()).hexdigest(), FIXTURE['archived_snapshot_sha256'])
        sample = archived / 'recordings' / FIXTURE['sample']['relative_path']
        before = hashlib.sha256(sample.read_bytes()).hexdigest()
        self.assertEqual(before, FIXTURE['sample']['sha256'])
        obs = json.loads((capture / 'retained-observation.json').read_text())
        self.assertEqual(obs['files']['1792105204.0-mqcybg']['sha256'], before)
        ffprobe = shutil.which('ffprobe')
        self.assertIsNotNone(ffprobe, 'required offline dependency missing: STOP')
        with tempfile.TemporaryDirectory(prefix='hv2-c-b-') as temp:
            w = Path(temp); scratch = w / 'stage'; scratch.mkdir(mode=0o700)
            camera = Camera('archived-task34', 'c210_signed', 'captured-binding', 'Archived')
            snapshot = Snapshot(dbpath, FIXTURE['archived_snapshot_sha256'], 'task34-captured-cut', PIN, True, True)
            with FrigateHistoricalProvider(camera, snapshot, archived / 'recordings', scratch,
                                            native_authorize=lambda c:c == camera) as provider:
                cancel = Cancel(); b = bounds()
                # Scope selects only A, with no context expansion or composition.
                from nanexus_trust.historical_v2.contracts import Interval
                page = provider.discover(request(camera, requested=Interval('1792105204', '1792105205')), cancel)
                self.assertEqual(len(page.items), 1)
                from nanexus_trust.historical_v2.placement import admit_single_file, unplaced
                item = admit_single_file(page, (page.items[0].item_id,))
                handle = provider.acquire(item, b, cancel)
                ns = list(read_nals(handle, lambda o,n:provider.read(handle,o,n,cancel),
                           lambda:provider.capabilities(camera), ffprobe=ffprobe, scratch_root=scratch))
                self.assertEqual(len(ns), FIXTURE['sample']['nals'])
                self.assertEqual(len({n.packet for n in ns}), FIXTURE['sample']['packets'])
                expected = json.loads((c1 / 'files-and-join.json').read_text())[0]
                self.assertEqual([(n.payload_offset,n.payload_bytes,n.payload_sha256) for n in ns],
                                 [(n['payload_offset'],n['payload_bytes'],n['payload_sha256']) for n in expected['nal_ledger']])
                for n in ns:
                    p = expected['packets'][n.packet]
                    self.assertEqual((n.pts,n.dts,n.duration), (p['pts'],p['dts'],p['duration']))
                    self.assertEqual(unplaced(n.token)[1].status.value, 'UNPLACED')
                provider.release(handle)
            toolkit = ROOT.parent / 'nanexus-video-trust-toolkit'
            upstream = toolkit / 'third_party/media-signing-framework'
            lib = w / 'liboms.so'
            commands = [
                ['gcc','-shared','-fPIC','-O1','-D_FILE_OFFSET_BITS=64', '-I'+str(upstream/'lib/src'),
                 '-I'+str(upstream/'lib/src/includes'),
                 *map(str, sorted((upstream/'lib/src').glob('*.c'))),
                 str(upstream/'lib/plugins/unthreaded-signing/plugin.c'), '-o',str(lib),'-lcrypto'],
                ['g++','-std=c++20','-O1','-Wall','-Wextra','-Werror', '-I'+str(toolkit/'include'),
                 '-I'+str(upstream/'lib/src/includes'),str(c1/'source_join.cpp'),
                 str(toolkit/'src/verification/historical_observation.cpp'),str(toolkit/'src/upstream/session.cpp'),
                 str(lib),'-Wl,-rpath,'+str(w),'-o',str(w/'source_join')]]
            for command in commands:
                result = subprocess.run(command,capture_output=True,timeout=55)
                self.assertEqual(result.returncode, 0, result.stderr.decode()[-1000:])
            feed = w / 'feed.bin'
            with feed.open('wb') as f:
                for n in ns:
                    token = n.token.encode(); f.write(struct.pack('<II',len(n.annex_b),len(token))+token+n.annex_b)
            result = subprocess.run([str(w/'source_join'),str(feed),'single-file-foundation'],capture_output=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr.decode())
            self.assertLess(len(result.stdout),1024*1024)
            events = [json.loads(line) for line in result.stdout.splitlines()]
            for event in events[:-1]:
                self.assertEqual(event['token'],ns[event['ordinal']].token)
            self.assertEqual({e['ordinal'] for e in events[:-1]}, set(range(len(ns))))
            baseline = [json.loads(s) for s in (c1/'join-0.jsonl').read_text().splitlines()]
            self.assertEqual([{k:v for k,v in e.items() if k!='token'} for e in events],
                             [{k:v for k,v in e.items() if k!='token'} for e in baseline])
            summary = events[-1]
            self.assertEqual((summary['feeds'],summary['fresh'],summary['finalized'],summary['tail']), (385,11,355,30))
            print('HV2-C REAL B JOIN:', json.dumps(summary,sort_keys=True))
        self.assertEqual(hashlib.sha256(sample.read_bytes()).hexdigest(),before)
        self.assertEqual(hashlib.sha256(dbpath.read_bytes()).hexdigest(), FIXTURE['archived_snapshot_sha256'])

class PrototypeReaderLifecycleTests(unittest.TestCase):
    def test_threaded_prlimit_limits_topology_and_reap(self):
        import os, threading
        from nanexus_trust.historical_v2.media import _probe
        with tempfile.TemporaryDirectory() as root:
            script=Path(root)/'probe'
            script.write_text('#!/usr/bin/python3\nimport os,resource,json\nprint(json.dumps(dict(pid=os.getpid(),pgid=os.getpgrp(),memory=resource.getrlimit(resource.RLIMIT_AS),cpu=resource.getrlimit(resource.RLIMIT_CPU))))\n')
            script.chmod(0o700);created=[];results=[];errors=[];real=subprocess.Popen
            def spawn(*args,**kw):
                self.assertNotIn('preexec_fn',kw);p=real(*args,**kw);created.append(p);return p
            def run():
                try:results.append(_probe(script,'unused',ReaderLimits(),lambda:None,time.monotonic()+3))
                except Exception as e:errors.append(e)
            with patch('nanexus_trust.historical_v2.media.subprocess.Popen',side_effect=spawn):
                thread=threading.Thread(target=run);thread.start();thread.join(4)
            self.assertFalse(thread.is_alive());self.assertFalse(errors)
            self.assertEqual(results[0]['pid'],created[0].pid);self.assertEqual(results[0]['pgid'],created[0].pid)
            self.assertEqual(results[0]['memory'],[512<<20,512<<20]);self.assertEqual(results[0]['cpu'],[5,5]);self.assertEqual(created[0].returncode,0)
    def test_probe_cancellation_reaps_owned_group(self):
        from nanexus_trust.historical_v2.media import _probe
        with tempfile.TemporaryDirectory() as root:
            script=Path(root)/'probe';script.write_text('#!/bin/sh\nwhile :; do :; done\n');script.chmod(0o700)
            created=[];real=subprocess.Popen;calls=0
            def spawn(*a,**kw):p=real(*a,**kw);created.append(p);return p
            def check():
                nonlocal calls
                calls+=1
                if calls>3:raise RuntimeError('cancelled')
            with patch('nanexus_trust.historical_v2.media.subprocess.Popen',side_effect=spawn),self.assertRaisesRegex(RuntimeError,'cancelled'):
                _probe(script,'unused',ReaderLimits(),check,time.monotonic()+3)
            self.assertIsNotNone(created[0].returncode)
