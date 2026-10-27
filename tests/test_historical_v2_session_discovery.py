"""On-demand discovery through the caller's own Frigate session.

Real WindowHTTP, Access, product coordinator, provider, admission, ProductPlan
and Bundle. Frigate's HTTP layer, media parsing and the native transport are
scripted. No listener, no SQLite catalog, no native process.
"""
import json
import ssl
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from dataclasses import replace
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.parse import urlsplit, parse_qs
from unittest.mock import patch
from zoneinfo import ZoneInfo

import test_historical_v2_prototype as old
import test_historical_v2_window_http as http_tests
from test_historical_v2_api import Reply
from test_historical_v2_rolling_product import update, terminal
from nanexus_trust.frigate import historical_v2_api as api
from nanexus_trust.frigate.historical_v2 import ProviderFailure
from nanexus_trust.historical_v2.continuity_protocol import sha
from nanexus_trust.historical_v2.continuity_windows import ProductRollingWindowCoordinator, RollingWindowCoordinator
from nanexus_trust.historical_v2.direct_media import DirectInput
from nanexus_trust.historical_v2.prototype import Coordinator, DirectSessionApiProfile
from nanexus_trust.historical_v2.rolling_product import ProductRolling
from nanexus_trust.historical_v2.windows import WindowCoordinator
from nanexus_trust.companion.window_http import WindowHTTP, Denied, session_recording_dates

T = int(datetime(2026, 10, 26, 10, 0, tzinfo=timezone.utc).timestamp())


class FakeFrigate:
    """Pinned 0.18.2 semantics for the four endpoints H2 reads."""
    def __init__(self, camera):
        self.camera = camera
        self.users = {'alice': ['front'], 'bob': ['front']}
        self.usernames = {}
        self.rows = []
        self.calls = []
        self.urls = []
        self.headers = []
        self.summary = None
        self.recordings_fault = None

    def open(self, request, timeout):
        assert timeout == 2
        parts = urlsplit(request.full_url)
        self.calls.append(parts.path)
        self.urls.append(request.full_url)
        self.headers.append(dict(request.header_items()))
        if request.get_header('Authorization') is not None or request.get_header('Remote-user') is not None:
            raise AssertionError('deployment credential or forwarded identity sent')
        if request.get_header('X-cache-bypass') != '1':
            raise AssertionError('nginx cache bypass missing')
        cookie = request.get_header('Cookie') or ''
        who = cookie.removeprefix('frigate_token=').split('.')[0] if cookie.startswith('frigate_token=') else None
        if who not in self.users:
            raise HTTPError(request.full_url, 401, 'unauthorized', {}, None)
        allowed = self.users[who]
        query = parse_qs(parts.query)
        if parts.path == '/api/profile':
            return Reply(json.dumps({'username': self.usernames.get(who, who), 'role': 'viewer',
                                     'allowed_cameras': allowed}).encode())
        if parts.path == '/api/version':
            return Reply(api.VERSION.encode())
        if parts.path == '/api/recordings/summary':
            if self.summary is not None:
                return Reply(self.summary)
            cameras = [c for c in query['cameras'][0].split(',') if c in allowed]
            zone = ZoneInfo(query['timezone'][0])
            days = {datetime.fromtimestamp(r['start_time'], zone).date().isoformat(): True
                    for r in self.rows if self.camera in cameras}
            return Reply(json.dumps(days).encode())
        if parts.path.startswith('/api/') and parts.path.endswith('/recordings'):
            camera = parts.path.split('/')[2]
            if camera not in allowed:
                raise HTTPError(request.full_url, 403, 'forbidden', {}, None)
            if self.recordings_fault == 'unavailable':
                raise HTTPError(request.full_url, 500, 'error', {}, None)
            if self.recordings_fault == 'malformed':
                return Reply(b'not-json')
            after, before = float(query['after'][0]), float(query['before'][0])
            return Reply(json.dumps([r for r in self.rows if camera == self.camera
                                     and r['end_time'] >= after and r['start_time'] <= before]).encode())
        raise AssertionError('unexpected Frigate path ' + parts.path)


class SessionDiscoveryTests(old.Harness):
    def setUp(self):
        super().setUp(); self.coordinator.close()
        self.frigate = FakeFrigate(self.camera)
        self.session = api.FrigateSession('https://frigate.test', ssl.create_default_context())
        self.worker = self.root / 'synthetic-worker'; self.worker.write_bytes(b'not an executable; native transport scripted')
        self.coordinator = ProductRollingWindowCoordinator(DirectSessionApiProfile(
            self.session, self.media, self.scratch, self.worker, self.ca, old.Path('/usr/bin/ffprobe')))
        self.auth = http_tests.Auth()
        self.access = http_tests.Access(self.auth, lambda: (200, self.service.camera_bindings()))
        self.ops = WindowHTTP(self.access, self.coordinator, self.service._hv2_camera); self.addCleanup(self.ops.close)
        self.entered = threading.Event(); self.resume = threading.Event(); self.addCleanup(self.resume.set)
        # Rolling publishes a file once its successor registers; pausing at the
        # third original leaves exactly one completed and two pending.
        self.pause_at = 2; self.native_calls = 0; self.plans = []
        for target, value in ((api, 'build_opener'),):
            p = patch.object(target, value, return_value=SimpleNamespace(open=self.frigate.open)); p.start(); self.addCleanup(p.stop)
        for p in (patch('nanexus_trust.historical_v2.rolling_product.WORKER_SHA256', sha(self.worker.read_bytes())),
                  patch.object(self.coordinator, '_input', self.source),
                  patch('nanexus_trust.historical_v2.continuity_windows.run_context', self.native),
                  patch('sqlite3.connect', side_effect=AssertionError('H2 must not open a catalog database'))):
            p.start(); self.addCleanup(p.stop)
        for i in range(3):
            self.recording(i)

    def recording(self, i, write=True):
        start = T + i * 10
        rid = f'{start}.0-rec{i:03d}'
        self.frigate.rows.append(dict(id=rid, start_time=start, end_time=start + 10, duration=10, segment_size=0.01,
                                      motion=0, objects=0, motion_heatmap=None))
        self.frigate.rows.sort(key=lambda r: r['start_time'])
        path = self.path(start)
        if write:
            path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(b'synthetic original ' + rid.encode())
        return rid

    def path(self, start):
        date = datetime.fromtimestamp(start, timezone.utc)
        return self.media / date.strftime('%Y-%m-%d/%H') / self.camera / date.strftime('%M.%S.mp4')

    @contextmanager
    def source(self, provider, item, *unused):
        # Models direct_input's first step: the original must still open.
        (provider.media_root / provider._items[item.item_id][1]).open('rb').close()
        data = b'\0\0\0\1\x65signed-synthetic'
        receipt = DirectInput(item.item_id, item.generation, 100, sha(item.item_id.encode()))
        prefix = sha((item.item_id + ':' + item.generation + ':' + receipt.sha256).encode())
        n = SimpleNamespace(token=f'{prefix}:p0:n0:o4:l{len(data)-4}', annex_b=data, packet=0, nal_in_packet=0,
                            length_offset=0, payload_offset=4, payload_bytes=len(data) - 4, payload_sha256=sha(data[4:]),
                            pts=0, dts=0, duration=1, time_base='1/15')
        yield receipt, iter((n,))

    def native(self, executable, plan, request, owner, attempt, trust, load, publish, check, **kwargs):
        self.native_calls += 1; self.plans.append(plan)
        spec = replace(plan.attempt(request, owner, attempt), trust_bytes=len(trust)); r = ProductRolling(plan, request, spec, publish)
        try:
            for i, item in enumerate(plan.originals):
                if i == self.pause_at:
                    self.entered.set()
                    while not self.resume.wait(.005): check()
                    check()
                r.register(item, *load(i, check)); r.accept(update(r, ('P', '.P', '..')[i])); r.commit_source(i)
            terminal(r)
            return SimpleNamespace(state='EXITED_SUCCESSFULLY', error=None, pid=None, reaped=True,
                                   consumed=len(plan.originals), metrics={'active_peak': 2})
        except Exception:
            return SimpleNamespace(state='FAILED', error='synthetic stopped', pid=None, reaped=True,
                                   consumed=r.admission.consumed, metrics={'active_peak': 2})

    def cookie(self, who='alice'): return 'frigate_token=' + who + '.session.signature'
    def data(self, end=29): return dict(camera='front', start=str(T + 1), end=str(T + end))
    def submit(self, end=29, wait=True):
        value, _ = self.ops.submit(self.cookie(), self.data(end))
        if wait: self.assertTrue(self.entered.wait(2))
        return value['request_id']
    def finish(self):
        self.resume.set(); self.ops.slot['thread'].join(3); self.assertFalse(self.ops.slot['running'])
    def recordings_calls(self): return [c for c in self.frigate.calls if c.endswith('/recordings')]
    def days(self, who='alice', camera='front', month='2026-10'):
        return self.ops.catalog_days(self.cookie(who), camera, month, 'UTC')[0]

    def assert_session_coverage(self, value, start=None, end=None):
        coverage = value['query_coverage']
        start = str(T + 1) if start is None else start
        end = str(T + 29) if end is None else end
        self.assertEqual(coverage['schema'], 'nanexus-hv2-query-coverage/1')
        self.assertEqual(coverage['query'], {'start': start, 'end': end})
        self.assertEqual(coverage['dataset'], coverage['query'])
        self.assertEqual(value['coverage'], 'UNKNOWN')
        kinds = [s['kind'] for s in coverage['segments']]
        self.assertTrue(kinds)
        self.assertNotIn('outside', kinds)
        self.assertNotIn('undiscovered', kinds)
        self.assertLessEqual(len(coverage['segments']), 512)
        return coverage

    # 1, 2: camera-selection snapshot is current, scoped and month-filtered.
    def test_new_recording_appears_in_next_selection_without_restart(self):
        first = self.days()
        self.assertEqual(first['dates'], ['2026-10-26']); self.assertEqual(first['camera'], 'front')
        self.assertEqual(first['meaning'], 'catalog_recordings_present')
        self.frigate.rows.append(dict(self.frigate.rows[0], id=f'{T + 86400}.0-rec900', start_time=T + 86400, end_time=T + 86410))
        self.frigate.rows.append(dict(self.frigate.rows[0], id=f'{T + 40 * 86400}.0-rec901', start_time=T + 40 * 86400, end_time=T + 40 * 86400 + 10))
        self.assertEqual(self.days()['dates'], ['2026-10-26', '2026-10-27'])
        self.assertEqual(self.days(month='2026-12')['dates'], ['2026-12-05'])
        summary = [h for c, h in zip(self.frigate.calls, self.frigate.headers) if c == '/api/recordings/summary']
        self.assertEqual(len(summary), 3)
        self.assertEqual(self.frigate.calls.count('/api/profile'), 3)

    def test_summary_response_is_validated_and_scoped_to_the_camera(self):
        self.days()
        query = parse_qs(urlsplit(self.frigate.urls[-1]).query)
        self.assertEqual(query, {'cameras': ['front'], 'timezone': ['UTC']})
        for raw in (b'[]', b'{"2026-10-26": false}', b'{"26-10-2026": true}', b'{"2026-10-26": true, "2026-10-26": true}', b'NaN'):
            self.frigate.summary = raw
            with self.assertRaises(Denied) as caught: self.days()
            self.assertEqual(caught.exception.status, 400)
        self.frigate.summary = b'{' + b' ' * (64 << 10) + b'}'
        with self.assertRaises(Denied) as caught: self.days()
        self.assertEqual(caught.exception.status, 503)

    # 3: Verify and Start/End never touch the date inventory.
    def test_verify_does_not_refresh_date_inventory(self):
        self.days(); before = self.frigate.calls.count('/api/recordings/summary')
        self.submit(); self.finish()
        self.assertEqual(self.frigate.calls.count('/api/recordings/summary'), before)

    # 4, 5, 12: submission-time eligibility, frozen identities and denominator, grid records.
    def test_plan_frozen_at_admission_while_catalog_changes(self):
        rid = self.submit()
        planned, _ = self.ops.observe(self.cookie(), rid)
        self.assertEqual([f['state'] for f in planned['files']], ['COMPLETED', 'PENDING', 'PENDING'])
        self.assertIn(planned['files'][0]['finding'], {'GREEN', 'AMBER', 'RED'})
        self.assertEqual([f['finding'] for f in planned['files'][1:]], [None, None])
        frozen = [(f['recording'], f['position'], f['metadata_interval']) for f in planned['files']]
        generations = dict(self.ops.slot['generations']); count = self.coordinator._record['planned_count']
        self.recording(3)
        self.frigate.rows = [r for r in self.frigate.rows if not r['id'].endswith('rec002')]
        self.finish()
        done, _ = self.ops.observe(self.cookie(), rid, True)
        self.assertEqual(done['state'], 'COMPLETE')
        self.assertEqual([(f['recording'], f['position'], f['metadata_interval']) for f in done['files']], frozen)
        self.assertEqual([f['state'] for f in done['files']], ['COMPLETED'] * 3)
        self.assertEqual(self.ops.slot['generations'], generations); self.assertEqual(self.coordinator._record['planned_count'], count)
        self.assertEqual(len(self.recordings_calls()), 1); self.assertEqual(self.native_calls, 1)
        self.assertEqual([i.item_id for i in self.plans[0].originals], [r for r, _, _ in frozen])
        self.ops.release(self.cookie(), rid)

        self.entered.clear(); self.resume.clear(); self.pause_at = 99
        second = self.submit(end=39, wait=False); self.ops.slot['thread'].join(3)
        value, _ = self.ops.observe(self.cookie(), second, True)
        self.assertEqual([f['recording'] for f in value['files']], [f'{T}.0-rec000', f'{T + 10}.0-rec001', f'{T + 30}.0-rec003'])
        self.assertEqual(len(self.recordings_calls()), 2)
        snapshots = [json.loads(p.qualified_order)['snapshot'] for p in self.plans]
        self.assertNotEqual(snapshots[0]['sha256'], snapshots[1]['sha256'])
        self.assertTrue(all(s['cut_id'] == 'api-' + s['sha256'] for s in snapshots))

    # 6: absent at admission keeps its slot; vanishing later is never substituted.
    def test_missing_at_admission_keeps_denominator_without_finding(self):
        self.path(T).unlink(); self.pause_at = 99
        rid = self.submit(wait=False); self.ops.slot['thread'].join(3)
        value, _ = self.ops.observe(self.cookie(), rid, True)
        self.assertEqual(value['state'], 'SOURCE_UNAVAILABLE')
        self.assertEqual([f['state'] for f in value['files']], ['PENDING', 'COMPLETED', 'COMPLETED'])
        self.assertIsNone(value['files'][0]['finding']); self.assertIn('no signing finding', value['reason'])

    def test_file_removed_after_admission_stops_without_substitute(self):
        rid = self.submit()
        self.path(T + 20).unlink(); self.recording(3)
        self.finish()
        value, _ = self.ops.observe(self.cookie(), rid, True)
        self.assertEqual(value['state'], 'EXECUTION_FAILED')
        self.assertEqual(len(value['files']), 3)
        self.assertEqual([f['state'] for f in value['files']], ['COMPLETED', 'PENDING', 'PENDING'])
        self.assertEqual(len(self.recordings_calls()), 1)

    # 7, 8, 9: server-side authorization, fail-closed, no selector bypass.
    def test_frigate_revocation_or_empty_list_rejects_discovery_and_verify(self):
        self.frigate.users['alice'] = []
        with self.assertRaises(Denied) as caught: self.days()
        self.assertEqual(caught.exception.status, 403)
        rid = self.submit(wait=False); self.ops.slot['thread'].join(3)
        value, _ = self.ops.observe(self.cookie(), rid, True)
        self.assertEqual(value['state'], 'UNAUTHORIZED'); self.assertEqual(value['files'], [])
        self.assertEqual(self.recordings_calls(), []); self.assertNotIn('/api/recordings/summary', self.frigate.calls)
        self.assertEqual(self.native_calls, 0)

    def test_frigate_401_and_principal_mismatch_reject(self):
        del self.frigate.users['alice']
        with self.assertRaises(Denied) as caught: self.days()
        self.assertEqual(caught.exception.status, 403)
        self.frigate.users['alice'] = ['front']; self.frigate.usernames['alice'] = 'bob'
        with self.assertRaises(Denied) as caught: self.days()
        self.assertEqual(caught.exception.status, 403)
        self.frigate.usernames['alice'] = 'anonymous'; self.auth.valid.add('anonymous'); self.auth.allowed['anonymous'] = ['front']
        with self.assertRaises(ProviderFailure):
            self.session.authorize('frigate_token=anonymous.x.y', 'anonymous', 'front', 1e18)
        self.assertNotIn('/api/recordings/summary', self.frigate.calls)

    def test_bridge_authority_rejects_before_any_frigate_call(self):
        self.auth.allowed['alice'] = []
        with self.assertRaises(Exception): self.days()
        with self.assertRaises(Exception): self.ops.submit(self.cookie(), self.data())
        for camera in ('back', 'front/../back', ''):
            with self.assertRaises(Exception): self.ops.catalog_days(self.cookie('bob'), camera, '2026-10', 'UTC')
            with self.assertRaises(Exception): self.ops.submit(self.cookie('bob'), dict(self.data(), camera=camera))
        self.assertEqual(self.frigate.calls, [])
        self.assertIsNone(self.ops.slot)

    def test_only_the_callers_session_cookie_is_forwarded(self):
        value, _ = self.ops.catalog_days('theme=dark; frigate_token=alice.session.signature; other=1', 'front', '2026-10', 'UTC')
        self.assertEqual(value['dates'], ['2026-10-26'])
        for headers in self.frigate.headers:
            self.assertEqual(headers['Cookie'], 'frigate_token=alice.session.signature')
            self.assertNotIn('Authorization', headers)
        for bad in ('frigate_token=a.b.c; frigate_token=d.e.f', 'theme=dark', 'frigate_token=', 'frigate_token=a b'):
            with self.assertRaises(ProviderFailure): api.caller_cookie(bad)

    def test_owner_bound_to_job_across_endpoints(self):
        rid = self.submit()
        for method in (self.ops.observe, self.ops.cancel, self.ops.release):
            with self.assertRaises(Denied): method(self.cookie('bob'), rid)
        self.finish(); self.ops.release(self.cookie(), rid)

    # 11: H1 cancel, grace and backend release still hold.
    def test_cancel_and_backend_release_unchanged(self):
        rid = self.submit()
        self.ops.cancel(self.cookie(), rid); self.finish()
        value, _ = self.ops.observe(self.cookie(), rid, True)
        self.assertEqual(value['state'], 'CANCELLED')
        self.assertEqual([f['state'] for f in value['files']], ['COMPLETED', 'PENDING', 'PENDING'])
        coverage = self.assert_session_coverage(value)
        self.assertEqual([s['kind'] for s in coverage['segments']], ['recording'] * 3)
        self.assertIn(coverage['segments'][0]['finding'], {'GREEN', 'AMBER', 'RED'})
        self.assertEqual([s['finding'] for s in coverage['segments'][1:]], ['PENDING', 'PENDING'])
        with patch('nanexus_trust.companion.window_http.CLEANUP_GRACE', 0):
            self.ops._arm_cleanup(self.ops.slot)
        for _ in range(200):
            if self.ops.slot is None: break
            time.sleep(.01)
        self.assertIsNone(self.ops.slot); self.assertIsNone(self.coordinator._record)
        self.submit(); self.finish()

    # Profile is product-window only; captured fixtures and other profiles keep their gates.
    def test_session_profile_is_window_product_only(self):
        profile = self.coordinator.profile
        with self.assertRaises(TypeError): Coordinator(profile)
        with self.assertRaises(TypeError): RollingWindowCoordinator(profile, lambda *a: None)
        WindowCoordinator(profile).close()
        with self.assertRaises(ValueError):
            DirectSessionApiProfile('https://frigate.test', self.media, self.scratch, self.worker, self.ca, old.Path('/usr/bin/ffprobe'))
        with self.assertRaises(ValueError): api.FrigateSession('https://frigate.test')
        with self.assertRaises(ValueError): api.FrigateSession('https://user:pw@frigate.test', ssl.create_default_context())

    def test_session_dates_reject_bad_inputs_before_any_call(self):
        for args in (('front', 'front', '2026-13', 'UTC'), ('front', 'front', '2026-10', '../etc'),
                     ('front', 'front/x', '2026-10', 'UTC')):
            with self.assertRaises(ValueError):
                session_recording_dates(self.session, self.cookie(), 'alice', *args)
        self.assertEqual(self.frigate.calls, [])

    def test_authorized_empty_discovery_reports_no_retained_recordings(self):
        self.frigate.rows = []
        rid = self.submit(wait=False); self.ops.slot['thread'].join(3)
        value, _ = self.ops.observe(self.cookie(), rid, True)
        self.assertEqual(value['state'], 'NO_MATCHING_RECORDING')
        self.assertEqual(value['reason'], 'No retained recordings in the selected time range.')
        self.assertEqual(value['files'], [])
        self.assertEqual(value['coverage'], 'UNKNOWN')
        self.assertEqual(value['continuity'], 'UNKNOWN')
        self.assertEqual(value['origin'], 'HOLD')
        self.assertNotIn('query_coverage', value)
        self.assertEqual(value['catalog_gaps_state'], 'unavailable')
        self.assertTrue(value['catalog_gaps'] in (None, []))
        self.assertEqual(self.native_calls, 0)
        self.assertEqual(self.plans, [])
        self.assertEqual(len(self.recordings_calls()), 1)
        self.assertIn('/api/profile', self.frigate.calls)
        self.assertNotIn('/api/recordings/summary', self.frigate.calls)
        with patch('nanexus_trust.companion.window_http.CLEANUP_GRACE', 0):
            self.ops._arm_cleanup(self.ops.slot)
        for _ in range(200):
            if self.ops.slot is None: break
            time.sleep(.01)
        self.assertIsNone(self.ops.slot)
        self.recording(0); self.entered.clear(); self.resume.clear(); self.pause_at = 99
        self.submit(wait=False); self.ops.slot['thread'].join(3)
        later, _ = self.ops.observe(self.cookie(), self.ops.slot['id'], True)
        self.assertEqual(later['state'], 'COMPLETE')
        self.assertEqual(len(later['files']), 1)
        self.assertEqual(self.native_calls, 1)

    def test_unauthorized_and_failed_discovery_are_not_empty_windows(self):
        self.frigate.users['alice'] = []
        rid = self.submit(wait=False); self.ops.slot['thread'].join(3)
        denied, _ = self.ops.observe(self.cookie(), rid, True)
        self.assertEqual(denied['state'], 'UNAUTHORIZED')
        self.assertNotEqual(denied['reason'], 'No retained recordings in the selected time range.')
        self.assertEqual(denied['files'], [])
        self.assertEqual(self.native_calls, 0)
        self.assertEqual(self.recordings_calls(), [])
        self.ops.release(self.cookie(), rid)

        self.frigate.users['alice'] = ['front']
        self.frigate.recordings_fault = 'unavailable'
        rid = self.submit(wait=False); self.ops.slot['thread'].join(3)
        failed, _ = self.ops.observe(self.cookie(), rid, True)
        self.assertEqual(failed['state'], 'API_UNAVAILABLE')
        self.assertNotEqual(failed['reason'], 'No retained recordings in the selected time range.')
        self.assertEqual(self.native_calls, 0)
        self.ops.release(self.cookie(), rid)

        self.frigate.recordings_fault = 'malformed'
        rid = self.submit(wait=False); self.ops.slot['thread'].join(3)
        malformed, _ = self.ops.observe(self.cookie(), rid, True)
        self.assertEqual(malformed['state'], 'API_MALFORMED')
        self.assertNotEqual(malformed['reason'], 'No retained recordings in the selected time range.')
        self.assertEqual(self.native_calls, 0)
        self.assertNotIn('query_coverage', malformed)

    def test_nonempty_session_observe_emits_accepted_query_coverage(self):
        rid = self.submit()
        pending, _ = self.ops.observe(self.cookie(), rid)
        coverage = self.assert_session_coverage(pending)
        self.assertEqual([s['kind'] for s in coverage['segments']], ['recording'] * 3)
        self.assertEqual([(s['start'], s['end'], s['recording']) for s in coverage['segments']], [
            (str(T + 1), str(T + 10), f'{T}.0-rec000'),
            (str(T + 10), str(T + 20), f'{T + 10}.0-rec001'),
            (str(T + 20), str(T + 29), f'{T + 20}.0-rec002'),
        ])
        self.assertIn(coverage['segments'][0]['finding'], {'GREEN', 'AMBER', 'RED'})
        self.assertEqual([s['finding'] for s in coverage['segments'][1:]], ['PENDING', 'PENDING'])
        self.assertEqual([f['state'] for f in pending['files']], ['COMPLETED', 'PENDING', 'PENDING'])
        self.assertEqual(pending['files'][0]['finding'], coverage['segments'][0]['finding'])
        self.finish()
        done, _ = self.ops.observe(self.cookie(), rid, True)
        finalized = self.assert_session_coverage(done)
        self.assertEqual([s['kind'] for s in finalized['segments']], ['recording'] * 3)
        self.assertEqual([s['recording'] for s in finalized['segments']], [s['recording'] for s in coverage['segments']])
        self.assertTrue(all(s['finding'] in {'GREEN', 'AMBER', 'RED'} for s in finalized['segments']))
        self.assertEqual([f['state'] for f in done['files']], ['COMPLETED'] * 3)
        self.assertEqual([s['finding'] for s in finalized['segments']], [f['finding'] for f in done['files']])
        self.ops.release(self.cookie(), rid)

    def test_session_coverage_clips_and_keeps_interior_catalog_gaps(self):
        self.frigate.rows = [r for r in self.frigate.rows if not r['id'].endswith('rec001')]
        self.pause_at = 99
        rid = self.submit(wait=False); self.ops.slot['thread'].join(3)
        value, _ = self.ops.observe(self.cookie(), rid, True)
        coverage = self.assert_session_coverage(value)
        self.assertEqual([s['kind'] for s in coverage['segments']], ['recording', 'gap', 'recording'])
        self.assertEqual(coverage['segments'][0]['recording'], f'{T}.0-rec000')
        self.assertEqual(coverage['segments'][1], {'kind': 'gap', 'start': str(T + 10), 'end': str(T + 20)})
        self.assertEqual(coverage['segments'][2]['recording'], f'{T + 20}.0-rec002')
        self.assertEqual(coverage['segments'][0]['start'], str(T + 1))
        self.assertEqual(coverage['segments'][2]['end'], str(T + 29))
        self.assertEqual(value['coverage'], 'UNKNOWN')
        self.assertNotEqual(value['state'], 'INVALID')
        self.ops.release(self.cookie(), rid)

    def test_session_coverage_uses_frozen_plan_after_catalog_mutation(self):
        rid = self.submit()
        pending, _ = self.ops.observe(self.cookie(), rid)
        frozen = self.assert_session_coverage(pending)
        identities = [(s.get('recording'), s['start'], s['end'], s['kind']) for s in frozen['segments']]
        self.recording(3)
        self.frigate.rows = [r for r in self.frigate.rows if not r['id'].endswith('rec002')]
        self.path(T + 20).unlink()
        self.finish()
        done, _ = self.ops.observe(self.cookie(), rid, True)
        coverage = self.assert_session_coverage(done)
        self.assertEqual([(s.get('recording'), s['start'], s['end'], s['kind']) for s in coverage['segments']], identities)
        self.assertEqual(len(done['files']), 3)
        self.assertEqual([f['recording'] for f in done['files']],
                         [f'{T}.0-rec000', f'{T + 10}.0-rec001', f'{T + 20}.0-rec002'])
        self.assertNotIn(f'{T + 30}.0-rec003', [f['recording'] for f in done['files']])
        self.assertEqual(len(self.recordings_calls()), 1)

    def test_unauthorized_empty_files_do_not_emit_query_coverage(self):
        self.frigate.users['alice'] = []
        rid = self.submit(wait=False); self.ops.slot['thread'].join(3)
        value, _ = self.ops.observe(self.cookie(), rid, True)
        self.assertEqual(value['state'], 'UNAUTHORIZED')
        self.assertEqual(value['files'], [])
        self.assertNotIn('query_coverage', value)
        self.assertEqual(self.native_calls, 0)


if __name__ == '__main__':
    import unittest; unittest.main()
