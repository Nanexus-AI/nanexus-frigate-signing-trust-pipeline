"""Focused checks for the Frigate 0.18.2 Live and Historical host entry."""
import hashlib
import http.client
import json
import os
from pathlib import Path
import signal
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from nanexus_trust.frigate import navigation_overlay
from nanexus_trust.frigate.host_entry import (
    ACCEPTED_WORKER, HISTORICAL_PORT, LIVE_PORT, HostEntryError, accepted_listeners,
    load_config, start, validate_config,
)
from nanexus_trust.frigate.session_server import OperationsHandler, Server
from nanexus_trust.historical_v2.rolling_product import WORKER_SHA256

ACCEPTED = 'cfe8d86efebd749e82e308d86f88316795fcc6828bb205da3d8f2d151e8d4d22'
PRODUCT = 'd12125f5923f82e6b17d923791b0069ff30820c1381fd405b47f78c0f18e3f47'
WORKER = ROOT / '.local/runtime/hv2-cont33r2/snapshot/launcher/worker_child'
PRODUCT_WORKER = ROOT / '.local/runtime/hv2-product-worker-isolated/worker_child'
OVERLAYS = (
    'src/nanexus_trust/frigate/navigation_overlay.py',
    'src/nanexus_trust/frigate/navigation_locations.nginx',
    'src/nanexus_trust/frigate/api_overlay.py',
    'src/nanexus_trust/frigate/api_nanexus.py',
    'src/nanexus_trust/frigate/live_frontend_overlay.py',
    'src/nanexus_trust/launcher.py',
    'src/nanexus_trust/historical_v2/rolling_product.py',
)
FORBIDDEN = ('/home/', '.local', '192.168.', 'cam1', 'signed_cam', 'c210-gateway', PRODUCT)


def _baseline():
    docker = subprocess.check_output(
        ['docker', 'inspect', '--format', '{{.State.StartedAt}} {{.State.Pid}}', 'nanexus-3cam-e2e'],
        text=True).strip()
    processes = {}
    for line in subprocess.check_output(['ps', '-eo', 'pid,args'], text=True).splitlines():
        if 'three-cam-e2e/companion.py' in line or 'h2-staged/historical-bridge.py' in line:
            pid = int(line.split(None, 1)[0])
            processes[pid] = Path(f'/proc/{pid}/cmdline').read_bytes()
    return docker, processes


BASELINE = _baseline()


def _free_port():
    reserved = {LIVE_PORT, HISTORICAL_PORT, 8971, 8973, 8582, 8766, 18774}
    while True:
        sock = socket.socket()
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
        sock.close()
        if port not in reserved:
            return port


def _private(path, text):
    path.write_text(text)
    path.chmod(0o600)
    return path


class _Profile(BaseHTTPRequestHandler):
    def log_message(self, *args):
        return

    def do_GET(self):
        if self.path != '/api/profile':
            self.send_error(404)
            return
        body = b'{"username":"operator","role":"admin","allowed_cameras":["gate"]}'
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _https_profile(cert, key):
    server = ThreadingHTTPServer(('127.0.0.1', 0), _Profile)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


class HostEntryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        root = Path(cls.temp.name)
        os.chmod(root, 0o700)
        subprocess.run([
            'openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-keyout', str(root / 'key.pem'),
            '-out', str(root / 'cert.pem'), '-days', '1', '-nodes', '-subj', '/CN=127.0.0.1',
        ], check=True, capture_output=True)
        reader = root / 'reader.py'
        reader.write_text('import time\ntime.sleep(60)\n')
        reader.chmod(0o700)
        cls.root = root
        cls.cert = root / 'cert.pem'
        cls.key = root / 'key.pem'
        cls.reader = reader

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()
        later = _baseline()
        if later != BASELINE:
            raise AssertionError('accepted three-camera runtime changed during host-entry tests')

    def _tree(self):
        directory = Path(tempfile.mkdtemp(dir=self.root))
        os.chmod(directory, 0o700)
        for name in ('media', 'scratch', 'state'):
            path = directory / name
            path.mkdir()
            path.chmod(0o700)
        token = _private(directory / 'token', 'a' * 48)
        return directory, token

    def _config(self, directory, token, **replace):
        live = _free_port()
        historical = _free_port()
        document = {
            'schema': 1,
            'frigate_version': '0.18.2-cfa08c5',
            'api_origin': 'https://127.0.0.1:9',
            'browser_origin': 'https://browser.test:8443',
            'instance_certificate': str(self.cert),
            'listen': {
                'live_host': '127.0.0.1', 'live_port': live,
                'historical_host': '127.0.0.1', 'historical_port': historical,
            },
            'token_file': str(token),
            'ffmpeg': str(self.reader),
            'ffprobe': '/bin/true',
            'policy': {
                'policy_id': 'test-policy', 'evaluation_interval': 0.5,
                'active_freshness_limit': 2, 'observation_horizon': 6, 'media_silence_limit': 1.5,
            },
            'supervision': {
                'poll_seconds': 0.2, 'backoff_initial_seconds': 0.5, 'backoff_max_seconds': 5,
            },
            'cameras': [{
                'camera': 'gate', 'stream': 'gate-main', 'context': 'grid', 'rendition': 'main',
                'source_id': 'source-gate', 'revision': 'rev-1', 'rtsp_url': 'rtsp://127.0.0.1:8554/gate',
            }],
            'historical': {
                'media_root': str(directory / 'media'),
                'scratch_root': str(directory / 'scratch'),
                'public_ca': str(self.cert),
                'worker': str(WORKER),
                'worker_sha256': ACCEPTED,
            },
            'state_root': str(directory / 'state'),
        }
        for key, value in replace.items():
            if isinstance(value, dict) and isinstance(document.get(key), dict):
                document[key].update(value)
            else:
                document[key] = value
        return document

    def _write(self, directory, document):
        path = _private(directory / 'host.json', json.dumps(document))
        return path

    def test_constants_match_the_accepted_worker_and_ports(self):
        self.assertEqual(ACCEPTED_WORKER, WORKER_SHA256)
        self.assertEqual(WORKER_SHA256, ACCEPTED)
        self.assertEqual(accepted_listeners(), {
            'live': ('127.0.0.1', 18774), 'historical': ('127.0.0.1', 8766)})
        self.assertEqual(hashlib.sha256(WORKER.read_bytes()).hexdigest(), ACCEPTED)

    def test_version_camera_token_and_listener_rejection(self):
        directory, token = self._tree()
        wrong = self._config(directory, token, frigate_version='0.18.0-77a66e7')
        with self.assertRaises(HostEntryError) as raised:
            validate_config(wrong)
        self.assertEqual(raised.exception.code, 'frigate-version')
        duplicate = self._config(directory, token)
        duplicate['cameras'].append(dict(duplicate['cameras'][0], stream='other', rtsp_url='rtsp://127.0.0.1:8554/other'))
        with self.assertRaises(HostEntryError) as raised:
            validate_config(duplicate)
        self.assertEqual(raised.exception.code, 'camera-binding')
        conflict = self._config(directory, token)
        conflict['cameras'].append(dict(conflict['cameras'][0], context='single', source_id='source-other'))
        with self.assertRaises(HostEntryError) as raised:
            validate_config(conflict)
        self.assertEqual(raised.exception.code, 'camera-binding')
        missing = self._config(directory, token, token_file=str(directory / 'absent'))
        with self.assertRaises(HostEntryError) as raised:
            validate_config(missing)
        self.assertEqual(raised.exception.code, 'token-file')
        token.chmod(0o644)
        try:
            with self.assertRaises(HostEntryError) as raised:
                validate_config(self._config(directory, token))
            self.assertEqual(raised.exception.code, 'token-file')
        finally:
            token.chmod(0o600)
        public = self._config(directory, token, listen={
            'live_host': '127.0.0.1', 'live_port': _free_port(),
            'historical_host': '0.0.0.0', 'historical_port': _free_port()})
        with self.assertRaises(HostEntryError) as raised:
            validate_config(public)
        self.assertEqual(raised.exception.code, 'historical-listener')
        self.assertNotIn(str(directory), str(raised.exception))
        with self.assertRaises(ValueError):
            Server(('10.1.1.1', 8766), OperationsHandler)

    def test_wrong_worker_is_rejected_before_execution(self):
        directory, token = self._tree()
        calls = []
        real = subprocess.Popen

        def capture(*args, **kwargs):
            calls.append(args[0] if args else kwargs.get('args'))
            return real(*args, **kwargs)

        decoy = directory / 'worker_child'
        decoy.write_bytes(b'not-the-accepted-worker')
        with patch('subprocess.Popen', capture):
            with self.assertRaises(HostEntryError) as raised:
                validate_config(self._config(directory, token, historical={
                    'worker': str(decoy), 'worker_sha256': ACCEPTED}))
            self.assertEqual(raised.exception.code, 'worker-identity')
            with self.assertRaises(HostEntryError) as raised:
                validate_config(self._config(directory, token, historical={
                    'worker': str(PRODUCT_WORKER), 'worker_sha256': PRODUCT}))
            self.assertEqual(raised.exception.code, 'worker-identity')
        self.assertEqual(calls, [])
        self.assertNotIn(PRODUCT, str(raised.exception))
        self.assertNotIn(str(decoy), str(raised.exception))

    def test_session_authorization_and_route_composition(self):
        directory, token = self._tree()
        profile, thread = _https_profile(self.cert, self.key)
        self.addCleanup(thread.join, 2)
        self.addCleanup(profile.server_close)
        self.addCleanup(profile.shutdown)
        document = self._config(directory, token, api_origin=f'https://127.0.0.1:{profile.server_address[1]}')
        document['cameras'].append({
            'camera': 'yard', 'stream': 'yard-main', 'context': 'grid', 'rendition': 'main',
            'source_id': 'source-yard', 'revision': 'rev-1', 'rtsp_url': 'rtsp://127.0.0.1:8554/yard',
        })
        worker_calls = []
        real = subprocess.Popen

        def capture(*args, **kwargs):
            argv = args[0] if args else kwargs.get('args')
            if argv and str(argv[0]) == str(WORKER):
                worker_calls.append(argv)
            return real(*args, **kwargs)

        with patch('nanexus_trust.frigate.host_entry.subprocess.Popen', capture):
            running = start(validate_config(document))
        try:
            self._exercise_routes(document, running, token, worker_calls)
        finally:
            self.assertFalse(running.stop())

    def _exercise_routes(self, document, running, token, worker_calls):
        host, historical_port = document['listen']['historical_host'], document['listen']['historical_port']
        # validate_config rewrites ports into the normalized object; use the started listeners.
        historical_port = running.historical_server.server_address[1]
        live_port = running.live_server.server_address[1]
        headers = {'Host': 'browser.test:8443', 'Origin': 'https://browser.test:8443',
                   'Cookie': 'frigate_token=caller-session-token'}
        session = http.client.HTTPConnection(host, historical_port, timeout=3)
        session.request('GET', '/session', headers=headers)
        response = session.getresponse()
        body = json.loads(response.read())
        self.assertEqual(response.status, 200)
        self.assertEqual([row['camera_id'] for row in body['cameras']], ['gate'])
        self.assertNotIn('yard', json.dumps(body))
        session.close()
        denied = http.client.HTTPConnection(host, historical_port, timeout=3)
        denied.request('GET', '/session?' + urlencode({'camera': 'yard'}), headers=headers)
        self.assertEqual(denied.getresponse().status, 403)
        denied.close()
        shortcut = http.client.HTTPConnection(host, historical_port, timeout=3)
        shortcut.request('GET', '/session', headers=headers | {'Authorization': 'Bearer shortcut', 'Remote-User': 'operator'})
        self.assertEqual(shortcut.getresponse().status, 403)
        shortcut.close()
        window = http.client.HTTPConnection(host, historical_port, timeout=3)
        window.request('GET', '/window/status', headers=headers)
        self.assertEqual(window.getresponse().status, 400)
        window.close()
        blocked = http.client.HTTPConnection(host, historical_port, timeout=3)
        payload = json.dumps({'camera': 'yard', 'start': '10', 'end': '20'}).encode()
        blocked.request('POST', '/window/verify', body=payload, headers=headers | {
            'Content-Type': 'application/json', 'Content-Length': str(len(payload)),
            'X-Nanexus-Request': '1'})
        self.assertEqual(blocked.getresponse().status, 403)
        blocked.close()
        live = http.client.HTTPConnection('127.0.0.1', live_port, timeout=3)
        live.request('GET', '/v1/live/status?' + urlencode({
            'camera': 'gate', 'stream': 'gate-main', 'context': 'grid'}))
        self.assertEqual(live.getresponse().status, 401)
        live.close()
        authed = http.client.HTTPConnection('127.0.0.1', live_port, timeout=3)
        authed.request('GET', '/v1/live/status?' + urlencode({
            'camera': 'gate', 'stream': 'gate-main', 'context': 'grid'}),
            headers={'Authorization': 'Bearer ' + token.read_text()})
        opened = authed.getresponse()
        status = json.loads(opened.read())
        self.assertEqual(opened.status, 200)
        self.assertIn(status['status'], ('ACTIVE', 'DEGRADED', 'UNKNOWN'))
        self.assertEqual(status['logical_camera'], 'gate')
        authed.close()
        self.assertEqual(worker_calls, [])
        nginx = (ROOT / 'src/nanexus_trust/frigate/navigation_locations.nginx').read_text()
        self.assertIn('proxy_pass http://127.0.0.1:8766/session;', nginx)
        for name in ('verify', 'cancel', 'status', 'progress', 'result', 'release'):
            self.assertIn(f'proxy_pass http://127.0.0.1:8766/window/{name};', nginx)

    def test_signal_shutdown_reaps_readers_and_coordinator(self):
        for sig in (signal.SIGTERM, signal.SIGINT):
            directory, token = self._tree()
            path = self._write(directory, self._config(directory, token))
            env = os.environ.copy()
            env['PYTHONPATH'] = str(ROOT / 'src')
            env['PYTHONDONTWRITEBYTECODE'] = '1'
            proc = subprocess.Popen(
                [sys.executable, '-m', 'nanexus_trust.frigate.host_entry', '--config', str(path)],
                cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            box = []
            reader = threading.Thread(target=lambda: box.append(proc.stdout.readline()))
            reader.start()
            reader.join(15)
            if reader.is_alive() or not box or 'host-entry-ready' not in box[0]:
                proc.kill()
                proc.wait(timeout=5)
                self.fail(proc.stderr.read() or 'startup timed out')
            self.assertIn('not a signing verdict', box[0])
            os.kill(proc.pid, sig)
            code = proc.wait(timeout=15)
            stderr = proc.stderr.read()
            self.assertEqual(code, 0, stderr)
            self.assertEqual(stderr, '')
            self.assertNotIn(token.read_text(), box[0])
            proc.stdout.close()
            proc.stderr.close()
            listing = subprocess.check_output(['ps', '-eo', 'args'], text=True)
            self.assertNotIn(str(self.reader), listing)

    def test_failed_historical_bind_stops_live(self):
        directory, token = self._tree()
        document = self._config(directory, token)
        held = socket.socket()
        held.bind(('127.0.0.1', document['listen']['historical_port']))
        held.listen(1)
        live_port = document['listen']['live_port']
        try:
            with self.assertRaises(OSError):
                start(document)
        finally:
            held.close()
        probe = socket.socket()
        probe.settimeout(0.2)
        self.assertNotEqual(probe.connect_ex(('127.0.0.1', live_port)), 0)
        probe.close()

    def test_product_source_and_overlays_stay_unscoped(self):
        for relative in (
                'src/nanexus_trust/frigate/host_entry.py',
                'src/nanexus_trust/frigate/session_server.py'):
            text = (ROOT / relative).read_text()
            for needle in FORBIDDEN:
                self.assertNotIn(needle, text)
        diff = subprocess.run(['git', 'diff', '--exit-code', '--', *OVERLAYS], cwd=ROOT, capture_output=True)
        self.assertEqual(diff.returncode, 0)
        for name, digest in navigation_overlay.UI_SHA256.items():
            self.assertEqual(hashlib.sha256((ROOT / 'ui/nanexus' / name).read_bytes()).hexdigest(), digest)
        head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
        self.assertEqual(head, 'b402ea4bb1176c1fa32c64b0f33c454c2d8777cb')

    def test_secret_values_are_absent_from_startup_errors(self):
        directory, token = self._tree()
        secret = 'secret-token-value-should-not-leak-000000000000'
        token.write_text(secret)
        token.chmod(0o600)
        sentinel = directory / 'media' / 'secret-media-sentinel'
        sentinel.mkdir()
        document = self._config(directory, token, historical={
            'media_root': str(sentinel), 'worker_sha256': '0' * 64})
        with self.assertRaises(HostEntryError) as raised:
            validate_config(document)
        self.assertEqual(str(raised.exception), 'worker-identity')
        self.assertNotIn(secret, str(raised.exception))
        self.assertNotIn('secret-media-sentinel', str(raised.exception))
        path = self._write(directory, document)
        path.chmod(0o644)
        with self.assertRaises(HostEntryError) as raised:
            load_config(path)
        self.assertEqual(raised.exception.code, 'configuration')
        self.assertNotIn(secret, str(raised.exception))


if __name__ == '__main__':
    unittest.main()
