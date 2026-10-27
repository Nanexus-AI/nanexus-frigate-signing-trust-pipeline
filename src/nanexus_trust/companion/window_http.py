"""Opt-in native-authority HTTP adapter. No listener, persistence or media IO.

Install handler(existing_UI3B_Handler) on the accepted bridge server and attach
WindowHTTP(access, coordinator, service._hv2_camera) as server.window_operations.
Existing handlers, authority and consume-once operations remain unchanged.
"""
import json
import os
import re
import secrets
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from decimal import Decimal
from urllib.parse import urlsplit, parse_qs, urlencode
from ..historical_v2 import contracts as a, wire, composition as c
from ..historical_v2.windows import WindowCoordinator, TTL
from ..historical_v2.prototype import PrototypeFailure, DirectSessionApiProfile
from ..frigate.historical_v2 import ProviderFailure
from ..historical_v2.catalog_gaps import project_catalog_gaps
from ..historical_v2.query_coverage import coverage_document
from ..historical_v2.window_results import summary_bytes

RID = re.compile(r'[0-9a-f]{32}\Z')
CAMERA = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z')

class Denied(Exception):
    def __init__(self,status,running=None):self.status=status;self.running=running

# One progress poll is 1s. Keep the finished slot long enough for that read,
# then drop it without a browser /release. This is not a job scheduler.
CLEANUP_GRACE = 3


def recording_zone(name):
    """Explicit IANA zone. Not the caller machine's local zone."""
    if type(name) is not str or not re.fullmatch(r'[A-Za-z0-9_+-][A-Za-z0-9._+\-/]{0,63}', name):
        raise ValueError('zone')
    if '..' in name or name in ('localtime', 'posixrules'):
        raise ValueError('zone')
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        raise ValueError('zone')


def catalog_recording_dates(path, camera, month, zone_name):
    """Calendar dates in one explicit zone that overlap Catalog recordings.

    Dates come from Catalog Unix timestamps. A returned date means retained
    Catalog coverage exists. It is not a signing result and does not mean the
    day is complete.
    """
    if type(camera) is not str or not CAMERA.fullmatch(camera):
        raise ValueError('camera')
    if type(month) is not str or not re.fullmatch(r'\d{4}-\d{2}', month):
        raise ValueError('month')
    year, mon = int(month[:4]), int(month[5:])
    if not 1970 <= year <= 2100 or not 1 <= mon <= 12:
        raise ValueError('month')
    zone = recording_zone(zone_name)
    # Pad the SQL window so a daylight-saving offset cannot hide a row. The
    # zone conversion below decides which dates are actually inside the month.
    left = datetime(year, mon, 1, tzinfo=timezone.utc).timestamp() - 14 * 3600
    next_year, next_mon = (year + 1, 1) if mon == 12 else (year, mon + 1)
    right = datetime(next_year, next_mon, 1, tzinfo=timezone.utc).timestamp() + 14 * 3600
    fd = os.open(path, os.O_RDONLY)
    try:
        db = sqlite3.connect(f'file:/proc/self/fd/{fd}?mode=ro&immutable=1', uri=True, timeout=0)
        db.execute('PRAGMA query_only=ON')
        rows = db.execute(
            'SELECT start_time,end_time FROM recordings WHERE camera=? AND start_time<? AND end_time>? '
            'ORDER BY start_time,end_time LIMIT 4001',
            (camera, right, left)).fetchall()
    finally:
        os.close(fd)
    complete = len(rows) <= 4000
    dates = set()

    def zoned_date(stamp):
        return datetime.fromtimestamp(float(stamp), timezone.utc).astimezone(zone).date()

    for raw_start, raw_end in rows[:4000]:
        if type(raw_start) not in (int, float) or type(raw_end) not in (int, float) or not raw_end > raw_start:
            continue
        day, final = zoned_date(raw_start), zoned_date(float(raw_end) - 1e-6)
        for _ in range(40):
            if day.year == year and day.month == mon:
                dates.add(day.isoformat())
            if day >= final:
                break
            day += timedelta(days=1)
    return dict(schema='nanexus-hv2-catalog-days/1', camera=camera, month=month, zone=zone_name,
                dates=sorted(dates), complete=complete,
                meaning='catalog_recordings_present')


def _month(month):
    if type(month) is not str or not re.fullmatch(r'\d{4}-\d{2}', month):
        raise ValueError('month')
    year, mon = int(month[:4]), int(month[5:])
    if not 1970 <= year <= 2100 or not 1 <= mon <= 12:
        raise ValueError('month')
    return year, mon


def session_recording_dates(session, cookie, owner, camera, native, month, zone_name):
    """Current Frigate recording dates for one camera, read once with the caller's session.

    Frigate groups recordings by start time in the requested zone. A returned
    date means retained recordings exist now. It is not a signing result and
    does not mean the day is complete.
    """
    year, mon = _month(month)
    recording_zone(zone_name)
    if type(native) is not str or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', native):
        raise ValueError('camera')
    session.authorize(cookie, owner, native, time.monotonic() + 2)
    raw = session.get(cookie, '/api/recordings/summary?' + urlencode({'cameras': native, 'timezone': zone_name}),
                      64 << 10, time.monotonic() + 2)
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise ValueError('duplicate date')
            result[key] = value
        return result
    days = json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    if type(days) is not dict:
        raise ValueError('summary')
    dates = set()
    for key, value in days.items():
        if value is not True or type(key) is not str or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', key):
            raise ValueError('summary')
        day = datetime.strptime(key, '%Y-%m-%d').date()
        if day.year == year and day.month == mon:
            dates.add(key)
    return dict(schema='nanexus-hv2-catalog-days/1', camera=camera, month=month, zone=zone_name,
                dates=sorted(dates), complete=True, meaning='catalog_recordings_present')


def finding(entry,row,*,owner=None,camera=None,request_id=None):
    """Only called after compose validation AND atomic bounded Bundle.add_file."""
    from ..historical_v2.video_signing import recognize
    scoped=recognize(entry,row,owner=owner,camera=camera,request_id=request_id)
    if scoped is not None:return scoped
    counts=row['finalized_counts']
    if counts.get('INTEGRITY_NEGATIVE',0):return 'RED'
    if (not counts.get('VERIFIED_SIGNING',0) or any(v for k,v in counts.items() if k!='VERIFIED_SIGNING')
            or row['unresolved'] or row['unprocessed'] or row['missing_evidence']):return 'AMBER'
    page=wire.loads(c.canonical(entry['result']['page']))
    observations=list(page.observations)
    if page.next_cursor:
        import base64
        objects={o['id']:o for o in entry['result']['objects']}
        raw=base64.b64decode(objects[page.next_cursor]['data'],validate=True)
        observations.extend(wire.loads(c.canonical(o)) for o in json.loads(raw)['observations'])
    positive=(observations and page.lifecycle is a.Lifecycle.COMPLETE and page.output_complete
              and not row['unresolved'] and not row['unprocessed'] and not row['missing_evidence']
              and all(o.state is a.SigningState.VERIFIED_SIGNING and o.finality is a.Finality.FINALIZED
                      and o.context_quality is a.Quality.SATISFIED and o.trust_profile for o in observations))
    return 'GREEN' if positive else 'AMBER'


class WindowHTTP:
    def __init__(self,access,coordinator,resolve_camera,*,clock=time.monotonic):
        if (not isinstance(coordinator,WindowCoordinator) or coordinator._observer is not None
                or coordinator._record is not None or coordinator._closed):
            raise TypeError('dedicated opt-in window coordinator required')
        self.access=access;self.coordinator=coordinator;self.resolve=resolve_camera;self.clock=clock
        self.lock=threading.RLock();self.slot=None;self.closed=False
        coordinator._observer=self._publish

    def _grant(self,cookie,camera,guard=None):
        owner,actual,refresh=self.access.grant(cookie,camera,guard)
        return owner,actual,self.resolve(camera,actual),refresh

    def _expire(self):
        if self.slot and not self.slot['running'] and self.clock()>=self.slot['expires']:
            self._disarm(self.slot)
            self._release(self.slot)

    def submit(self,cookie,data):
        if type(data) is not dict or set(data)!={'camera','start','end'}:raise Denied(400)
        camera=data['camera']
        if type(camera) is not str or not CAMERA.fullmatch(camera):raise Denied(400)
        try:
            a.Interval(data['start'],data['end'])
            if Decimal(data['end'])-Decimal(data['start'])>1800:raise Denied(413)
        except Denied:raise
        except Exception:raise Denied(400)
        owner,guard,admitted,refresh=self._grant(cookie,camera)
        with self.lock:
            self._expire()
            if self.closed:raise Denied(503)
            if self.slot is not None:raise Denied(409,self.slot['running'] is True)
            record=dict(owner=owner,camera=camera,guard=guard,admitted=admitted,id=secrets.token_hex(16),
                        running=True,state='RUNNING',files=[],reason=None,result=None,cancel=threading.Event(),
                        expires=None,thread=None,evidence_available=False,request_start=data['start'],request_end=data['end'],
                        catalog_gaps=None,catalog_gaps_state='unavailable')
            self.slot=record
            thread=threading.Thread(target=self._work,args=(record,cookie,data['start'],data['end']),daemon=True,name='historical-window-http')
            record['thread']=thread
            try:thread.start()
            except Exception:self.slot=None;raise Denied(503)
            return dict(request_id=record['id'],state='RUNNING'),refresh

    def _publish(self,event,value,row):
        with self.lock:
            record=self.slot
            if record is None or not record['running']:raise PrototypeFailure('CANCELLED')
            if event=='plan':
                if record['files'] or len(value)>256:raise ValueError('plan binding')
                planned=[dict(file_id=str(i),recording=item.item_id,position=i,
                    metadata_interval=dict(start=item.metadata_interval.start,end=item.metadata_interval.end),
                    state='PENDING',finding=None) for i,item in enumerate(value)]
                if len(c.canonical(planned))+8192>256<<10:raise ValueError('progress cap')
                if len({item.item_id for item in value})!=len(value):raise ValueError('duplicate plan recording')
                record['generations']={item.item_id:item.generation for item in value}
                record['files']=planned
                record['planned']=True
                record['catalog_gaps'],record['catalog_gaps_state']=self._catalog_gaps(record,value)
            else:
                matches=[i for i,f in enumerate(record['files']) if f['recording']==row['recording']]
                if len(matches)!=1:raise ValueError('progress recording')
                index=matches[0]
                if (record['files'][index]['state']!='PENDING' or value['recording']!=row['recording']
                        or value['generation']!=row['generation']
                        or record['generations'][row['recording']]!=row['generation']):
                    raise ValueError('progress generation or duplicate')
                update=dict(record['files'][index],state='COMPLETED',finding=finding(value,row,owner=record['owner'],camera=record['admitted'],request_id=record['id']),
                            finalized_counts=dict(row['finalized_counts']),unresolved=row['unresolved'],
                            unprocessed=row['unprocessed'],missing_evidence=row['missing_evidence'])
                candidate=list(record['files']);candidate[index]=update
                if len(c.canonical(candidate))+8192>256<<10:raise ValueError('progress cap')
                record['files']=candidate

    def _catalog_gaps(self,record,items):
        """Plan-time projection. A failure stays unavailable and does not change files."""
        try:
            intervals=[]
            for item in items:
                iv=item.metadata_interval
                intervals.append(None if iv is None else (iv.start,iv.end))
            gaps=project_catalog_gaps(record['camera'],record['request_start'],record['request_end'],intervals)
            if len(c.canonical({'files':record['files'],'catalog_gaps':gaps}))+8192>256<<10:
                return None,'unavailable'
            return gaps,'available'
        except Exception:
            return None,'unavailable'

    def _work(self,record,cookie,start,end):
        last=[0.]
        def permission():
            if record['cancel'].is_set():raise PrototypeFailure('CANCELLED')
            if self.resolve(record['camera'],record['guard'])!=record['admitted']:raise PrototypeFailure('UNAUTHORIZED')
            now=self.clock()
            if now-last[0]>=1:
                owner,guard,admitted,_=self._grant(cookie,record['camera'],record['guard']);last[0]=now
                if owner!=record['owner'] or admitted!=record['admitted']:raise PrototypeFailure('UNAUTHORIZED')
        result=None;state='EXECUTION_FAILED';reason='Verification stopped after an operational failure.'
        try:
            result=self.coordinator.run(record['owner'],record['admitted'],start,end,record['id'],permission,
                                        cookie=cookie if type(self.coordinator.profile) is DirectSessionApiProfile else None)
            last[0]=float('-inf');permission()
            state='COMPLETE' if result['status']=='RESULT' else result['status']
            reason=None if state=='COMPLETE' else result.get('reason','Verification stopped; remaining files were not processed.')
        except Exception:
            state='CANCELLED' if record['cancel'].is_set() else 'EXECUTION_FAILED'
            reason='Verification cancelled.' if state=='CANCELLED' else 'Verification stopped after an operational or authorization failure.'
        finally:
            # Coordinator.run has terminated/reaped before adapter execution ends.
            with self.lock:
                if self.slot is record:
                    record.update(running=False,state=state,reason=reason,expires=self.clock()+TTL,
                                  evidence_available=result is not None and self.coordinator._record is not None)
                    if result is not None:
                        record['result']={k:result[k] for k in ('status','processed','unprocessed','input','retained_bytes') if k in result}
                    if not self.closed:
                        self._arm_cleanup(record)

    def _disarm(self,record):
        timer=record.get('cleanup')
        if timer is not None:
            timer.cancel()
            record['cleanup']=None

    def _arm_cleanup(self,record):
        """Release this finished slot after a short read window. A newer slot is left alone."""
        self._disarm(record)
        timer=threading.Timer(CLEANUP_GRACE,self._cleanup,args=(record,))
        timer.daemon=True
        record['cleanup']=timer
        timer.start()

    def _cleanup(self,record):
        with self.lock:
            if self.closed or self.slot is not record or record['running']:
                return False
            self._disarm(record)
            self._release(record)
            return True

    def _admit(self,cookie,rid):
        if type(rid) is not str or not RID.fullmatch(rid):raise Denied(400)
        with self.lock:
            self._expire();r=self.slot
            if r is None or r['id']!=rid:raise Denied(404)
            owner,guard,admitted,refresh=self._grant(cookie,r['camera'],r['guard'])
            if owner!=r['owner'] or admitted!=r['admitted']:raise Denied(403)
            return r,refresh

    def catalog_days(self,cookie,camera,month,zone_name):
        if type(camera) is not str or not CAMERA.fullmatch(camera):raise Denied(400)
        owner,guard,admitted,refresh=self._grant(cookie,camera)
        if owner is None or admitted is None:raise Denied(403)
        profile=self.coordinator.profile
        try:
            if type(profile) is DirectSessionApiProfile:
                value=session_recording_dates(profile.session,cookie,owner,camera,admitted.native_key,month,zone_name)
            else:
                value=catalog_recording_dates(profile.snapshot.path,camera,month,zone_name)
        except ProviderFailure as exc:
            raise Denied(403 if exc.reason=='access-denied-or-revoked' else 503)
        except ValueError:raise Denied(400)
        except Exception:raise Denied(503)
        return value,refresh

    def observe(self,cookie,rid,result=False):
        r,refresh=self._admit(cookie,rid)
        with self.lock:
            if self.slot is not r:raise Denied(404)
            if result and r['running']:raise Denied(409)
            available=r['evidence_available'] and self.coordinator._record is not None
            value=dict(schema='nanexus-hv2-window-progress/1',request_id=rid,camera=r['camera'],
                       state=r['state'],reason=r['reason'],files=r['files'],summary=r['result'],
                       summary_only=True,evidence_available=available,origin='HOLD',continuity='UNKNOWN',coverage='UNKNOWN',
                       catalog_gaps=r['catalog_gaps'],catalog_gaps_state=r['catalog_gaps_state'])
            coverage=self._query_coverage(r)
            if coverage is not None:value['query_coverage']=coverage
            return json.loads(summary_bytes(value)),refresh

    def _query_coverage(self,record):
        """Live-session timeline partition of the user query. Not signing coverage.

        The established document requires a dataset interval. Session jobs have no
        corpus manifest, so dataset equals the query and outside is not emitted.
        """
        if type(self.coordinator.profile) is not DirectSessionApiProfile:return None
        if not record.get('planned') or not record['files']:return None
        spans=[]
        for item in record['files']:
            iv=item.get('metadata_interval') or {}
            start,end=iv.get('start'),iv.get('end')
            if start is None or end is None:continue
            finding=item.get('finding') if item.get('state')=='COMPLETED' else 'PENDING'
            spans.append((start,end,item.get('recording'),finding))
        return coverage_document(record['request_start'],record['request_end'],
                                 record['request_start'],record['request_end'],spans,True)

    def cancel(self,cookie,rid):
        r,refresh=self._admit(cookie,rid)
        with self.lock:
            if self.slot is not r or not r['running']:raise Denied(409)
            r['cancel'].set()
            if self.coordinator._record:
                try:self.coordinator.cancel_owned(r['owner'],r['admitted'],rid,lambda:None)
                except PrototypeFailure as e:
                    if e.code!='UNAVAILABLE':raise
            return dict(request_id=rid,status='CANCELLATION_REQUESTED'),refresh

    def _release(self,r):
        if r['running']:raise Denied(409)
        if self.coordinator._record:
            self.coordinator.release(r['owner'],r['admitted'],r['id'],lambda:None)
        self.slot=None

    def release(self,cookie,rid):
        if type(rid) is not str or not RID.fullmatch(rid):raise Denied(400)
        with self.lock:
            current=self.slot
            stale=current is None or current['id']!=rid
        if stale:
            # Already gone, or a newer job owns the slot. Do not touch that job.
            _profile,refresh=self.access.session(cookie)
            return dict(request_id=rid,released=True),refresh
        try:
            r,refresh=self._admit(cookie,rid)
        except Denied as e:
            if e.status!=404:raise
            return dict(request_id=rid,released=True),None
        with self.lock:
            if self.slot is not r:
                return dict(request_id=rid,released=True),refresh
            if r['running']:raise Denied(409)
            self._disarm(r)
            self._release(r)
        r['thread'].join(1)
        return dict(request_id=rid,released=True),refresh

    def close(self):
        with self.lock:
            self.closed=True;r=self.slot
            if r:
                r['cancel'].set()
                self._disarm(r)
        self.coordinator.close()
        if r:r['thread'].join(6)
        if r and r['thread'].is_alive():raise RuntimeError('window worker still stopping')
        with self.lock:self.slot=None


def handler(accepted_handler):
    """Reuse accepted Host/Origin/CSRF/cookie checks and all legacy routes."""
    class Handler(accepted_handler):
        def _window(self,post):
            url=urlsplit(self.path)
            if not url.path.startswith('/window/'):return False
            try:
                cookie=self.validate(post)
                if url.scheme or url.netloc or url.fragment:raise Denied(400)
                adapter=self.server.window_operations
                if post:
                    if self.headers.get_all('Content-Type')!=['application/json'] or len(self.headers.get_all('Content-Length') or [])!=1:raise Denied(400)
                    n=int(self.headers['Content-Length'])
                    if not 0<n<=1024 or url.query:raise Denied(400)
                    def pairs(items):
                        d={}
                        for k,v in items:
                            if k in d:raise Denied(400)
                            d[k]=v
                        return d
                    raw=self.rfile.read(n)
                    if len(raw)!=n:raise Denied(400)
                    data=json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda _:(_ for _ in ()).throw(Denied(400)))
                    if url.path=='/window/verify':value,refresh=adapter.submit(cookie,data);code=202
                    else:
                        if type(data) is not dict or set(data)!={'request_id'}:raise Denied(400)
                        action={'/window/cancel':adapter.cancel,'/window/release':adapter.release}.get(url.path)
                        if action is None:raise Denied(404)
                        value,refresh=action(cookie,data['request_id']);code=200
                else:
                    if self.headers.get('Content-Length') not in (None,'0'):raise Denied(400)
                    if url.path=='/window/status' and 'month' in url.query:
                        q=parse_qs(url.query,strict_parsing=True,max_num_fields=3)
                        if set(q)!={'camera','month','zone'} or any(len(v)!=1 for v in q.values()):raise Denied(400)
                        value,refresh=adapter.catalog_days(cookie,q['camera'][0],q['month'][0],q['zone'][0])
                        self.reply(200,value,refresh)
                        return True
                    if url.path not in ('/window/status','/window/progress','/window/result'):raise Denied(404)
                    q=parse_qs(url.query,strict_parsing=True,max_num_fields=1)
                    if set(q)!={'request_id'} or len(q['request_id'])!=1:raise Denied(400)
                    value,refresh=adapter.observe(cookie,q['request_id'][0],url.path=='/window/result');code=200
                self.reply(code,value,refresh)
            except Exception as e:
                status=getattr(e,'status',None)
                if isinstance(e,PrototypeFailure):status={'UNAUTHORIZED':403,'UNAVAILABLE':404,'BUSY':409,'LIMIT':413}.get(e.code,503)
                body={'error':'window_operation_unavailable'}
                running=getattr(e,'running',None)
                if status==409 and type(running) is bool:body['running']=running
                self.reply(status if status in (400,401,403,404,409,413,503) else 400,body)
            return True
        def do_GET(self):
            if not self._window(False):super().do_GET()
        def do_POST(self):
            if not self._window(True):super().do_POST()
    return Handler


def install(server,access,service,coordinator):
    """Explicitly opt an existing authenticated bridge listener into window routes.

    Caller retains existing bind/TLS/origin/auth configuration. Ordinary server
    shutdown must precede server_close; the latter also stops this owned worker.
    """
    if hasattr(server,'window_operations'):raise ValueError('already installed')
    adapter=WindowHTTP(access,coordinator,service._hv2_camera)
    server.window_operations=adapter
    server.RequestHandlerClass=handler(server.RequestHandlerClass)
    original_close=server.server_close
    def close():
        try:adapter.close()
        finally:original_close()
    server.server_close=close
    return adapter
