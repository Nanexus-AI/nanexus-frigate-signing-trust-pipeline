"""Finite, opt-in private rolling worker. No paths/media bytes cross into B.

One direct-read file buffer at a time; one child/extractor until context end.
"""
from dataclasses import dataclass, replace
import hashlib
import os
import signal
import stat
import struct
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


# Pinned ONVIF Media Signing Framework identity. The directory comes from the executable, not from a checkout.
OMS_SONAME = 'libmedia-signing-framework.so.25'
OMS_SHA256 = 'e157fd03c14ba2ae63ce19800cb00d994bc4500a80d84f88492d5537cda76c98'
_LIB_CAP = 8 << 20
_DT_NEEDED = 1
_DT_STRTAB = 5
_DT_STRSZ = 10
_DT_RUNPATH = 29
_PT_LOAD = 1
_PT_DYNAMIC = 2


def _oms(value, reason='oms-library-identity'):
    if not value:
        raise ProtocolError(reason)


def _elf_need_and_runpath(blob):
    """Read this executable's DT_NEEDED names and its one DT_RUNPATH string.

    This is not a loader. Other dynamic tags, including DT_RPATH, are ignored.
    """
    _oms(len(blob) >= 64 and blob[:4] == b'\x7fELF' and blob[4] == 2 and blob[5] == 1 and blob[6] == 1)
    _oms(struct.unpack_from('<H', blob, 16)[0] in (2, 3) and struct.unpack_from('<H', blob, 18)[0] == 62)
    _oms(struct.unpack_from('<I', blob, 20)[0] == 1)
    phoff = struct.unpack_from('<Q', blob, 32)[0]
    ehsize, phentsize, phnum = struct.unpack_from('<HHH', blob, 52)
    _oms(ehsize == 64 and phentsize == 56 and 1 <= phnum <= 64)
    _oms(phoff + phnum * phentsize <= len(blob))
    loads = []
    dynamic = None
    for index in range(phnum):
        off = phoff + index * phentsize
        ptype, _, poff, paddr, _, filesz, _, _ = struct.unpack_from('<IIQQQQQQ', blob, off)
        _oms(poff <= len(blob) and filesz <= len(blob) - poff)
        if ptype == _PT_LOAD and filesz:
            loads.append((poff, paddr, filesz))
        elif ptype == _PT_DYNAMIC:
            _oms(dynamic is None and filesz and filesz % 16 == 0 and filesz // 16 <= 128)
            dynamic = (poff, filesz)
    _oms(dynamic is not None and loads)
    dynoff, dynsz = dynamic
    needed = []
    runpath = None
    strtab = strsz = None
    saw_null = False
    for cursor in range(dynoff, dynoff + dynsz, 16):
        tag, val = struct.unpack_from('<QQ', blob, cursor)
        if tag == 0:
            saw_null = True
            break
        elif tag == _DT_NEEDED:
            needed.append(val)
        elif tag == _DT_STRTAB:
            _oms(strtab is None)
            strtab = val
        elif tag == _DT_STRSZ:
            _oms(strsz is None and 0 < val <= 1 << 20)
            strsz = val
        elif tag == _DT_RUNPATH:
            _oms(runpath is None)
            runpath = val
    _oms(saw_null and strtab is not None and strsz is not None and runpath is not None and needed)
    def map_vaddr(vaddr, size):
        for poff, paddr, filesz in loads:
            if paddr <= vaddr and vaddr - paddr <= filesz - size:
                return poff + (vaddr - paddr)
        raise ProtocolError('oms-library-identity')
    strings = blob[map_vaddr(strtab, strsz):map_vaddr(strtab, strsz) + strsz]
    def text(offset):
        _oms(offset < len(strings))
        end = strings.find(b'\0', offset)
        _oms(end >= 0)
        try:
            return strings[offset:end].decode('ascii')
        except UnicodeError as exc:
            raise ProtocolError('oms-library-identity') from exc
    names = [text(item) for item in needed]
    _oms(names.count(OMS_SONAME) == 1)
    return text(runpath)


def _runpath_directory(raw, origin):
    """One absolute directory, or one directory expressed only with $ORIGIN."""
    _oms(isinstance(raw, str) and 0 < len(raw) <= 4096 and '\x00' not in raw and ':' not in raw)
    if raw.startswith('/'):
        _oms('$' not in raw)
        expanded = raw
    else:
        marker = '\0'
        expanded = raw.replace('${ORIGIN}', marker).replace('$ORIGIN', marker)
        _oms(marker in expanded and '$' not in expanded)
        expanded = expanded.replace(marker, origin)
        _oms(expanded.startswith('/'))
    try:
        directory = Path(expanded).resolve(strict=True)
        info = directory.lstat()
    except (OSError, RuntimeError) as exc:
        raise ProtocolError('oms-library-identity') from exc
    _oms(stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode))
    return directory


def _reject_hwcaps_override(directory):
    """Refuse the directory glibc searches before the RUNPATH directory itself.

    glibc opens glibc-hwcaps/<cpu-level>/<SONAME> ahead of <SONAME>. The active
    level is a host property, so any entry of this name is an ambiguous path.
    """
    try:
        os.lstat(directory / 'glibc-hwcaps')
    except FileNotFoundError:
        return
    except OSError as exc:
        raise ProtocolError('oms-library-identity') from exc
    raise ProtocolError('oms-library-identity')


def resolved_oms_library(executable):
    """Resolve the OMS SONAME inside the executable's one DT_RUNPATH directory.

    The launch environment carries no LD_LIBRARY_PATH. A missing RUNPATH entry would
    otherwise fall through to the ld cache and the default system directories; this
    function does not. A glibc-hwcaps entry in the selected directory is refused.
    A SONAME symlink is accepted only when it names one regular file in that same
    directory. The returned file is what the check hashes.
    """
    path = Path(executable)
    try:
        resolved = path.resolve(strict=True)
        info = resolved.lstat()
    except (OSError, RuntimeError) as exc:
        raise ProtocolError('oms-library-identity') from exc
    _oms(stat.S_ISREG(info.st_mode) and not stat.S_ISLNK(info.st_mode) and 0 < info.st_size <= 8 << 20)
    try:
        fd = os.open(resolved, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    except OSError as exc:
        raise ProtocolError('oms-library-identity') from exc
    try:
        blob = _read_fd(fd, info.st_size)
        _oms(len(blob) == info.st_size)
    finally:
        os.close(fd)
    directory = _runpath_directory(_elf_need_and_runpath(blob), str(resolved.parent))
    _reject_hwcaps_override(directory)
    candidate = directory / OMS_SONAME
    try:
        kind = candidate.lstat()
    except OSError as exc:
        raise ProtocolError('oms-library-identity') from exc
    if stat.S_ISLNK(kind.st_mode):
        try:
            target = os.readlink(candidate)
        except OSError as exc:
            raise ProtocolError('oms-library-identity') from exc
        _oms(target and '/' not in target and target not in ('.', '..') and '\x00' not in target)
        final = directory / target
        try:
            kind = final.lstat()
        except OSError as exc:
            raise ProtocolError('oms-library-identity') from exc
        _oms(stat.S_ISREG(kind.st_mode) and not stat.S_ISLNK(kind.st_mode) and final.parent == directory)
    else:
        final = candidate
        _oms(stat.S_ISREG(kind.st_mode) and final.parent == directory)
    return final


def admit_oms_library(executable):
    """Require the pinned OMS bytes before exec.

    Path and hash checks narrow the launch, but a replacement after this open and
    before the dynamic loader's open can still change what the process maps.
    """
    final = resolved_oms_library(executable)
    try:
        fd = os.open(final, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    except OSError as exc:
        raise ProtocolError('oms-library-identity') from exc
    try:
        info = os.fstat(fd)
        _oms(stat.S_ISREG(info.st_mode) and 0 < info.st_size <= _LIB_CAP)
        blob = _read_fd(fd, info.st_size)
        _oms(len(blob) == info.st_size and sha(blob) == OMS_SHA256)
    finally:
        os.close(fd)


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
            admit_oms_library(resolved)
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
