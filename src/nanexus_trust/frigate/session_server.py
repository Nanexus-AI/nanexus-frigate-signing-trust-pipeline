"""Caller-cookie session server for the Frigate 0.18.2 Historical bridge.

Extracted from the accepted private session listener. The browser is authorized
with its own frigate_token. Localhost is not a substitute for that check.
"""
import http.client
import json
import re
import ssl
import threading
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

CAP = 32768
ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z')
FORBIDDEN_HEADERS = (
    'Authorization', 'Remote-User', 'Remote-Role', 'X-Server-Port',
    'X-Proxy-Secret', 'Transfer-Encoding',
)


class Denied(Exception):
    def __init__(self, status=401):
        self.status = status


def decode(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate key')
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))


def credential(value):
    if not isinstance(value, str) or len(value) > 4096 or any(ord(c) < 32 or ord(c) > 126 for c in value):
        raise Denied()
    found = []
    for field in value.split(';'):
        key, sep, token = field.strip().partition('=')
        if key == 'frigate_token':
            if not sep or not re.fullmatch(r'[A-Za-z0-9_.-]{1,3072}', token):
                raise Denied()
            found.append(token)
    if len(found) != 1:
        raise Denied()
    return 'frigate_token=' + found[0]


def refresh_cookie(value):
    if not value:
        return None
    if len(value) > 4096 or '\r' in value or '\n' in value:
        raise Denied(502)
    jar = SimpleCookie()
    jar.load(value)
    if set(jar) != {'frigate_token'}:
        raise Denied(502)
    morsel = jar['frigate_token']
    if (not re.fullmatch(r'[A-Za-z0-9_.-]{1,3072}', morsel.value) or morsel['domain']
            or morsel['path'] != '/' or not morsel['httponly']):
        raise Denied(502)
    # Preserve native attributes verbatim; never mint, persist or broaden a cookie.
    return value


class Authority:
    """Pinned authenticated Frigate listener. No proxy, redirects, or anonymous port."""

    def __init__(self, host, port, context):
        if host != '127.0.0.1' or type(port) is not int or not 1 <= port <= 65535:
            raise ValueError('loopback Frigate API required')
        if not isinstance(context, ssl.SSLContext):
            raise ValueError('explicit TLS context required')
        self.host = host
        self.port = port
        self.context = context

    def profile(self, cookie):
        connection = http.client.HTTPSConnection(
            self.host, self.port, timeout=2, context=self.context)
        try:
            connection.request('GET', '/api/profile', headers={
                'Cookie': cookie, 'Accept': 'application/json',
                'Accept-Encoding': 'identity', 'X-Cache-Bypass': '1'})
            response = connection.getresponse()
            if response.status in (401, 403):
                raise Denied(401)
            if response.status != 200:
                raise Denied(503)
            headers = response.getheaders()
            if len(headers) > 32 or sum(len(key) + len(value) for key, value in headers) > 8192:
                raise Denied(502)
            if (response.getheader('Content-Type', '').split(';')[0] != 'application/json'
                    or response.getheader('Content-Encoding', 'identity') != 'identity'):
                raise Denied(502)
            if response.getheader('Content-Length') and int(response.getheader('Content-Length')) > CAP:
                raise Denied(502)
            cookies = [value for key, value in headers if key.lower() == 'set-cookie']
            if len(cookies) > 1:
                raise Denied(502)
            raw = bytearray()
            deadline = time.monotonic() + 2
            while True:
                if response.fp is None:
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise Denied(503)
                sock = connection.sock or response.fp.raw._sock
                sock.settimeout(remaining)
                chunk = response.read1(min(8192, CAP + 1 - len(raw)))
                if not chunk:
                    break
                raw.extend(chunk)
                if len(raw) > CAP:
                    raise Denied(502)
            return decode(raw), refresh_cookie(cookies[0] if cookies else None)
        except Denied:
            raise
        except (OSError, ValueError, http.client.HTTPException):
            raise Denied(503)
        finally:
            connection.close()


class Bridge:
    def __init__(self, authority, bindings):
        self.authority = authority
        self.bindings = bindings

    def session(self, cookie, selected=None):
        cookie = credential(cookie)
        try:
            profile, refresh = self.authority.profile(cookie)
        except Denied:
            raise
        except Exception:
            raise Denied(503)
        try:
            if type(profile) is not dict or set(profile) != {'username', 'role', 'allowed_cameras'}:
                raise ValueError()
            user, role, allowed = (profile[key] for key in ('username', 'role', 'allowed_cameras'))
            if not isinstance(user, str) or not ID.fullmatch(user) or user.lower() == 'anonymous':
                raise ValueError()
            if not isinstance(role, str) or not ID.fullmatch(role):
                raise ValueError()
            if (type(allowed) is not list or len(allowed) > 256
                    or any(not isinstance(camera, str) or not ID.fullmatch(camera) for camera in allowed)
                    or len(set(allowed)) != len(allowed)):
                raise ValueError()
            refresh = refresh_cookie(refresh)
            code, document = self.bindings()
            if (code != 200 or type(document) is not dict or set(document) != {'cameras'}
                    or type(document['cameras']) is not list or len(document['cameras']) > 16):
                raise ValueError()
            rows, seen = [], set()
            for row in document['cameras']:
                if type(row) is not dict:
                    raise ValueError()
                camera = row.get('camera_id')
                if not isinstance(camera, str) or not ID.fullmatch(camera) or camera in seen:
                    raise ValueError()
                seen.add(camera)
                if row.get('coverage_state') not in ('CONFIGURED', 'NOT_CONFIGURED', 'CONFIGURATION_AMBIGUOUS'):
                    raise ValueError()
                if row['coverage_state'] == 'CONFIGURED':
                    guard = row.get('binding_guard')
                    if not isinstance(guard, str) or not re.fullmatch(r'[0-9a-f]{64}', guard):
                        raise ValueError()
                    if camera in allowed:
                        rows.append({'camera_id': camera, 'display_name': camera})
            rows.sort(key=lambda row: row['camera_id'])
            if selected is not None and (not isinstance(selected, str) or not ID.fullmatch(selected)
                                          or selected not in {row['camera_id'] for row in rows}):
                raise Denied(403)
            return {
                'principal': user, 'cameras': rows, 'selected_camera': selected,
                'operations_available': False,
            }, refresh
        except Denied:
            raise
        except Exception:
            raise Denied(502)


class Access(Bridge):
    def session(self, cookie, selected=None):
        document, refresh = super().session(cookie, selected)
        return document | {'operations_available': True}, refresh

    def grant(self, cookie, camera, guard=None):
        document, refresh = self.session(cookie, camera)
        code, data = self.bindings()
        if code != 200:
            raise Denied(503)
        rows = [row for row in data['cameras']
                if row.get('camera_id') == camera and row.get('coverage_state') == 'CONFIGURED']
        if (len(rows) != 1 or not isinstance(rows[0].get('binding_guard'), str)
                or not re.fullmatch(r'[0-9a-f]{64}', rows[0]['binding_guard'])):
            raise Denied(403)
        actual = rows[0]['binding_guard']
        if guard is not None and actual != guard:
            raise Denied(403)
        return document['principal'], actual, refresh


class Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 4

    def __init__(self, address, handler):
        if address[0] != '127.0.0.1':
            raise ValueError('historical listener must bind loopback')
        self.slots = threading.BoundedSemaphore(4)
        super().__init__(address, handler)

    def process_request(self, request, address):
        if not self.slots.acquire(False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.slots.release()

    def get_request(self):
        stream, address = super().get_request()
        stream.settimeout(3)
        return stream, address


class SessionHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        return

    def do_GET(self):
        try:
            if (len(self.path) > 256 or len(self.headers) > 20
                    or sum(len(key) + len(value) for key, value in self.headers.items()) > 8192):
                raise Denied(400)
            if self.headers.get_all('Host') != [self.server.expected_host]:
                raise Denied(403)
            if self.headers.get_all('Origin') not in (None, [self.server.expected_origin]):
                raise Denied(403)
            if any(self.headers.get(key) is not None for key in FORBIDDEN_HEADERS):
                raise Denied(403)
            if self.headers.get('Content-Length') not in (None, '0'):
                raise Denied(400)
            url = urlsplit(self.path)
            if url.path != '/session' or url.scheme or url.netloc or url.fragment:
                raise Denied(404)
            query = parse_qs(url.query, strict_parsing=True, keep_blank_values=True, max_num_fields=1)
            if set(query) - {'camera'} or any(len(value) != 1 for value in query.values()):
                raise Denied(400)
            if len(self.headers.get_all('Cookie') or []) != 1:
                raise Denied()
            value, cookie = self.server.bridge.session(
                self.headers['Cookie'], query.get('camera', [None])[0])
            self.reply(200, value, cookie)
        except Denied as exc:
            self.reply(exc.status, {'error': {
                401: 'authentication_required', 403: 'not_authorized', 404: 'not_available',
            }.get(exc.status, 'unavailable')})
        except Exception:
            self.reply(400, {'error': 'invalid_request'})

    def reply(self, status, value, cookie=None):
        raw = json.dumps(value, separators=(',', ':'), allow_nan=False).encode()
        if len(raw) > 8192:
            status, raw, cookie = 502, b'{"error":"unavailable"}', None
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Connection', 'close')
        if cookie:
            self.send_header('Set-Cookie', cookie)
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        self.reply(405, {'error': 'operations_disabled'})

    do_PUT = do_DELETE = do_PATCH = do_POST


class OperationsHandler(SessionHandler):
    """Legacy one-slot routes plus the session checks the window adapter reuses."""

    def validate(self, post=False):
        if (len(self.path) > 256 or len(self.headers) > 20
                or sum(len(key) + len(value) for key, value in self.headers.items()) > 8192):
            raise Denied(400)
        if self.headers.get_all('Host') != [self.server.expected_host]:
            raise Denied(403)
        origins = self.headers.get_all('Origin')
        if (post and origins != [self.server.expected_origin]) or (
                not post and origins not in (None, [self.server.expected_origin])):
            raise Denied(403)
        if post and self.headers.get_all('X-Nanexus-Request') != ['1']:
            raise Denied(403)
        if any(self.headers.get(key) is not None for key in FORBIDDEN_HEADERS):
            raise Denied(403)
        if len(self.headers.get_all('Cookie') or []) != 1:
            raise Denied(401)
        return self.headers['Cookie']

    def do_GET(self):
        if urlsplit(self.path).path == '/session':
            return super().do_GET()
        try:
            cookie = self.validate()
            url = urlsplit(self.path)
            if url.scheme or url.netloc or url.fragment or url.path not in ('/operation', '/result'):
                raise Denied(404)
            if self.headers.get('Content-Length') not in (None, '0'):
                raise Denied(400)
            query = parse_qs(url.query, strict_parsing=True, max_num_fields=1)
            if set(query) != {'request_id'} or len(query['request_id']) != 1:
                raise Denied(400)
            code, raw, refresh = self.server.operations.observe(
                cookie, query['request_id'][0], url.path == '/result')
            self.reply_raw(code, raw, refresh)
        except Denied as exc:
            self.reply(exc.status, {'error': 'operation_unavailable'})
        except Exception:
            self.reply(400, {'error': 'invalid_request'})

    def do_POST(self):
        try:
            cookie = self.validate(True)
            if self.path not in ('/verify', '/cancel'):
                raise Denied(404)
            if (self.headers.get_all('Content-Type') != ['application/json']
                    or len(self.headers.get_all('Content-Length') or []) != 1):
                raise Denied(400)
            size = int(self.headers['Content-Length'])
            if not 0 < size <= 1024:
                raise Denied(413)
            raw = self.rfile.read(size)
            if len(raw) != size:
                raise Denied(400)
            data = decode(raw)
            if self.path == '/verify':
                value, refresh = self.server.operations.submit(cookie, data)
                code = 202
            else:
                if type(data) is not dict or set(data) != {'request_id'}:
                    raise Denied(400)
                value, refresh = self.server.operations.cancel(cookie, data['request_id'])
                code = 200
            self.reply(code, value, refresh)
        except Denied as exc:
            self.reply(exc.status, {'error': 'operation_unavailable'})
        except Exception:
            self.reply(400, {'error': 'invalid_request'})

    def reply_raw(self, status, raw, cookie=None):
        if len(raw) > (4 << 20):
            status, raw, cookie = 413, b'{"error":"limit"}', None
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Connection', 'close')
        self.send_header('X-Content-Type-Options', 'nosniff')
        if cookie:
            self.send_header('Set-Cookie', cookie)
        self.end_headers()
        self.wfile.write(raw)

    def reply(self, status, value, cookie=None):
        self.reply_raw(status, json.dumps(value, separators=(',', ':'), allow_nan=False).encode(), cookie)

    def do_PUT(self):
        self.reply(405, {'error': 'operation_unavailable'})

    do_DELETE = do_PATCH = do_PUT
