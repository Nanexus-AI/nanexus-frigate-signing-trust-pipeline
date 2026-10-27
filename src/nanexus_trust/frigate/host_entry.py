"""Frigate 0.18.2 host entry for the accepted Live and Historical listeners.

One process supervises both loopback listeners. Live keeps its bearer token.
Historical keeps the caller's frigate_token. This is not an installer, and
listener readiness is not a signing verdict.

The worker path is rechecked before execution. A pathname can still change
between that check and exec; this entry does not add a larger process-security
subsystem. Both listeners must share Frigate's loopback, which the accepted
deployment provides with host networking.
"""
import argparse
import hashlib
import http.client
import json
import os
from pathlib import Path
import signal
import ssl
import stat
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit

from ..companion.jobs import JobStore
from ..companion.live import FDObservationSource, LiveScheduler, PlaybackMapping
from ..companion.service import CompanionService
from ..companion.transport import LiveHTTPServer, LiveTransport, token_from_file
from ..companion.window_http import install
from ..contracts import CameraId, RenditionId, identifier
from ..historical_v2.continuity_windows import isolated_product_coordinator
from ..historical_v2.contracts import Camera
from ..historical_v2.prototype import DirectSessionApiProfile, PrototypeFailure
from ..historical_v2.rolling_product import WORKER_SHA256
from ..live.policy import LiveSigningPolicy
from ..live.service import Binding
from .historical_v2_api import VERSION, FrigateSession, endpoint
from .retained import private_directory
from .session_server import Access, Authority, OperationsHandler, Server

ACCEPTED_WORKER = WORKER_SHA256
LIVE_PORT = 18774
HISTORICAL_PORT = 8766


def accepted_listeners():
    """Loopback ports already fixed by the committed nginx fragment and Live API."""
    return {'live': ('127.0.0.1', LIVE_PORT), 'historical': ('127.0.0.1', HISTORICAL_PORT)}


class HostEntryError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise HostEntryError('configuration')
        result[key] = value
    return result


def _fields(value, required):
    if type(value) is not dict or set(value) != set(required.split()):
        raise HostEntryError('configuration')


def _absolute(value):
    if type(value) is not str or not value or '\x00' in value:
        raise HostEntryError('configuration')
    path = Path(value)
    if not path.is_absolute() or '..' in path.parts:
        raise HostEntryError('configuration')
    return path


def _loopback(value):
    if value != '127.0.0.1':
        raise HostEntryError('historical-listener')
    return value


def _port(value):
    if type(value) is not int or isinstance(value, bool) or not 1024 <= value <= 65535:
        raise HostEntryError('configuration')
    return value


def _private_file(path):
    try:
        info = path.lstat()
    except OSError:
        raise HostEntryError('token-file') from None
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.getuid():
        raise HostEntryError('token-file')
    return path


def _executable(value):
    path = _absolute(value)
    if not path.is_file() or not os.access(path, os.X_OK):
        raise HostEntryError('configuration')
    return path


def _origin(value, *, https_loopback=False):
    if type(value) is not str:
        raise HostEntryError('configuration')
    try:
        normalized = endpoint(value)
    except ValueError:
        raise HostEntryError('configuration') from None
    parsed = urlsplit(normalized)
    if https_loopback and (parsed.scheme != 'https' or parsed.hostname != '127.0.0.1' or parsed.port is None):
        raise HostEntryError('configuration')
    return normalized, parsed


def _number(value, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
        raise HostEntryError('configuration')
    return value


def _worker_identity(path, expected):
    """Reject a mismatched worker before a listener or ffmpeg starts.

    ``admit_worker_executable`` repeats this check before exec. The path can
    still be replaced in between; that TOCTOU limitation is unchanged.
    """
    if expected != ACCEPTED_WORKER:
        raise HostEntryError('worker-identity')
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or not 0 < info.st_size <= 8 << 20:
            raise OSError()
        descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    except OSError:
        raise HostEntryError('worker-identity') from None
    try:
        digest = hashlib.sha256()
        remaining = info.st_size
        while remaining:
            chunk = os.read(descriptor, min(1 << 20, remaining))
            if not chunk:
                raise HostEntryError('worker-identity')
            digest.update(chunk)
            remaining -= len(chunk)
        if digest.hexdigest() != expected:
            raise HostEntryError('worker-identity')
    finally:
        os.close(descriptor)


def _rtsp(value):
    if type(value) is not str or len(value) > 2048 or any(ord(c) < 33 for c in value):
        raise HostEntryError('camera-binding')
    parsed = urlsplit(value)
    if (parsed.scheme != 'rtsp' or not parsed.hostname or parsed.username or parsed.password
            or parsed.fragment or parsed.port == 0):
        raise HostEntryError('camera-binding')
    return value


def validate_config(config):
    if type(config) is not dict:
        raise HostEntryError('configuration')
    _fields(config, 'schema frigate_version api_origin browser_origin instance_certificate listen '
             'token_file ffmpeg ffprobe policy supervision cameras historical state_root')
    if type(config['schema']) is not int or isinstance(config['schema'], bool) or config['schema'] != 1:
        raise HostEntryError('configuration')
    if config['frigate_version'] != VERSION:
        raise HostEntryError('frigate-version')
    api, api_parts = _origin(config['api_origin'], https_loopback=True)
    browser, browser_parts = _origin(config['browser_origin'])
    certificate = _absolute(config['instance_certificate'])
    if not certificate.is_file() or certificate.is_symlink():
        raise HostEntryError('configuration')
    _fields(config['listen'], 'live_host live_port historical_host historical_port')
    live_host = config['listen']['live_host']
    historical_host = config['listen']['historical_host']
    if live_host != '127.0.0.1':
        raise HostEntryError('configuration')
    _loopback(historical_host)
    live_port = _port(config['listen']['live_port'])
    historical_port = _port(config['listen']['historical_port'])
    if live_port == historical_port:
        raise HostEntryError('configuration')
    token = _private_file(_absolute(config['token_file']))
    try:
        token_from_file(token)
    except ValueError:
        raise HostEntryError('token-file') from None
    ffmpeg = _executable(config['ffmpeg'])
    ffprobe = _executable(config['ffprobe'])
    _fields(config['policy'], 'policy_id evaluation_interval active_freshness_limit observation_horizon media_silence_limit')
    policy_fields = config['policy']
    try:
        identifier(policy_fields['policy_id'])
        policy = LiveSigningPolicy(
            policy_fields['policy_id'],
            _number(policy_fields['evaluation_interval'], 0.01, 10),
            _number(policy_fields['active_freshness_limit'], 0.01, 3600),
            _number(policy_fields['observation_horizon'], 0.01, 3600),
            _number(policy_fields['media_silence_limit'], 0.01, 3600))
    except (HostEntryError, ValueError):
        raise HostEntryError('configuration') from None
    _fields(config['supervision'], 'poll_seconds backoff_initial_seconds backoff_max_seconds')
    supervision = config['supervision']
    poll = _number(supervision['poll_seconds'], 0.05, 2)
    backoff_initial = _number(supervision['backoff_initial_seconds'], 0.1, 5)
    backoff_max = _number(supervision['backoff_max_seconds'], 0.5, 30)
    if backoff_max < backoff_initial:
        raise HostEntryError('configuration')
    cameras = config['cameras']
    if type(cameras) is not list or not 1 <= len(cameras) <= 16:
        raise HostEntryError('camera-binding')
    seen, bindings = set(), {}
    normalized = []
    for item in cameras:
        _fields(item, 'camera stream context rendition source_id revision rtsp_url')
        try:
            for name in ('camera', 'stream', 'context', 'rendition', 'source_id', 'revision'):
                identifier(item[name])
        except ValueError:
            raise HostEntryError('camera-binding') from None
        if item['context'] not in ('grid', 'single'):
            raise HostEntryError('camera-binding')
        key = item['camera'], item['stream'], item['context']
        if key in seen:
            raise HostEntryError('camera-binding')
        seen.add(key)
        logical = item['camera'], item['rendition']
        material = item['source_id'], item['revision'], item['rtsp_url']
        if logical in bindings and bindings[logical] != material:
            raise HostEntryError('camera-binding')
        bindings[logical] = material
        normalized.append({**item, 'rtsp_url': _rtsp(item['rtsp_url'])})
    _fields(config['historical'], 'media_root scratch_root public_ca worker worker_sha256')
    historical = config['historical']
    media = _absolute(historical['media_root'])
    scratch = _absolute(historical['scratch_root'])
    public_ca = _absolute(historical['public_ca'])
    worker = _absolute(historical['worker'])
    state = _absolute(config['state_root'])
    if not media.is_dir() or media.is_symlink() or not public_ca.is_file() or public_ca.is_symlink():
        raise HostEntryError('configuration')
    if type(historical['worker_sha256']) is not str:
        raise HostEntryError('worker-identity')
    _worker_identity(worker, historical['worker_sha256'])
    return {
        'frigate_version': VERSION,
        'api_origin': api,
        'api_port': api_parts.port,
        'browser_origin': browser,
        'browser_host': browser_parts.netloc,
        'instance_certificate': certificate,
        'live': (live_host, live_port),
        'historical': (historical_host, historical_port),
        'token_file': token,
        'ffmpeg': ffmpeg,
        'ffprobe': ffprobe,
        'policy': policy,
        'poll_seconds': poll,
        'backoff_initial_seconds': backoff_initial,
        'backoff_max_seconds': backoff_max,
        'cameras': tuple(normalized),
        'media_root': media,
        'scratch_root': scratch,
        'public_ca': public_ca,
        'worker': worker,
        'worker_sha256': historical['worker_sha256'],
        'state_root': state,
    }


def load_config(path):
    if type(path) is not str and not isinstance(path, Path):
        raise HostEntryError('configuration')
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise HostEntryError('configuration')
    try:
        info = path.lstat()
    except OSError:
        raise HostEntryError('configuration') from None
    if (not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or info.st_mode & 0o077
            or info.st_uid != os.getuid() or info.st_size > 65536):
        raise HostEntryError('configuration')
    try:
        config = json.loads(path.read_bytes(), object_pairs_hook=_pairs,
                            parse_constant=lambda _: (_ for _ in ()).throw(HostEntryError('configuration')))
    except (json.JSONDecodeError, UnicodeError, OSError, HostEntryError):
        raise HostEntryError('configuration') from None
    return validate_config(config)


def reader_command(executable, url):
    return (
        str(executable), '-hide_banner', '-nostdin', '-loglevel', 'error',
        '-rtsp_transport', 'tcp', '-i', url,
        '-an', '-c:v', 'copy', '-f', 'h264', 'pipe:1',
    )


class Feed:
    """One ffmpeg reader, pipe, and observation source for one camera."""

    def __init__(self, name, command, poll, backoff_initial, backoff_max):
        self.name = name
        self.command = tuple(command)
        self.poll = poll
        self.backoff_initial = backoff_initial
        self.backoff_max = backoff_max
        self.lock = threading.Lock()
        self.write = None
        self.proc = None
        self.proc_gen = None
        self.generation = 0
        self.started_at = 0.0
        self.backoff = 0.0
        self.next_start = 0.0
        self.stop_event = threading.Event()
        self.failures = 0
        self.thread = None

    def factory(self, binding):
        if binding.camera.value != self.name:
            raise OSError('binding does not match this feed')
        read_fd, write_fd = os.pipe()
        os.set_blocking(write_fd, False)
        with self.lock:
            previous, self.write = self.write, write_fd
            self.generation += 1
        if previous is not None:
            os.close(previous)
        return FDObservationSource(read_fd)

    def _close(self, fd):
        if fd is None:
            return
        try:
            os.close(fd)
        except OSError:
            pass

    def _signal(self, proc, sig):
        try:
            os.killpg(proc.pid, sig)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                proc.send_signal(sig)
            except OSError:
                pass

    def _stop_proc(self):
        with self.lock:
            proc = self.proc
            generation = self.proc_gen
            self.proc = None
            self.proc_gen = None
            close_fd = None
            if proc is not None and generation == self.generation and self.write is not None:
                close_fd = self.write
                self.write = None
        if proc is not None and proc.poll() is None:
            self._signal(proc, signal.SIGTERM)
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._signal(proc, signal.SIGKILL)
                proc.wait(timeout=1)
        self._close(close_fd)

    def _reap(self):
        with self.lock:
            proc = self.proc
            generation = self.proc_gen
            started = self.started_at
        if proc is None or proc.poll() is None:
            return
        with self.lock:
            if self.proc is not proc:
                return
            self.proc = None
            self.proc_gen = None
            close_fd = None
            if generation == self.generation and self.write is not None:
                close_fd = self.write
                self.write = None
        self._close(close_fd)
        lived = time.monotonic() - started if started else 0
        if lived < 2:
            self.backoff = min(self.backoff_max, self.backoff * 2 if self.backoff else self.backoff_initial)
            self.failures += 1
        else:
            self.backoff = self.backoff_initial
        self.next_start = time.monotonic() + self.backoff

    def _once(self):
        self._reap()
        if time.monotonic() < self.next_start:
            return
        with self.lock:
            write = self.write
            generation = self.generation
            running = self.proc is not None and self.proc.poll() is None
        if write is None or running:
            return
        proc = subprocess.Popen(
            self.command, stdout=write, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
            start_new_session=True)
        with self.lock:
            running = self.proc is not None and self.proc.poll() is None
            keep = (self.generation == generation and self.write == write
                    and not self.stop_event.is_set() and not running)
            if keep:
                self.proc = proc
                self.proc_gen = generation
                self.started_at = time.monotonic()
                proc = None
        if proc is not None:
            if proc.poll() is None:
                self._signal(proc, signal.SIGTERM)
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self._signal(proc, signal.SIGKILL)
                    proc.wait(timeout=1)

    def loop(self):
        while not self.stop_event.is_set():
            try:
                self._once()
            except Exception:
                self.failures += 1
                self._stop_proc()
                self.backoff = min(self.backoff_max, self.backoff * 2 if self.backoff else self.backoff_initial)
                self.next_start = time.monotonic() + self.backoff
                if self.stop_event.wait(self.backoff):
                    return
                continue
            if self.stop_event.wait(self.poll):
                return

    def start(self):
        self.thread = threading.Thread(target=self.loop, name='feed-' + self.name, daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        self._stop_proc()
        if self.thread is not None:
            self.thread.join(timeout=3)


class BindingResolver:
    """Authorize a Historical camera only while Live still publishes that binding."""

    def __init__(self, host, port, token):
        self.host = host
        self.port = port
        self._token = token

    def _bindings(self):
        connection = http.client.HTTPConnection(self.host, self.port, timeout=2)
        try:
            connection.request('GET', '/v1/cameras/bindings', headers={
                'Authorization': 'Bearer ' + self._token,
                'Accept': 'application/json',
                'Accept-Encoding': 'identity',
            })
            response = connection.getresponse()
            raw = response.read(32768)
            if response.read(1):
                raise RuntimeError('bindings response too large')
            return response.status, json.loads(raw)
        except Exception:
            raise PrototypeFailure('UNAUTHORIZED') from None
        finally:
            connection.close()

    def _hv2_camera(self, camera, guard):
        try:
            code, document = self._bindings()
        except Exception:
            raise PrototypeFailure('UNAUTHORIZED') from None
        rows = [row for row in document.get('cameras', [])
                if row.get('camera_id') == camera and row.get('coverage_state') == 'CONFIGURED'
                and row.get('binding_guard') == guard]
        if code != 200 or len(rows) != 1:
            raise PrototypeFailure('UNAUTHORIZED')
        return Camera('frigate-captured', camera, guard, camera)


class Running:
    def __init__(self):
        self.feeds = []
        self.jobs = None
        self.scheduler = None
        self.service = None
        self.live_server = None
        self.live_thread = None
        self.historical_server = None
        self.historical_thread = None
        self.coordinator = None
        self.installed = False
        self.stopped = False

    def stop(self):
        if self.stopped:
            return False
        self.stopped = True
        errors = False
        for feed in self.feeds:
            try:
                feed.stop()
            except Exception:
                errors = True
        closed_coordinator = False
        if self.historical_server is not None:
            try:
                if self.historical_thread is not None:
                    self.historical_server.shutdown()
                    self.historical_thread.join(timeout=5)
                    if self.historical_thread.is_alive():
                        errors = True
                self.historical_server.server_close()
                closed_coordinator = self.installed
            except Exception:
                errors = True
        if self.coordinator is not None and not closed_coordinator:
            try:
                self.coordinator.close()
            except Exception:
                errors = True
        if self.live_server is not None:
            try:
                if self.live_thread is not None:
                    self.live_server.shutdown()
                    self.live_thread.join(timeout=3)
                    if self.live_thread.is_alive():
                        errors = True
                self.live_server.server_close()
            except Exception:
                errors = True
        try:
            if self.service is not None:
                self.service.close()
            else:
                if self.scheduler is not None:
                    self.scheduler.close()
                if self.jobs is not None:
                    self.jobs.close()
        except Exception:
            errors = True
        for feed in self.feeds:
            if feed.proc is not None and feed.proc.poll() is None:
                errors = True
        return errors


def _wait_live(host, port):
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        try:
            probe = http.client.HTTPConnection(host, port, timeout=0.2)
            probe.request('GET', '/readyz')
            probe.getresponse().read()
            probe.close()
            return
        except OSError:
            time.sleep(0.05)
    raise HostEntryError('startup-failed')


def start(config):
    """Open both listeners. On any failure, nothing from this call stays running."""
    if os.geteuid() == 0:
        raise HostEntryError('configuration')
    if not isinstance(config.get('policy'), LiveSigningPolicy):
        config = validate_config(config)
    _worker_identity(config['worker'], config['worker_sha256'])
    running = Running()
    try:
        private_directory(config['scratch_root'])
        context = ssl.create_default_context(cafile=str(config['instance_certificate']))
        # The accepted local leaf is pinned and has no localhost SAN. Caller
        # authorization still requires the frigate_token and Frigate's profile.
        context.check_hostname = False
        session = FrigateSession(config['api_origin'], context)
        profile = DirectSessionApiProfile(
            session, config['media_root'], config['scratch_root'], config['worker'],
            config['public_ca'], config['ffprobe'])
        running.coordinator = isolated_product_coordinator(
            profile, config['worker'], config['worker_sha256'])
        policy = config['policy']
        feeds = {
            item['camera']: Feed(
                item['camera'], reader_command(config['ffmpeg'], item['rtsp_url']),
                config['poll_seconds'], config['backoff_initial_seconds'], config['backoff_max_seconds'])
            for item in config['cameras']
        }
        mappings = tuple(
            PlaybackMapping(
                item['camera'], item['stream'], item['context'],
                Binding(CameraId(item['camera']), RenditionId(item['rendition']),
                        item['source_id'], item['revision'], policy))
            for item in config['cameras'])
        running.feeds = list(feeds.values())
        running.scheduler = LiveScheduler(
            mappings, lambda binding: feeds[binding.camera.value].factory(binding),
            interval=policy.evaluation_interval)
        running.jobs = JobStore(config['state_root'] / 'jobs', lambda *args, **kwargs: None)
        running.service = CompanionService(
            running.scheduler, running.jobs, tuple(item['camera'] for item in config['cameras']))
        token = token_from_file(config['token_file'])
        running.live_server = LiveHTTPServer(config['live'], LiveTransport(running.service, token))
        for feed in running.feeds:
            feed.start()
        running.scheduler.start()
        running.live_thread = threading.Thread(
            target=running.live_server.serve_forever, name='live-http', daemon=True)
        running.live_thread.start()
        _wait_live(*config['live'])
        authority = Authority('127.0.0.1', config['api_port'], context)
        resolver = BindingResolver(*config['live'], token)
        access = Access(authority, resolver._bindings)
        running.historical_server = Server(config['historical'], OperationsHandler)
        running.historical_server.expected_origin = config['browser_origin']
        running.historical_server.expected_host = config['browser_host']
        running.historical_server.bridge = access
        if config['frigate_version'] != VERSION:
            raise HostEntryError('frigate-version')
        install(running.historical_server, access, resolver, running.coordinator)
        running.installed = True
        running.historical_thread = threading.Thread(
            target=lambda: running.historical_server.serve_forever(poll_interval=0.1),
            name='historical-http', daemon=True)
        running.historical_thread.start()
        return running
    except Exception:
        running.stop()
        raise


def run(config):
    os.umask(0o077)
    running = start(config)
    stop = threading.Event()
    previous = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}

    def _handle(signum, _frame):
        stop.set()

    try:
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, _handle)
        print('host-entry-ready; readiness is not a signing verdict.', flush=True)
        stop.wait()
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
        if running.stop():
            raise HostEntryError('shutdown-incomplete')


def main(argv=None):
    parser = argparse.ArgumentParser(description='Nanexus Frigate 0.18.2 Live and Historical host entry')
    parser.add_argument('--config', required=True)
    args = parser.parse_args(argv)
    try:
        run(load_config(args.config))
    except HostEntryError as exc:
        print(exc.code, file=sys.stderr)
        return 1
    except Exception:
        print('startup-failed', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
