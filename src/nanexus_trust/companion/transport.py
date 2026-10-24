"""Private GET-only Live adapter. No browser identity or History API."""

from decimal import Decimal, InvalidOperation
from http.server import BaseHTTPRequestHandler, HTTPServer
import hmac
import json
import re
import socket
from socketserver import ThreadingMixIn
import threading
from urllib.parse import parse_qs, urlsplit

from ..contracts import identifier, utc_timestamp

MAX_RESPONSE = 4096
REASONS = frozenset(('startup', 'reconnect', 'unsupported_signing_structure', 'parser_uncertainty',
    'observation_discontinuity', 'freshness_unconfigured', 'no_media_observed', 'observer_unavailable',
    'no_signing_evidence_in_window', 'evidence_stale', 'signing_evidence_overdue', 'recent_signing_evidence',
    'invalid_request', 'service_closed', 'mapping_unavailable', 'observer_unavailable',
    'observation_stopped', 'adapter_backlog', 'transport_unavailable'))
FIELDS = ('status', 'reason', 'age_ms', 'observed_at', 'last_evidence_at', 'session_id',
          'mapping_revision', 'logical_camera', 'rendition')


def unknown():
    return dict.fromkeys(FIELDS) | {'status': 'UNKNOWN', 'reason': 'transport_unavailable'}


def token_from_file(path):
    """Deployment secret only. Never include path/value in exceptions or logs."""
    try:
        with open(path, 'rb') as stream:
            token = stream.read(129).strip()
        if not re.fullmatch(rb'[A-Za-z0-9_-]{43,128}', token):
            raise ValueError()
        return token.decode('ascii')
    except (OSError, ValueError, TypeError):
        raise ValueError('service credential unavailable') from None


def playback_query(query):
    if len(query) > 1024:
        raise ValueError('invalid request')
    values = parse_qs(query, keep_blank_values=True, strict_parsing=True, max_num_fields=3)
    if set(values) != {'camera', 'stream', 'context'} or any(len(v) != 1 for v in values.values()):
        raise ValueError('invalid request')
    return tuple(identifier(values[key][0]) for key in ('camera', 'stream', 'context'))


def live_document(result):
    """Whitelist/validate fields; never send arbitrary service dictionaries."""
    try:
        status, reason = result['status'], result['reason']
        if status not in ('ACTIVE', 'DEGRADED', 'UNKNOWN') or reason not in REASONS:
            return unknown()
        document = {'status': status, 'reason': reason, 'age_ms': result['evidence_age_ms'],
                    'observed_at': result['observed_at'], 'last_evidence_at': result['last_evidence_at'],
                    'session_id': result['session_id'], 'mapping_revision': result['mapping_id'],
                    'logical_camera': result['camera'], 'rendition': result['rendition']}
        for name in ('session_id', 'mapping_revision', 'logical_camera', 'rendition'):
            if document[name] is not None:
                identifier(document[name])
        for name in ('observed_at', 'last_evidence_at'):
            if document[name] is not None:
                utc_timestamp(document[name])
        age = document['age_ms']
        if age is not None:
            if type(age) is not str or len(age) > 64:
                return unknown()
            number = Decimal(age)
            if not number.is_finite() or number < 0 or number.adjusted() > 20 or number.as_tuple().exponent < -9:
                return unknown()
            document['age_ms'] = format(number, 'f')
            if not re.fullmatch(r'[0-9]{1,20}(?:\.[0-9]{1,9})?', document['age_ms']):
                return unknown()
        if status != 'UNKNOWN' and any(document[k] is None for k in FIELDS if k != 'last_evidence_at'):
            return unknown()
        if len(json.dumps(document).encode()) > MAX_RESPONSE:
            return unknown()
        return document
    except (KeyError, ValueError, TypeError, InvalidOperation):
        return unknown()


class LiveTransport:
    def __init__(self, service, token):
        if type(token) is not str or not re.fullmatch(r'[A-Za-z0-9_-]{43,128}', token):
            raise ValueError('high-entropy deployment credential required')
        self.service = service
        self._token = token

    def dispatch(self, method, target, authorization):
        if type(authorization) is not str or not hmac.compare_digest(
                authorization.encode(), ('Bearer ' + self._token).encode()):
            return 401, {'error': 'unauthorized'}
        if method != 'GET':
            return 405, {'error': 'method_not_allowed'}
        if len(target) > 1200:
            return 400, {'error': 'invalid_request'}
        try:
            parsed = urlsplit(target)
        except ValueError:
            return 400, {'error': 'invalid_request'}
        if parsed.scheme or parsed.netloc or parsed.fragment:
            return 400, {'error': 'invalid_request'}
        if parsed.path == '/readyz' and not parsed.query:
            try:
                ready = self.service.readiness().get('ready') is True
                return (200 if ready else 503), {'ready': ready}
            except Exception:
                return 503, {'ready': False}
        if parsed.path == '/v1/cameras/bindings' and not parsed.query:
            try:
                return 200, self.service.camera_bindings()
            except Exception:
                return 503, {'error': 'service_unavailable'}
        if parsed.path == '/v1/cameras/status':
            try:
                values = parse_qs(parsed.query, strict_parsing=True, keep_blank_values=True, max_num_fields=2)
                if set(values) != {'camera', 'guard'} or any(len(v) != 1 for v in values.values()):
                    raise ValueError()
                camera, guard = (identifier(values[k][0]) for k in ('camera', 'guard'))
            except ValueError:
                return 400, {'error': 'invalid_request'}
            try:
                return 200, self.service.camera_status(camera, guard)
            except Exception:
                return 503, {'error': 'service_unavailable'}
        if parsed.path != '/v1/live/status':
            return 404, {'error': 'not_found'}
        try:
            key = playback_query(parsed.query)
        except ValueError:
            return 400, {'error': 'invalid_request'}
        try:
            return 200, live_document(self.service.live_status(*key))
        except Exception:
            # Boundary catches service failure; no raw diagnostics or stale cache.
            return 200, unknown()


class LiveHTTPServer(ThreadingMixIn, HTTPServer):
    """At most eight bounded connections; private network binding is deployment's job."""
    daemon_threads = True
    request_queue_size = 8

    def __init__(self, address, transport):
        self.transport = transport
        self._slots = threading.BoundedSemaphore(8)
        super().__init__(address, _Handler)

    def get_request(self):
        stream, address = super().get_request()
        stream.settimeout(1)
        return stream, address

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._slots.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self._slots.release()

    def handle_error(self, request, client_address):
        pass  # No credential/request/exception logging.


class _Handler(BaseHTTPRequestHandler):
    def handle(self):
        def expire():
            try:
                self.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        timer = threading.Timer(2, expire)
        timer.daemon = True
        timer.start()
        try:
            super().handle()
        finally:
            timer.cancel()

    def log_message(self, *args):
        pass

    def send_error(self, code, message=None, explain=None):
        self._reply(code, {'error': 'invalid_request'})

    def _reply(self, code, document):
        data = json.dumps(document, separators=(',', ':'), allow_nan=False).encode()
        limit = 32768 if getattr(self, 'path', '') == '/v1/cameras/bindings' else MAX_RESPONSE
        if len(data) > limit:
            code, data = 200, json.dumps(unknown()).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store, private')
        self.send_header('Connection', 'close')
        self.end_headers()
        self.wfile.write(data)
        self.close_connection = True

    def do_GET(self):
        if len(self.headers) > 16 or sum(len(k)+len(v) for k,v in self.headers.items()) > 4096:
            self._reply(400, {'error': 'invalid_request'})
            return
        auth = self.headers.get_all('Authorization', [])
        code, document = self.server.transport.dispatch(self.command, self.path, auth[0] if len(auth)==1 else None)
        self._reply(code, document)

    do_POST = do_GET
    do_PUT = do_GET
    do_PATCH = do_GET
    do_DELETE = do_GET
    do_HEAD = do_GET
    do_OPTIONS = do_GET
