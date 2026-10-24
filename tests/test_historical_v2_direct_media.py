"""Direct-source controls. Preserved media runs only by explicit witness selection."""
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import base64
import fcntl
import hashlib
import json
import os
import tempfile
import time
import unittest
from unittest.mock import patch

from nanexus_trust.historical_v2.direct_media import direct_input
from nanexus_trust.historical_v2 import prototype as p
from nanexus_trust.historical_v2.media import ReaderLimits, MediaFailure
from nanexus_trust.frigate.historical_v2 import ProviderFailure, _identity
import test_historical_v2_prototype as old
import test_historical_v2_media as media_tests

ROOT=Path(__file__).resolve().parents[1]
ART=ROOT/'.local/governance/ai-dlc/evidence/m1-hv2-d-planning/hr2-artifacts'

class DirectInputTests(unittest.TestCase):
    def setUp(self):
        self.fixture=media_tests.MediaTests();self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        f=self.fixture
        self.item=SimpleNamespace(camera=f.handle.camera,item_id=f.handle.item_id,generation=f.handle.generation)
        self.provider=SimpleNamespace(media_root=f.root,_items={self.item.item_id:(self.item,Path('synthetic'),None)},
                                      _handles={},_guard=lambda *args:None)
        self.bounds=SimpleNamespace(max_item_bytes=1024,max_context_bytes=0,max_work_units=100)
        self.processes=[]

    def read(self,check=lambda:None,limits=ReaderLimits()):
        return direct_input(self.provider,self.item,self.bounds,None,check,ffprobe='/usr/bin/ffprobe',limits=limits)

    def test_exact_sequence_tokens_timing_and_no_copy(self):
        f=self.fixture
        def probe(executable,path,limits,check,deadline,*,pass_fds):
            fd=pass_fds[0]
            self.assertEqual(fcntl.fcntl(fd,fcntl.F_GETFL)&os.O_ACCMODE,os.O_RDONLY)
            self.assertEqual(path,f'/proc/self/fd/{fd}')
            self.assertEqual(_identity(os.stat(path)),_identity(f.path.stat()))
            return f.probe
        with patch('nanexus_trust.historical_v2.direct_media._probe',side_effect=probe),patch('tempfile.TemporaryDirectory',side_effect=AssertionError('media stage forbidden')):
            with self.read() as (handle,reader):
                actual=list(reader)
                expected_identity=hashlib.sha256((handle.item_id+':'+handle.generation+':'+handle.sha256).encode()).hexdigest()
            self.assertEqual([n.token.split(':',1)[0] for n in actual],[expected_identity]*4)
            expected=f.nals()
            self.assertEqual([(n.annex_b,n.pts,n.dts,n.duration,n.time_base,n.payload_offset) for n in actual],
                             [(n.annex_b,n.pts,n.dts,n.duration,n.time_base,n.payload_offset) for n in expected])
        self.assertEqual(list(f.root.iterdir()),[f.path])

    def test_replacement_mutation_truncation_deletion_and_missing(self):
        f=self.fixture
        for action in ('replace','mutate','truncate','delete','missing','symlink'):
            with self.subTest(action=action):
                if f.path.exists() or f.path.is_symlink():f.path.unlink()
                f.path.write_bytes(f.data)
                if action=='missing':f.path.unlink()
                if action=='symlink':
                    target=f.root/'target';target.write_bytes(f.data);f.path.unlink();f.path.symlink_to(target)
                with patch('nanexus_trust.historical_v2.direct_media._probe',return_value=f.probe),self.assertRaises(ProviderFailure):
                    with self.read() as (_,reader):
                        list(reader)
                        if action=='replace':
                            other=f.root/'replacement';other.write_bytes(f.data);other.replace(f.path)
                        elif action=='mutate':f.path.write_bytes(f.data[:-1]+b'X')
                        elif action=='truncate':f.path.write_bytes(b'x')
                        elif action=='delete':f.path.unlink()

    def test_permission_denied_no_retry(self):
        with patch('nanexus_trust.historical_v2.direct_media._open',side_effect=PermissionError('denied')) as opened,self.assertRaises(ProviderFailure):
            with self.read():self.fail('admitted denied source')
        self.assertEqual(opened.call_count,1)

    def test_mid_read_change_and_authority_or_cancellation(self):
        f=self.fixture
        with patch('nanexus_trust.historical_v2.direct_media._probe',return_value=f.probe),self.assertRaises(ProviderFailure):
            with self.read() as (_,reader):
                next(reader);f.path.write_bytes(f.data+b'x');next(reader)
        f.path.write_bytes(f.data)
        calls=0
        def cancel():
            nonlocal calls
            calls+=1
            if calls==5:raise p.PrototypeFailure('CANCELLED')
        before=set(os.listdir('/proc/self/fd'))
        with patch('nanexus_trust.historical_v2.direct_media._probe',return_value=f.probe),self.assertRaisesRegex(p.PrototypeFailure,'CANCELLED'):
            with self.read(check=cancel) as (_,reader):list(reader)
        self.assertEqual(before,set(os.listdir('/proc/self/fd')))

    def test_probe_failure_output_deadline_cancel_and_cleanup(self):
        import subprocess
        f=self.fixture;script=f.root/'probe-control'
        real=subprocess.Popen
        def spawn(*args,**kwargs):
            process=real(*args,**kwargs);self.processes.append(process);return process
        for body,reason,limits in [('#!/bin/sh\nexit 1\n','probe-failed',ReaderLimits()),
              ('#!/bin/sh\nprintf 01234567890123456789\n','probe-output-cap',ReaderLimits(max_probe_bytes=8)),
              ('#!/bin/sh\nwhile :; do :; done\n','reader-deadline',ReaderLimits(timeout_seconds=.05))]:
            script.write_text(body);script.chmod(0o700)
            before=set(os.listdir('/proc/self/fd'))
            with patch('nanexus_trust.historical_v2.direct_media._probe',wraps=__import__('nanexus_trust.historical_v2.media',fromlist=['_probe'])._probe),patch('nanexus_trust.historical_v2.media.subprocess.Popen',side_effect=spawn),self.assertRaisesRegex(MediaFailure,reason):
                with direct_input(self.provider,self.item,self.bounds,None,lambda:None,ffprobe=script,limits=limits) as (_,reader):list(reader)
            self.assertEqual(before,set(os.listdir('/proc/self/fd')))
            self.assertTrue(all(proc.poll() is not None for proc in self.processes))
        calls=0
        def cancel():
            nonlocal calls
            calls+=1
            if self.processes and self.processes[-1].poll() is None:raise p.PrototypeFailure('CANCELLED')
        before=set(os.listdir('/proc/self/fd'))
        with patch('nanexus_trust.historical_v2.media.subprocess.Popen',side_effect=spawn),self.assertRaisesRegex(p.PrototypeFailure,'CANCELLED'):
            with direct_input(self.provider,self.item,self.bounds,None,cancel,ffprobe=script) as (_,reader):list(reader)
        self.assertTrue(all(proc.poll() is not None for proc in self.processes))
        self.assertEqual(before,set(os.listdir('/proc/self/fd')))

class DirectCoordinatorTests(old.Harness):
    def profile(self):return p.DirectCapturedProfile(**vars(super().profile()))
    def test_source_failure_stops_request_before_next_file(self):
        self.row('r1',10,20);self.row('r2',20,30);self.row('r3',30,40)
        visited=[]
        def verify(provider,item,*args):
            visited.append(item.item_id)
            if item.item_id=='r1':
                args[-1]['status']='RESULT';args[-1]['result']={'prior_independent_finding':True}
            else:raise ProviderFailure('source-changed')
        with patch.object(self.coordinator,'_verify_file',side_effect=verify):
            _,response=self.call(start='11',end='39')
        self.assertEqual(visited,['r1','r2'])
        self.assertEqual(response['status'],'SOURCE_UNAVAILABLE')
        self.assertEqual(len(response['files']),1)
        self.assertTrue(response['files'][0]['result']['prior_independent_finding'])
        self.assertEqual(list(self.scratch.iterdir()),[])
    def test_missing_source_and_probe_failure_never_feed_native(self):
        self.row();(self.media/'r1.mp4').unlink()
        with patch.object(p.Supervisor,'run',side_effect=AssertionError('Native must not run')):
            self.assertEqual(self.call()[1]['status'],'SOURCE_UNAVAILABLE')
            (self.media/'r1.mp4').write_bytes(b'synthetic')
            with patch('nanexus_trust.historical_v2.direct_media._probe',side_effect=MediaFailure('probe-failed')):
                self.assertEqual(self.call()[1]['status'],'UNSUPPORTED')
        self.assertEqual(list(self.scratch.iterdir()),[])
    def test_missing_later_source_fails_before_any_verification(self):
        self.row('r1',10,20);self.row('r2',20,30)
        (self.media/'r2.mp4').unlink()
        with patch.object(self.coordinator,'_verify_file',side_effect=AssertionError('no best-effort continuation')):
            _,response=self.call(start='11',end='29')
        self.assertEqual(response['status'],'SOURCE_UNAVAILABLE')
        self.assertIsNone(response['result'])
        self.assertEqual(list(self.scratch.iterdir()),[])
    def test_post_read_change_prevents_native(self):
        self.row()
        from nanexus_trust.historical_v2 import direct_media
        real=direct_media.direct_input
        @contextmanager
        def changed(*args,**kwargs):
            with real(*args,**kwargs) as value:
                yield value
                (self.media/'r1.mp4').write_bytes(b'changed')
        f=media_tests.MediaTests();f.setUp();self.addCleanup(f.doCleanups)
        (self.media/'r1.mp4').write_bytes(f.data)
        with patch.object(direct_media,'direct_input',changed),patch.object(direct_media,'_probe',return_value=f.probe),patch.object(p.Supervisor,'run',side_effect=AssertionError('Native must not run')):
            self.assertEqual(self.call()[1]['status'],'SOURCE_UNAVAILABLE')

import test_historical_v2_api as api_tests

class DirectApiTests(api_tests.ApiHarness):
    def test_trusted_api_inventory_direct_path_and_failure(self):
        self.coordinator.profile=p.DirectApiProfile(**vars(self.coordinator.profile))
        path=self.row()
        with self.http(),patch.object(p.Supervisor,'run',side_effect=AssertionError('Native must not run')):
            path.unlink()
            _,response=self.call()
        self.assertEqual(response['status'],'SOURCE_UNAVAILABLE')
        self.assertEqual(response['catalog']['mode'],'recordings-api')
        self.assertEqual(response['media_access'],'direct-readonly-original-experimental-v1')
        self.assertEqual(len(self.calls),2)
        self.assertEqual(list(self.scratch.iterdir()),[])

class PreservedWitnessTests(old.Harness):
    def test_direct_saved_native_equivalence(self):
        saved=ROOT/'.local/governance/ai-dlc/evidence/m1-hv2-d-planning/d1.5-artifacts'
        source=json.loads((saved/'source-ledger.json').read_text())['source']
        paths={suffix:Path(next(name for name in source if name.endswith(suffix))) for suffix in ('.mp4','.sqlite','.pem')}
        before={name:(hashlib.sha256(Path(name).read_bytes()).hexdigest(),_identity(Path(name).stat())) for name in source}
        self.assertEqual({name:values[0] for name,values in before.items()},source)
        baseline=json.loads((old.ART/'saved-result.json').read_text())
        self.coordinator.profile=p.DirectCapturedProfile(p.Snapshot(paths['.sqlite'],source[str(paths['.sqlite'])],'slice3-captured',p.PIN_0182,True,True),paths['.mp4'].parents[3],self.scratch,old.WORKER,paths['.pem'],Path('/usr/bin/ffprobe'))
        self.camera='slice3_signed';self.connect()
        # These paths must never run: no Provider copy, Reader copy or media output.
        from nanexus_trust.frigate.historical_v2 import FrigateHistoricalProvider
        import sys
        media_opens=[];audit_active=[True]
        def audit(event,args):
            if event=='open':
                name,mode,flags=args
                if audit_active[0] and isinstance(name,str) and (name.endswith('.mp4') or name.startswith('/proc/self/fd/')):
                    media_opens.append((name,flags))
                    if flags & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND):raise AssertionError('writable media open')
        sys.addaudithook(audit)
        processes=[]
        import subprocess
        spawn=subprocess.Popen
        def popen(*args,**kwargs):
            proc=spawn(*args,**kwargs);processes.append(proc);return proc
        start=time.monotonic()
        with patch.object(FrigateHistoricalProvider,'acquire',side_effect=AssertionError('Provider media staging forbidden')),patch.object(p,'read_nals',side_effect=AssertionError('Reader staging forbidden')),patch('subprocess.Popen',side_effect=popen):
            code,response=self.call(start=baseline['selection']['requested']['start'],end=baseline['selection']['requested']['end'])
        audit_active[0]=False
        duration=time.monotonic()-start
        self.assertEqual(response['status'],'RESULT',response.get('execution'))
        self.assertEqual(list(response['result']['source_ledger']),baseline['result']['source_ledger'])
        self.assertEqual(response['result']['original_file_sha256'],baseline['result']['original_file_sha256'])
        def updates(result):
            return json.loads(base64.b64decode(next(obj['data'] for obj in result['result']['objects'] if obj['id'].startswith('updates-'))))
        def normalized(values):return [{k:v for k,v in u.items() if k not in ('run_id','attempt')} for u in values]
        self.assertEqual(normalized(updates(response)),normalized(updates(baseline)))
        base,objects,us,obs=self.decoded(response)
        self.assertEqual(len(obs),188);self.assertEqual(sum(o.state.value=='VERIFIED_SIGNING' for o in obs),165)
        self.assertEqual(sum(o.state.value=='INDETERMINATE' for o in obs),23)
        self.assertEqual(len(base.unresolved_refs),16)
        self.assertEqual(base.lifecycle.value,'INCOMPLETE');self.assertTrue(all(o.placement.status.value=='UNPLACED' for o in obs))
        self.assertEqual(before,{name:(hashlib.sha256(Path(name).read_bytes()).hexdigest(),_identity(Path(name).stat())) for name in source})
        self.assertEqual(list(self.scratch.iterdir()),[])
        self.assertTrue(all(proc.poll() is not None for proc in processes))
        summary=dict(seconds=duration,nals=len(response['result']['source_ledger']),updates=len(us),finalized=len(obs),verified=165,indeterminate=23,unresolved=16,source_preserved=True,source={name:dict(sha256=values[0],identity=values[1]) for name,values in before.items()},source_ledger_exact=True,native_updates_equal_except_run_attempt=True,processes_reaped=len(processes),media_open_count=len(media_opens),media_write_opens=0,staging_functions_forbidden=True,response_bytes=len(json.dumps(response,separators=(',',':')).encode()))
        (ART/'witness.json').write_text(json.dumps(summary,indent=2))
        print(json.dumps(summary))
