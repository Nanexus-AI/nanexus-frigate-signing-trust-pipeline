"""Pinned recordings API discovery; standard original-file layout, never SQL.

The API response is a request-local metadata observation, not a frozen archive
or a qualified clock origin. Staging/read/release reuse the accepted Provider.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from http.client import HTTPException
import hashlib
import json
import math
from pathlib import Path
import re
import time
from urllib.error import URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .historical_v2 import FrigateHistoricalProvider, PIN_0182, PROFILE_0182, ProviderFailure
from ..historical_v2.contracts import Interval, Quality
from ..historical_v2.provider import InventoryPage, Item

API_PROFILE = 'frigate-0.18.2-cfa08c5-recordings-api-utc-original-v1'
VERSION = '0.18.2-cfa08c5'
MAX_API_BYTES = 128 << 10


def endpoint(value):
    parsed = urlsplit(value)
    if (parsed.scheme not in ('http', 'https') or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or parsed.query or parsed.fragment or parsed.path not in ('', '/')
            or any(ord(c) < 33 for c in value)):
        raise ValueError('trusted Frigate origin required')
    return value.rstrip('/')


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ProviderFailure('API_REDIRECT_REFUSED')


def _pairs(values):
    result = {}
    for key, value in values:
        if key in result:
            raise ValueError('duplicate API field')
        result[key] = value
    return result


@dataclass(frozen=True)
class ApiCut:
    """Identity of exact version/metadata bytes and bound request; not SQLite."""
    sha256: str
    cut_id: str
    source_pin: str = PIN_0182


class ApiProvider(FrigateHistoricalProvider):
    max_interval = 900
    max_rows = 128
    max_items = 9
    max_api_bytes = MAX_API_BYTES
    def __init__(self, camera, origin, token_file, media_root, scratch_root, *, native_authorize):
        self.origin = endpoint(origin)
        self.instance_id = hashlib.sha256(self.origin.encode()).hexdigest()
        self.token_file = Path(token_file) if token_file is not None else None
        # The parent initializer only needs the explicit source pin and owns
        # staging lifecycle/authority. Its SQLite discovery is never invoked.
        super().__init__(camera, ApiCut('0' * 64, 'api-unobserved'), media_root,
                         scratch_root, native_authorize=native_authorize, profile=PROFILE_0182)
        self.profile = API_PROFILE
        self.observed = False

    def _get(self, target, cap, bounds, cancel):
        self._guard(self.camera, bounds, cancel)
        headers = {'Accept': 'application/json', 'Cache-Control': 'no-cache'}
        if self.token_file is not None:
            with self.token_file.open('rb') as stream:
                token = stream.read(4097).strip()
            if not 0 < len(token) <= 4096 or any(c < 33 or c > 126 for c in token):
                raise ProviderFailure('API_CREDENTIAL_UNAVAILABLE')
            headers['Authorization'] = 'Bearer ' + token.decode('ascii')
        deadline = min(time.monotonic() + 2, bounds.deadline_monotonic.numerator /
                       bounds.deadline_monotonic.denominator)
        try:
            with build_opener(_NoRedirect()).open(Request(self.origin + target, headers=headers), timeout=2) as response:
                if response.status != 200:
                    raise ProviderFailure('API_UNAVAILABLE')
                declared = response.headers.get('Content-Length')
                if declared is not None:
                    if not declared.isdigit():
                        raise ProviderFailure('API_INCOMPLETE_RESPONSE')
                    declared = int(declared)
                    if declared > cap:
                        raise ProviderFailure('API_RESPONSE_LIMIT')
                if response.headers.get('Content-Encoding', 'identity') != 'identity':
                    raise ProviderFailure('API_RESPONSE_ENCODING_UNSUPPORTED')
                raw = bytearray()
                while True:
                    self._guard(self.camera, bounds, cancel)
                    if time.monotonic() >= deadline:
                        raise ProviderFailure('API_DEADLINE')
                    # A single buffered/raw read returns available bytes so a
                    # slow trickle cannot keep a filling read(n) alive forever.
                    data = response.read1(min(8192, cap - len(raw) + 1))
                    if not data:
                        if declared is not None and len(raw) != declared:
                            raise ProviderFailure('API_INCOMPLETE_RESPONSE')
                        return bytes(raw)
                    raw.extend(data)
                    if len(raw) > cap:
                        raise ProviderFailure('API_RESPONSE_LIMIT')
        except (URLError, HTTPException, OSError, ValueError) as exc:
            raise ProviderFailure('API_UNAVAILABLE') from exc

    def discover(self, request, cancel):
        self._guard(request.camera, request.bounds, cancel)
        if (self.observed or request.cursor is not None or request.coordinate != 'host-metadata-unix-seconds'
                or request.bounds.max_context_items or self._handles
                or Decimal(request.requested.end) - Decimal(request.requested.start) > self.max_interval):
            raise ProviderFailure('API_SCOPE_UNSUPPORTED')
        self.observed = True
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', self.camera.native_key):
            raise ProviderFailure('API_CAMERA_UNSUPPORTED')
        version = self._get('/api/version', 64, request.bounds, cancel).decode('ascii').strip()
        if version != VERSION:
            raise ProviderFailure('API_VERSION_UNSUPPORTED')
        # Server compares inclusive float endpoints. Round outwards, then
        # select half-open overlaps using exact decimal metadata locally.
        query = urlencode({'after': repr(math.nextafter(float(request.requested.start), -math.inf)),
                           'before': repr(math.nextafter(float(request.requested.end), math.inf))})
        target = '/api/' + quote(self.camera.native_key, safe='') + '/recordings?' + query
        raw = self._get(target, self.max_api_bytes, request.bounds, cancel)
        try:
            rows = json.loads(raw, parse_float=Decimal, object_pairs_hook=_pairs,
                              parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
            if type(rows) is not list:
                raise ValueError('API list required')
            if len(rows) > self.max_rows:
                raise ProviderFailure('API_RESPONSE_LIMIT')
            fields = {'id', 'start_time', 'end_time', 'segment_size', 'motion', 'objects', 'motion_heatmap', 'duration'}
            selected = []
            seen = set()
            paths = set()
            for row in rows:
                self._guard(request.camera, request.bounds, cancel)
                if type(row) is not dict or set(row) != fields:
                    raise ValueError('API fields')
                rid = row['id']
                if type(rid) is not str or len(rid) > 30 or not re.fullmatch(r'[0-9]+(?:\.[0-9]+)?-[a-z0-9]{6}', rid):
                    raise ValueError('API identity')
                for key in ('start_time', 'end_time', 'duration'):
                    if type(row[key]) not in (int, Decimal) or not Decimal(row[key]).is_finite():
                        raise ValueError('API time')
                left, right = Decimal(row['start_time']), Decimal(row['end_time'])
                if left < 0 or left >= right or row['duration'] <= 0 or Decimal(rid.rsplit('-', 1)[0]) != left or rid in seen:
                    raise ValueError('API interval/identity')
                seen.add(rid)
                if left >= Decimal(request.requested.end) or right <= Decimal(request.requested.start):
                    continue
                # Exact pinned move_segment formatter, restricted to its UTC
                # standard layout. Never used as signing time/origin evidence.
                date = datetime.fromtimestamp(int(left), timezone.utc)
                relative = Path(date.strftime('%Y-%m-%d/%H')) / self.camera.native_key / date.strftime('%M.%S.mp4')
                if relative in paths:
                    raise ValueError('API ambiguous original path')
                paths.add(relative)
                selected.append((left, rid, row, relative))
            selected.sort(key=lambda value: (value[0], value[1]))
        except (ValueError, TypeError, OverflowError, RecursionError) as exc:
            raise ProviderFailure('API_MALFORMED') from exc
        self.response_sha256 = hashlib.sha256(raw).hexdigest()
        binding = dict(instance=self.instance_id,version=version, camera=self.camera.native_key, binding=self.camera.binding_generation,
                       requested=[request.requested.start, request.requested.end], response_sha256=hashlib.sha256(raw).hexdigest())
        digest = hashlib.sha256(json.dumps(binding, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        self.snapshot = ApiCut(digest, 'api-' + digest)
        limit = min(self.max_items, request.bounds.max_items)
        self._items.clear()
        items = []
        for _, rid, row, relative in selected[:limit]:
            generation = hashlib.sha256((digest + ':' + rid).encode()).hexdigest()
            item = Item(rid, generation, self.camera, Interval(format(Decimal(row['start_time']),'f'), format(Decimal(row['end_time']),'f')),
                        request.coordinate, None, False, 'h264', 'mp4-avc1', Quality.UNKNOWN, None, None)
            self._items[rid] = (item, relative, None)
            items.append(item)
        overlaps = set()
        for i, left in enumerate(items):
            for right in items[i + 1:]:
                if Decimal(right.metadata_interval.start) < Decimal(left.metadata_interval.end):
                    overlaps.update((left.item_id, right.item_id))
        self._guard(request.camera, request.bounds, cancel)
        return self.inventory_page(digest, self.snapshot.cut_id, self.profile, self.camera, request.requested,
                             Quality.INCOMPLETE if len(selected) > limit else Quality.SATISFIED,
                             Quality.SATISFIED, tuple(items), (), (), tuple(sorted(overlaps)), None, True)


class WindowApiProvider(ApiProvider):
    from ..historical_v2.window_inventory import window_inventory
    inventory_page = staticmethod(window_inventory)
    """Opt-in window discovery, one bounded response, no invented pagination."""
    max_interval = 1800
    max_rows = 256
    max_items = 257
    max_api_bytes = 512 << 10

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.profile = 'frigate-0.18.2-cfa08c5-recordings-api-window-experimental-v1'
