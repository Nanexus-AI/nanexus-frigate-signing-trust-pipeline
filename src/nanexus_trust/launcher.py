"""Explicit POSIX host composition; no discovery, installation or trust policy."""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import threading
import time
from urllib.parse import urlsplit
import urllib.request

from .companion import CompanionService, FDObservationSource, JobStore, LiveScheduler, PlaybackMapping
from .companion.historical_transport import HistoricalHTTPServer, HistoricalTransport
from .companion.service import HistoricalProfile
from .companion.transport import token_from_file
from .contracts import CameraId, RenditionId, identifier
from .historical.backend import ToolkitBackend
from .historical.executor import FiniteExecutor
from .historical.models import Limits
from .live import Binding, LiveSigningPolicy

PROFILE = '0.18.0-77a66e7'


def pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError('duplicate configuration key')
        result[key] = value
    return result


def fields(value, required):
    if type(value) is not dict or set(value) != set(required.split()):
        raise ValueError('invalid configuration fields')


def absolute(value):
    if type(value) is not str or not value or '\x00' in value:
        raise ValueError('absolute configuration path required')
    path = Path(value)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('absolute configuration path required')
    return path


def load_config(path):
    """Private operator input. Never echo paths, URLs, credentials or exceptions."""
    path = Path(path)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.getuid():
        raise ValueError('configuration must be private and owned by service user')
    with path.open('rb') as stream:
        payload = stream.read(65537)
    if len(payload) > 65536:
        raise ValueError('configuration limit')
    config = json.loads(payload, object_pairs_hook=pairs,
                        parse_constant=lambda _: (_ for _ in ()).throw(ValueError('invalid number')))
    selectors = config.get('canonical_signing_bindings', [])
    if type(selectors) is not list or len(selectors) > 16:
        raise ValueError('bounded canonical selectors required')
    from .companion.cameras import CanonicalSelector
    for item in selectors:
        fields(item, 'camera_id stream context binding_identity')
        CanonicalSelector(**item)
    core = {k: v for k, v in config.items() if k not in ('canonical_signing_bindings','historical_v2')}
    fields(core, 'schema frigate_version listen token_file tools trust database media_root recording_mount state_root policy mappings')
    if type(config['schema']) is not int or config['schema'] != 1 or config['frigate_version'] != PROFILE:
        raise ValueError('unsupported configuration profile')
    fields(config['listen'], 'host port')
    host, port = config['listen']['host'], config['listen']['port']
    ip = ipaddress.IPv4Address(host)
    if not (ip.is_loopback or ip in ipaddress.IPv4Network('10.0.0.0/8')
            or ip in ipaddress.IPv4Network('172.16.0.0/12') or ip in ipaddress.IPv4Network('192.168.0.0/16')):
        raise ValueError('private IPv4 listener required')
    if type(port) is not int or not 1024 <= port <= 65535:
        raise ValueError('unprivileged listener port required')
    fields(config['tools'], 'toolkit toolkit_version ffmpeg ffprobe')
    identifier(config['tools']['toolkit_version'])
    for name in ('toolkit', 'ffmpeg', 'ffprobe'):
        tool = absolute(config['tools'][name])
        if not tool.is_file() or not os.access(tool, os.X_OK):
            raise ValueError('external executable unavailable')
    fields(config['trust'], 'anchor id')
    identifier(config['trust']['id'])
    if not absolute(config['trust']['anchor']).is_file():
        raise ValueError('public trust anchor unavailable')
    secret = absolute(config['token_file'])
    info = secret.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.getuid():
        raise ValueError('service token must be private and owned by service user')
    token_from_file(secret)
    database, media, state = (absolute(config[k]) for k in ('database', 'media_root', 'state_root'))
    if not database.is_file() or not media.is_dir():
        raise ValueError('Frigate source unavailable')
    absolute(config['recording_mount'])
    # Resolve only for overlap detection; retained adapter independently rejects symlinks.
    roots = (database.parent.resolve(), media.resolve(), state.resolve())
    if any(x == y or x in y.parents or y in x.parents
           for x, y in ((roots[0], roots[2]), (roots[1], roots[2]))):
        raise ValueError('companion state must be separate from Frigate storage')
    fields(config['policy'], 'policy_id evaluation_interval active_freshness_limit observation_horizon media_silence_limit')
    policy = LiveSigningPolicy(**config['policy'])
    if not 0.01 <= policy.evaluation_interval <= 10:
        raise ValueError('invalid scheduler interval')
    mappings = config['mappings']
    if type(mappings) is not list or not 1 <= len(mappings) <= 16:
        raise ValueError('one to sixteen explicit playback mappings required')
    seen, bindings = set(), {}
    for item in mappings:
        fields(item, 'camera stream context rendition source_id revision rtsp_url')
        for name in ('camera', 'stream', 'context', 'rendition', 'source_id', 'revision'):
            identifier(item[name])
        if item['context'] not in ('grid', 'single'):
            raise ValueError('unsupported desktop context')
        key = item['camera'], item['stream'], item['context']
        if key in seen:
            raise ValueError('duplicate playback mapping')
        seen.add(key)
        url = item['rtsp_url']
        if type(url) is not str or len(url) > 2048 or any(ord(c) < 33 for c in url):
            raise ValueError('invalid compressed ingest endpoint')
        parsed = urlsplit(url)
        if parsed.scheme != 'rtsp' or not parsed.hostname or parsed.fragment or parsed.port == 0:
            raise ValueError('explicit RTSP endpoint required')
        binding = item['camera'], item['rendition']
        value = item['source_id'], item['revision'], url
        if binding in bindings and bindings[binding] != value:
            raise ValueError('conflicting logical binding')
        bindings[binding] = value
    if 'historical_v2' in config:
        validate_hv2_config(config['historical_v2'])
    return config


def validate_hv2_config(value):
    from .frigate.historical_v2_api import API_PROFILE, endpoint
    if type(value) is dict and value.get('profile') == API_PROFILE:
        fields(value, 'profile origin token_file media_root worker public_ca ffprobe')
        endpoint(value['origin'])
        for key in ('media_root','worker','public_ca','ffprobe'):
            path=absolute(value[key])
            if not (path.is_dir() if key=='media_root' else path.is_file()):raise ValueError('API prototype input unavailable')
        if value['token_file'] is not None and not absolute(value['token_file']).is_file():raise ValueError('API token unavailable')
        if not os.path.isfile('/usr/bin/prlimit'):raise ValueError('prototype Reader limiter unavailable')
        return
    fields(value, 'profile snapshot snapshot_sha256 cut media_root worker public_ca ffprobe')
    from .frigate.historical_v2 import PROFILE_0182
    if value['profile'] != PROFILE_0182: raise ValueError('unsupported prototype profile')
    for key in ('snapshot','media_root','worker','public_ca','ffprobe'):
        path = absolute(value[key])
        if not (path.is_dir() if key == 'media_root' else path.is_file()): raise ValueError('prototype input unavailable')
    if not os.path.isfile('/usr/bin/prlimit'): raise ValueError('prototype Reader limiter unavailable')
    if type(value['snapshot_sha256']) is not str or len(value['snapshot_sha256']) != 64 or any(c not in '0123456789abcdef' for c in value['snapshot_sha256']): raise ValueError('captured snapshot digest required')
    identifier(value['cut'])


def prototype_coordinator(value, scratch_root):
    from .historical_v2.prototype import Coordinator, CapturedProfile
    from .frigate.historical_v2 import Snapshot, PIN_0182
    validate_hv2_config(value)
    from .historical_v2.prototype import ApiProfile
    from .frigate.historical_v2_api import API_PROFILE
    if value['profile']==API_PROFILE:
        return Coordinator(ApiProfile(value['origin'],Path(value['token_file']) if value['token_file'] is not None else None,
            Path(value['media_root']),Path(scratch_root),Path(value['worker']),Path(value['public_ca']),Path(value['ffprobe'])))
    return Coordinator(CapturedProfile(Snapshot(Path(value['snapshot']),value['snapshot_sha256'],value['cut'],PIN_0182,True,True),
        Path(value['media_root']),Path(scratch_root),Path(value['worker']),Path(value['public_ca']),Path(value['ffprobe'])))


class Ingest:
    """Spawn without waiting for RTSP connection; retire/reap outside query locks."""
    def __init__(self, executable):
        self.executable = executable
        self.processes = {}
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._reap, daemon=True, name='ingest-reaper')
        self.thread.start()

    def open(self, url):
        with self.lock:
            if self.stop.is_set() or len(self.processes) >= 32:
                raise OSError('ingest unavailable')
            process = subprocess.Popen([self.executable, '-hide_banner', '-nostdin', '-loglevel', 'error',
                '-rtsp_transport', 'tcp', '-timeout', '3000000', '-probesize', '32768', '-analyzeduration',
                '0', '-i', url, '-map', '0:v:0', '-an', '-c:v', 'copy', '-flush_packets', '1',
                '-f', 'h264', 'pipe:1'], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, start_new_session=True)
            self.processes[process] = None
            try:
                fd = os.dup(process.stdout.fileno())
            finally:
                process.stdout.close()
        owner = self

        class Source(FDObservationSource):
            def close(self):
                if not self.closed:
                    super().close()
                    owner.retire(process)
        try:
            return Source(fd)
        except Exception:
            os.close(fd)
            self.retire(process)
            raise

    @staticmethod
    def _signal(process, sig):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            pass

    def retire(self, process):
        with self.lock:
            if process in self.processes and self.processes[process] is None:
                self.processes[process] = time.monotonic()
                self._signal(process, signal.SIGTERM)

    def _reap(self):
        while not self.stop.wait(0.1):
            with self.lock:
                for process, retired in list(self.processes.items()):
                    if process.poll() is not None:
                        del self.processes[process]
                    elif retired is not None and time.monotonic() - retired >= 1:
                        self._signal(process, signal.SIGKILL)

    def close(self):
        self.stop.set()
        self.thread.join(timeout=1)
        with self.lock:
            pending = list(self.processes)
            for process in pending:
                self._signal(process, signal.SIGKILL)
            deadline = time.monotonic() + 2
            for process in pending:
                process.wait(timeout=max(0.01, deadline - time.monotonic()))
            self.processes.clear()


def run(config):
    if os.geteuid() == 0:
        raise ValueError('run as a dedicated unprivileged user')
    # Enforce actual kernel read-only source access, not only SQLite mode=ro.
    for path in (Path(config['database']).parent, Path(config['media_root'])):
        if not os.statvfs(path).f_flag & os.ST_RDONLY:
            raise ValueError('Frigate DB directory and media must be mounted read-only')
    ingest = Ingest(config['tools']['ffmpeg'])
    live = jobs = server = service = thread = hv2 = None
    try:
        policy = LiveSigningPolicy(**config['policy'])
        mappings, urls = [], {}
        for item in config['mappings']:
            binding = Binding(CameraId(item['camera']), RenditionId(item['rendition']),
                              item['source_id'], item['revision'], policy)
            mappings.append(PlaybackMapping(item['camera'], item['stream'], item['context'], binding))
            urls[(binding.camera.value, binding.rendition.value)] = item['rtsp_url']
        live = LiveScheduler(tuple(mappings), lambda b: ingest.open(urls[(b.camera.value, b.rendition.value)]),
                             interval=policy.evaluation_interval)
        backend = ToolkitBackend(Path(config['tools']['toolkit']), Path(config['trust']['anchor']),
                                 config['tools']['toolkit_version'])
        executor = FiniteExecutor(backend, Path(config['tools']['ffprobe']))
        state = Path(config['state_root'])
        from .frigate.retained import private_directory
        private_directory(state)
        profile = HistoricalProfile(config['database'], config['media_root'], state / 'scratch',
            executor, backend.component, config['trust']['id'], recording_mount=config['recording_mount'],
            limits=Limits())
        jobs = JobStore(state / 'jobs', profile.service)
        from .companion.cameras import CanonicalSelector
        if 'historical_v2' in config: private_directory(state / 'hv2-scratch')
        hv2 = prototype_coordinator(config['historical_v2'], state / 'hv2-scratch') if 'historical_v2' in config else None
        service = CompanionService(live, jobs, tuple(dict.fromkeys(m['camera'] for m in config['mappings'])),
            canonical_selectors=tuple(CanonicalSelector(**s) for s in config.get('canonical_signing_bindings', [])), historical_v2=hv2)
        server = HistoricalHTTPServer((config['listen']['host'], config['listen']['port']),
                                     HistoricalTransport(service, token_from_file(config['token_file'])))
        stop = threading.Event()
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: stop.set())
        jobs.start()
        live.start()
        thread = threading.Thread(target=server.serve_forever, daemon=True, name='companion-http')
        thread.start()
        print('Nanexus companion started; readiness is not a signing verdict.', flush=True)
        stop.wait()
    finally:
        # Each cleanup runs even if another fails; the host unit owns the complete cgroup.
        errors = []
        for action in (hv2.close if hv2 else None,
                       (lambda: server.shutdown()) if thread else None,
                       server.server_close if server else None,
                       (lambda: thread.join(timeout=2)) if thread else None,
                       live.close if live else None, ingest.close, jobs.close if jobs else None):
            if action:
                try:
                    action()
                except Exception:
                    errors.append(True)
        if errors:
            raise RuntimeError('bounded shutdown incomplete')


def main():
    parser = argparse.ArgumentParser(description='Nanexus explicit host companion')
    parser.add_argument('--config', required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--check', action='store_true', help='validate operator config/resources only; no source mounts or tools executed')
    mode.add_argument('--probe', action='store_true', help='query existing authenticated readiness; not a media/connectivity test')
    args = parser.parse_args()
    try:
        os.umask(0o077)
        config = load_config(args.config)
        if args.check:
            print('Configuration valid; runtime mount, tool compatibility and media readiness not tested.')
        elif args.probe:
            request = urllib.request.Request('http://%s:%d/readyz' % (config['listen']['host'], config['listen']['port']),
                headers={'Authorization': 'Bearer ' + token_from_file(config['token_file'])})
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(request, timeout=2) as response:
                if response.status != 200 or json.loads(response.read(4097)) != {'ready': True}:
                    raise ValueError('not ready')
            print('Companion ready; not a media or trust verdict.')
        else:
            run(config)
        return 0
    except Exception:
        print('Nanexus configuration, source mounts, external tools or service unavailable; check operator setup without logging secrets.')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
