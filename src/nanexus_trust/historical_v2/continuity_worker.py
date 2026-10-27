"""Finite, opt-in private rolling worker. No paths/media bytes cross into B.

One direct-read file buffer at a time; one child/extractor until context end.
"""
from dataclasses import dataclass, replace
import hashlib
import os
import signal
import stat
import subprocess
import time
from pathlib import Path
from .continuity_protocol import Decoder, encode, sha, CONFIG_DIGEST, ProtocolError
from .continuity_updates import Reassembly, token_hex
from .continuity import Rolling, require


@dataclass(frozen=True)
class Outcome:
    state: str
    error: str | None
    pid: int | None
    reaped: bool
    consumed: int
    published: tuple
    seconds: float
    exit_code: int | None
    metrics: dict | None = None


def _read_fd(fd, size):
    blob = bytearray()
    while len(blob) < size:
        chunk = os.read(fd, size - len(blob))
        if not chunk:
            break
        blob.extend(chunk)
    return bytes(blob)


def admit_worker_executable(executable, expected_sha256):
    """Hash one explicit regular file. No directory search, environment lookup, or fallback."""
    require(type(expected_sha256) is str and len(expected_sha256) == 64
            and all(c in '0123456789abcdef' for c in expected_sha256), 'product-worker-identity')
    path = Path(executable)
    require(path.is_absolute(), 'product-worker-identity')
    try:
        info = path.lstat()
    except OSError as exc:
        raise ProtocolError('product-worker-identity') from exc
    require(stat.S_ISREG(info.st_mode) and not stat.S_ISLNK(info.st_mode), 'product-worker-identity')
    try:
        resolved = path.resolve(strict=True)
        fd = os.open(resolved, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    except OSError as exc:
        raise ProtocolError('product-worker-identity') from exc
    try:
        current = os.fstat(fd)
        require(stat.S_ISREG(current.st_mode) and 0 < current.st_size <= 8 << 20, 'product-worker-identity')
        require((current.st_dev, current.st_ino) == (info.st_dev, info.st_ino), 'product-worker-identity')
        blob = _read_fd(fd, current.st_size)
        require(len(blob) == current.st_size and sha(blob) == expected_sha256, 'product-worker-identity')
        again = os.fstat(fd)
        require((again.st_dev, again.st_ino, again.st_size, again.st_mtime_ns)
                == (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns), 'product-worker-identity')
    finally:
        os.close(fd)
    return resolved, (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns)


def confirm_worker_executable(path, identity):
    """Recheck the inode immediately before exec. This narrows, but does not close, pathname TOCTOU."""
    try:
        info = Path(path).lstat()
    except OSError as exc:
        raise ProtocolError('product-worker-identity') from exc
    require(stat.S_ISREG(info.st_mode) and not stat.S_ISLNK(info.st_mode), 'product-worker-identity')
    require((info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns) == identity, 'product-worker-identity')


def run_context(executable,plan,request,owner,attempt,trust,load,publish,check,*,timeout=45,_fault='normal',worker_sha256=None):
    """Trusted caller supplies admitted Plan and safe direct Reader callback.

    load(index, check) returns (original receipt, tuple[Nal], catalog metadata)
    only AFTER post-read consistency checks. No automatic retries/fallback.
    """
    product=getattr(plan,'product',False)
    require(.05<=timeout<=(270 if product else 45) and _fault in ('normal','check'),'time-or-fault')
    require(type(trust) is bytes and 0<len(trust)<=32768 and sha(trust)==plan.trust_sha256,'context-trust')
    spec=replace(plan.attempt(request,owner,attempt),trust_bytes=len(trust))
    committed=[]
    def commit(entry):publish(entry);committed.append(entry['recording'] if product else entry)
    if product:
        from .rolling_product import ProductRolling
        rolling=ProductRolling(plan,request,spec,commit)
    else:rolling=Rolling(plan,request,spec,commit)
    start=time.monotonic();deadline=start+timeout
    process=None;decoder=Decoder();assembly=Reassembly(32768 if product else 4096);sent=received=sendseq=recvseq=0;last=start;consumed=0;state='FAILED';error=None;reaped=False
    def guard():
        check()
        if time.monotonic()>deadline:raise TimeoutError('context-deadline')
    try:
        guard()
        if product:
            from .rolling_product import WORKER_SHA256
            resolved,identity=admit_worker_executable(executable,WORKER_SHA256 if worker_sha256 is None else worker_sha256)
            confirm_worker_executable(resolved,identity)
            argv0=str(resolved)
        else:
            argv0=str(Path(executable).resolve())
        process=subprocess.Popen([argv0,str(os.getpid()),_fault],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True,close_fds=True,env={'LANG':'C','LC_ALL':'C'})
        for f in (process.stdin,process.stdout,process.stderr):os.set_blocking(f.fileno(),False)
        stderr=0
        def pump():
            nonlocal received,stderr
            guard()
            if time.monotonic()-last>1:raise TimeoutError('feed-no-progress')
            for stream in (process.stdout,process.stderr):
                try:b=os.read(stream.fileno(),min(16384,(2048+65536+4-len(decoder.buffer))) if stream is process.stdout else 16384)
                except BlockingIOError:continue
                if not b:
                    if stream is process.stdout and process.poll() is not None:raise EOFError('worker-exit')
                    continue
                if stream is process.stderr:
                    stderr+=len(b);require(stderr<=65536,'stderr-cap')
                else:
                    received+=len(b);require(received<=(96<<20 if product else 10<<20),'wire-output-cap');decoder.feed(b)
        def send(kind,payload=b'',**fields):
            nonlocal sendseq,sent,last
            guard();data=encode(kind,spec.attempt,sendseq,payload,**fields);sendseq+=1;sent+=len(data);require(sent<=(320<<20 if product else 40<<20),'wire-input-cap');at=0
            while at<len(data):
                pump()
                try:n=os.write(process.stdin.fileno(),data[at:])
                except BlockingIOError:n=0
                if n:at+=n;last=time.monotonic()
                else:time.sleep(.001)
        def message():
            nonlocal recvseq,last
            while True:
                guard();value=decoder.pop()
                if value:
                    h,p=value
                    require(h['seq']==recvseq and h['attempt']==('startup' if recvseq==0 else spec.attempt),'worker-binding')
                    recvseq+=1;last=time.monotonic();return h,p
                pump();time.sleep(.001)
        def receive(kind):
            h,p=message();require(h['type']==kind and not p,'worker-message');return h
        def update(phase):
            nonlocal consumed
            while True:
                h,p=message();require(h['type'] in ('UPDATE_BEGIN','UPDATE_CHUNK','UPDATE_END'),'worker-update')
                raw=assembly.accept(h,p)
                if raw is not None:
                    u=rolling.accept(raw);require(u['phase']==phase,'update-phase');consumed=u['consumed'];return
        ready=receive('READY');require(ready['configuration']==spec.configuration and ready['abi']>=1 and ready['guard']=='landlock-seccomp-pdeath' and ready['capability']=='B-h264-cont03a','guard-profile')
        if _fault=='check':state='ISOLATION_CHECKED'
        else:
            fields={k:v for k,v in spec.__dict__.items() if k not in ('attempt','principal')}
            send('BEGIN',**fields)
            send('TRUST_CHUNK',trust,offset=0);send('TRUST_END');require(receive('ADMITTED')['trust_digest']==plan.trust_sha256,'trust-ack')
            total=hashlib.sha256();ordinal=0
            for index,original in enumerate(plan.originals):
                guard();prepare=time.monotonic()
                # Explicit source-preparation phase; no outstanding B input/report.
                receipt,nals,metadata=load(index,guard)
                require(time.monotonic()-prepare<=5,'source-preparation-cap');last=time.monotonic()
                size,input_digest=rolling.register(original,receipt,nals,metadata)
                send('SOURCE_BEGIN',ordinal=index,source=original.item_id,generation=original.generation,original_sha256=receipt['sha256'],source_digest=input_digest,source_bytes=size,nal_count=len(nals))
                feed_start=time.monotonic()
                for n in nals:
                    guard();require(time.monotonic()-feed_start<=5,'source-feed-cap')
                    total.update(n.annex_b)
                    send('NAL_BEGIN',ordinal=ordinal,token=token_hex(n.token),size=len(n.annex_b),digest=sha(n.annex_b))
                    for at in range(0,len(n.annex_b),65536):send('NAL_CHUNK',n.annex_b[at:at+65536],ordinal=ordinal,offset=at)
                    send('NAL_END',ordinal=ordinal);update('feed');ordinal+=1
                send('SOURCE_END',ordinal=index,source_digest=input_digest);ack=receive('SOURCE_END');require((ack['ordinal'],ack['source_digest'])==(index,input_digest),'source-ack')
                rolling.commit_source(index)
                del nals
            if plan.input_sha256 is not None:require(total.hexdigest()==plan.input_sha256,'context-occurrence-chain')
            send('FINISH_INPUT');update('finish');terminal=receive('TERMINAL')
            require(terminal['consumed']==consumed==ordinal and terminal['completeness']=='INCOMPLETE','terminal')
            # No result publication based only on child exit; Finish projection is explicit below.
            state='EXITED_SUCCESSFULLY'
        process.stdin.close()
        process.wait(timeout=min(1,max(.01,deadline-time.monotonic())))
        require(process.returncode==0 and not decoder.buffer and not os.read(process.stdout.fileno(),1),'worker-terminal-exit')
        if _fault=='normal':rolling.finish()
    except Exception as exc:
        rolling.fail();state='FAILED';error=type(exc).__name__+':'+str(exc)[:160]
    finally:
        if process is not None:
            if process.poll() is None:
                try:os.killpg(process.pid,signal.SIGTERM)
                except ProcessLookupError:pass
                try:process.wait(timeout=.25)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid,signal.SIGKILL);process.wait(timeout=1)
            process.wait();reaped=True
            for f in (process.stdin,process.stdout,process.stderr):
                if not f.closed:f.close()
    return Outcome(state,error,process.pid if process else None,reaped,consumed,tuple(committed),time.monotonic()-start,process.returncode if process else None,
        dict(active_peak=rolling.max_active,report_peak=rolling.max_report,working_bytes_peak=rolling.max_working_bytes,
             cross_file_retirements=rolling.cross_file_retirements,raw_bytes=rolling.admission.total if rolling.admission else 0,
             wire_input=sent,wire_output=received,finish=rolling.ended,
             pending=len(rolling.admission.last) if rolling.admission else 0) if product else None)
