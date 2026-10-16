"""Fixed Historical job/evidence adapter; no media paths or user credentials."""
import hmac
import json
import re
import threading
from urllib.parse import parse_qs, urlsplit

from ..contracts import Interval, identifier
from .jobs import opaque
from .transport import LiveTransport, LiveHTTPServer, _Handler

MAX_JSON = 32768
MAX_DOWNLOAD = 8 * 1024 * 1024


def job_document(job):
    request = job['request']
    identifier(request['camera']); Interval(request['start'], request['end']); opaque(job['job_id'])
    if job['state'] not in ('accepted', 'queued', 'running', 'completed', 'interrupted', 'failed'):
        raise ValueError('job unavailable')
    result = job['result']
    document = {k: job[k] for k in ('job_id', 'state', 'stage', 'evidence_id')}
    identifier(document['stage'])
    document.update(request)
    document.update(verification=None, metadata_coverage=None, requested_coverage=None, context=None)
    if document['evidence_id'] is not None:
        opaque(document['evidence_id'])
    if job['state'] == 'completed':
        if result['verification'] not in ('VALID', 'PARTIAL', 'INVALID', 'UNKNOWN'):
            raise ValueError('result unavailable')
        if result['metadata_coverage'] not in ('FULL', 'NOT_FULL', 'INDETERMINATE') or result['requested_coverage'] not in ('FULL', 'INDETERMINATE'):
            raise ValueError('result unavailable')
        if result['request']['camera']['value'] != request['camera'] or result['request']['interval'] != {'start': request['start'], 'end': request['end']}:
            raise ValueError('result unavailable')
        document.update({k: result[k] for k in ('verification', 'metadata_coverage', 'requested_coverage')})
        context = result['context']
        if context is not None:
            if context['camera']['value'] != request['camera'] or not 1 <= len(context['intervals']) <= 128:
                raise ValueError('context unavailable')
            for span in context['intervals']:
                Interval(**span)
            document['context'] = context['intervals']
    if len(json.dumps(document).encode()) > MAX_JSON:
        raise ValueError('result limit')
    return document


class HistoricalTransport(LiveTransport):
    def __init__(self, service, token):
        super().__init__(service, token)
        self._download_slot = threading.Lock()

    def historical(self, method, target, authorization, body=b''):
        if type(authorization) is not str or not hmac.compare_digest(authorization.encode(), ('Bearer '+self._token).encode()):
            return 401, {'error': 'unauthorized'}
        try:
            if len(target) > 1024 or len(body) > 1024:
                raise ValueError()
            url = urlsplit(target)
            if url.scheme or url.netloc or url.fragment:
                raise ValueError()
            query = parse_qs(url.query, strict_parsing=True, keep_blank_values=True, max_num_fields=1)
            if set(query) != {'camera'} or len(query['camera']) != 1:
                raise ValueError()
            camera = identifier(query['camera'][0])
            self.service._camera(camera)
            if url.path == '/v1/historical/jobs' and method == 'POST':
                def pairs(items):
                    d = {}
                    for k,v in items:
                        if k in d: raise ValueError()
                        d[k] = v
                    return d
                data = json.loads(body, object_pairs_hook=pairs)
                if type(data) is not dict or set(data) != {'start', 'end'}:
                    raise ValueError()
                Interval(data['start'], data['end'])
                submitted = self.service.submit(camera, data['start'], data['end'])
                return 202, job_document(self.service.job(submitted['job_id']))
            match = re.fullmatch(r'/v1/historical/jobs/([0-9a-f]{32})(?:/evidence/([0-9a-f]{32})(/download)?)?', url.path)
            if not match or method != 'GET' or body:
                return 405, {'error': 'unsupported_request'}
            job = self.service.job(match[1])
            # Caller-supplied camera is never sufficient: compare durable association.
            if job['request']['camera'] != camera:
                return 404, {'error': 'unavailable'}
            document = job_document(job)
            if not match[2]:
                return 200, document
            if job['evidence_id'] != match[2] or job['state'] != 'completed':
                return 404, {'error': 'unavailable'}
            if match[3]:
                # D1 assembly has its own larger evidence bound; serialize it so
                # concurrent HTTP callers cannot multiply that memory footprint.
                if not self._download_slot.acquire(blocking=False):
                    return 503, {'error': 'evidence_busy'}
                try:
                    data = self.service.download(match[2])
                    if len(data) > MAX_DOWNLOAD:
                        return 413, {'error': 'evidence_download_limit'}
                    return 200, data
                finally:
                    self._download_slot.release()
            summary = self.service.evidence_summary(match[2])
            if summary['recognition'] not in ('SUPPORTED', 'UNKNOWN') or not re.fullmatch(r'[0-9a-f]{64}', summary['manifest_sha256']):
                raise ValueError()
            return 200, {'evidence_id': match[2], 'recognition': summary['recognition'], 'manifest_sha256': summary['manifest_sha256']}
        except KeyError:
            return 404, {'error': 'unavailable'}
        except (ValueError, TypeError, UnicodeError):
            return 400, {'error': 'invalid_or_unavailable_request'}
        except Exception:
            return 503, {'error': 'service_unavailable'}


class HistoricalHTTPServer(LiveHTTPServer):
    def __init__(self, address, transport):
        super().__init__(address, transport)
        self.RequestHandlerClass = _HistoricalHandler


class _HistoricalHandler(_Handler):
    def do_GET(self):
        if not self.path.startswith('/v1/historical/'):
            return super().do_GET()
        if len(self.headers) > 16 or sum(len(k)+len(v) for k,v in self.headers.items()) > 4096:
            return self._reply(400, {'error': 'invalid_request'})
        auth = self.headers.get_all('Authorization', [])
        lengths = self.headers.get_all('Content-Length', [])
        if self.headers.get('Transfer-Encoding') or len(lengths) > 1:
            return self._reply(400, {'error': 'invalid_request'})
        try:
            length = int(lengths[0]) if lengths else 0
            if not 0 <= length <= 1024 or (self.command == 'POST' and self.headers.get_content_type() != 'application/json'):
                raise ValueError()
            body = self.rfile.read(length)
            if len(body) != length: raise ValueError()
        except (ValueError, OSError):
            return self._reply(400, {'error': 'invalid_request'})
        code, document = self.server.transport.historical(self.command, self.path, auth[0] if len(auth)==1 else None, body)
        data = document if isinstance(document, bytes) else json.dumps(document, separators=(',', ':'), allow_nan=False).encode()
        if len(data) > (MAX_DOWNLOAD if isinstance(document, bytes) else MAX_JSON):
            code, data = 503, b'{"error":"response_limit"}'
        self.send_response(code)
        self.send_header('Content-Type', 'application/zip' if isinstance(document, bytes) else 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store, private')
        self.send_header('X-Content-Type-Options', 'nosniff')
        if isinstance(document, bytes):
            self.send_header('Content-Disposition', 'attachment; filename="historical-evidence.zip"')
        self.send_header('Connection', 'close')
        self.end_headers(); self.wfile.write(data); self.close_connection = True

    do_POST = do_GET
    do_PUT = do_GET
    do_PATCH = do_GET
    do_DELETE = do_GET
    do_HEAD = do_GET
    do_OPTIONS = do_GET
