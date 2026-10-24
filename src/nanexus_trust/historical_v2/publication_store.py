"""Single-owner local-ext4 store with explicit, bounded committed-generation GC."""
from contextlib import contextmanager
import ctypes
import fcntl
import os
import stat
import threading
import uuid
import time
import hashlib
from . import publication as p
from . import wire

ROOT_FORMAT='nanexus-hv2-publication-root/1'
CONTROL='nanexus-hv2-control/1'
REGISTRY='nanexus-hv2-registry/1'
MAX_ROOT=64<<20

class PublicationStore:
    """Root and authority are trusted deployment/test inputs, never client paths.

    Only this owner process reads files; returned bytes contain no file handles.
    IO responsiveness and ext4 fsync behavior are environmental assumptions.
    """
    def __init__(self, root, authority, *, max_bytes=MAX_ROOT, _fault=None):
        p.number(max_bytes,MAX_ROOT,65536)
        for name in ('authorize_publish','publish_guard','authorize_retrieve','retrieval_guard'):
            p.need(callable(getattr(authority,name,None)),'ACCESS_DENIED')
        self.authority=authority;self.max_bytes=max_bytes;self._fault=_fault or (lambda _:None)
        self._lock=threading.RLock();self._writer=threading.Lock();self._pins={};self._closed=False
        self._root_fd=None;self._owner_fd=None;self.recovery=[];self._blocked=False;self._head_possible=False;self.incarnation=uuid.uuid4().hex;self._maintenance_possible=False;self._maintenance_checks=None
        try:
            self._root_fd=self._open_root(root);self.dev=os.fstat(self._root_fd).st_dev
            # Linux ext filesystem magic alone is insufficient: also require
            # an existing ext4 mount attestation for this exact device.
            buf=ctypes.create_string_buffer(256);libc=ctypes.CDLL(None,use_errno=True)
            p.need(libc.fstatfs(self._root_fd,buf)==0 and ctypes.c_long.from_buffer(buf).value==0xef53,'UNSUPPORTED_FILESYSTEM')
            with open('/proc/self/mountinfo','rb') as f:mounts=f.read((512<<10)+1)
            p.need(len(mounts)<=512<<10,'UNSUPPORTED_FILESYSTEM')
            device=f'{os.major(self.dev)}:{os.minor(self.dev)}'.encode()
            p.need(any(line.split()[2]==device and line.split(b' - ',1)[1].split()[0]==b'ext4' for line in mounts.splitlines()),'UNSUPPORTED_FILESYSTEM')
            self._owner_fd=os.open('owner.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600,dir_fd=self._root_fd);self._check(self._owner_fd,False)
            try:fcntl.flock(self._owner_fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError as e:raise p.PublicationError('BUSY') from e
            names=self._entries(self._root_fd,5)
            if 'format.json' not in names:
                p.need(set(names)=={'owner.lock'},'CORRUPT')
                self.root_id=uuid.uuid4().hex
                self._write(self._root_fd,'format.json',p.canonical({'schema':ROOT_FORMAT,'root':self.root_id}))
                os.mkdir('publications',0o700,dir_fd=self._root_fd)
                self._write(self._root_fd,'registry.json',p.canonical({'schema':REGISTRY,'root':self.root_id,'publications':[]}));os.fsync(self._root_fd)
            root_doc=p.load(self._read(self._root_fd,'format.json',p.MAX_META));p.fields(root_doc,('schema','root'));p.need(root_doc['schema']==ROOT_FORMAT,'UNSUPPORTED');p.hexid(root_doc['root'],32);self.root_id=root_doc['root']
            self._registry()
            self._recover()
        except BaseException:
            self.close();raise

    def _open_root(self, root):
        path=os.fspath(root);p.need(type(path) is str and path.startswith('/') and '\x00' not in path,'UNSAFE_PATH')
        parts=path.split('/')[1:];p.need(parts and all(x not in ('','.','..') for x in parts),'UNSAFE_PATH')
        fd=os.open('/',os.O_RDONLY|os.O_DIRECTORY)
        try:
            for i,name in enumerate(parts):
                if i==len(parts)-1:
                    try:os.mkdir(name,0o700,dir_fd=fd);os.fsync(fd)
                    except FileExistsError:pass
                new=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd);os.close(fd);fd=new
            s=os.fstat(fd);p.need(s.st_uid==os.geteuid() and stat.S_IMODE(s.st_mode)==0o700,'UNSAFE_PATH')
            return fd
        except BaseException:os.close(fd);raise

    def _check(self, fd, directory):
        if self._maintenance_checks is not None:
            p.need(self._maintenance_checks<128,'MAINTENANCE_LIMIT');self._maintenance_checks+=1
        s=os.fstat(fd)
        p.need(s.st_uid==os.geteuid() and s.st_dev==self.dev and stat.S_IMODE(s.st_mode)==(0o700 if directory else 0o600),'UNSAFE_PATH')
        p.need(stat.S_ISDIR(s.st_mode) if directory else stat.S_ISREG(s.st_mode) and s.st_nlink==1,'UNSAFE_PATH')
        return s

    @contextmanager
    def _dir(self,parent,name,create=False):
        p.need(type(name) is str and name not in ('','.','..') and '/' not in name and '\\' not in name,'UNSAFE_PATH')
        if create:os.mkdir(name,0o700,dir_fd=parent);os.fsync(parent)
        fd=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=parent)
        try:self._check(fd,True);yield fd
        finally:os.close(fd)

    def _entries(self,fd,cap):
        names=[]
        with os.scandir(fd) as entries:
            for e in entries:
                p.need(len(names)<cap,'RECOVERY_PENDING');names.append(e.name)
        return names

    def _read(self,fd,name,cap):
        p.need('/' not in name and '\\' not in name and name not in ('','.','..'),'UNSAFE_PATH')
        opened=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=fd)
        try:
            before=self._check(opened,False);p.need(before.st_size<=cap,'CORRUPT')
            data=bytearray()
            while len(data)<=before.st_size:
                b=os.read(opened,min(65536,before.st_size+1-len(data)))
                if not b:break
                data.extend(b)
            after=self._check(opened,False)
            p.need(len(data)==before.st_size and (before.st_size,before.st_mtime_ns,before.st_ctime_ns)==(after.st_size,after.st_mtime_ns,after.st_ctime_ns),'CORRUPT')
            return bytes(data)
        finally:os.close(opened)

    def _write(self,fd,name,data,point=None):
        p.need(type(data) is bytes and len(data)<=p.MAX_OBJECT,'QUOTA')
        opened=os.open(name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=fd)
        try:
            self._check(opened,False);at=0
            # Partial IO is handled; the fault boundary can leave real partial bytes.
            while at<len(data):
                size=min(65536,max(1,(len(data)-at)//2)) if at==0 else min(65536,len(data)-at)
                n=os.write(opened,data[at:at+size]);p.need(n>0,'IO_FAILURE');at+=n
                if point and at<len(data):self._fault(point+'-partial')
            if point:self._fault(point+'-written')
            os.fsync(opened)
            if point:self._fault(point+'-synced')
        finally:os.close(opened)

    def _registry(self):
        p.need(not self._closed,'UNAVAILABLE')
        r=p.load(self._read(self._root_fd,'registry.json',p.MAX_META));p.fields(r,('schema','root','publications'))
        p.need(r['schema']==REGISTRY and r['root']==self.root_id and type(r['publications']) is list and len(r['publications'])<=4)
        ids=set()
        for item in r['publications']:
            p.fields(item,('id','association'));p.hexid(item['id'],32);p.need(item['id'] not in ids);ids.add(item['id']);self._association(item['association'])
        return r

    def _association(self,b):
        p.fields(b,('owner','camera','camera_binding','request'))
        for v in b.values():p.text(v)
        p.hexid(b['request'])

    def _record(self,identity):
        p.hexid(identity,32)
        records=[x for x in self._registry()['publications'] if x['id']==identity]
        p.need(len(records)==1,'UNAVAILABLE');return records[0]

    @contextmanager
    def _publication(self,identity):
        self._record(identity)
        with self._dir(self._root_fd,'publications') as pubs:
            with self._dir(pubs,identity) as fd:yield fd

    def _control_bytes(self,c):
        value={k:v for k,v in c.items() if k!='checksum'}
        raw=p.canonical({**value,'checksum':p.digest(p.canonical(value))});p.need(len(raw)<=p.MAX_META,'QUOTA');return raw

    def _control(self,fd,identity):
        return p.control(p.load(self._read(fd,'control.json',p.MAX_META)),self.root_id,identity)

    def _replace(self,fd,name,data,*,head=False,association=None):
        temp=name+'.next'
        self._write(fd,temp,data,'control' if head else None)
        self._fault('before-head' if head else 'before-metadata-replace')
        if head:
            # Called under publish_guard after preparing/syncing the candidate.
            # This fresh check is the authorization decision point; clock read
            # and rename are not physically simultaneous. No lease is renewed.
            check=getattr(self.authority,'final_publish_check',self.authority.authorize_publish)
            p.need(type(association) is dict and callable(check) and check(association) is True,'ACCESS_DENIED')
            self._head_possible=True
        os.replace(temp,name,src_dir_fd=fd,dst_dir_fd=fd)
        if head:self._fault('head-transition')
        os.fsync(fd)
        if head:self._fault('control-directory-synced')

    def _recognize(self,fd,identity,d):
        with self._dir(fd,'generations') as gens:
            with self._dir(gens,d['generation']) as gen:
                raw=self._read(gen,'manifest.json',p.MAX_META);p.check_bytes(raw,{k:d[k] for k in ('sha256','size')},p.MAX_META)
                with self._dir(gen,'objects') as objects,self._dir(gen,'pages') as pages:
                    m=p.recognize(raw,lambda e:self._read(objects,e['sha256']+'.blob',p.MAX_OBJECT),lambda e:self._read(pages,f"{e['ordinal']:04d}.json",wire.MAX_DOCUMENT_BYTES))
                    p.need(m['publication']==identity and m['sequence']==d['sequence'] and m['generation']==d['generation'])
                    c=self._control(fd,identity);matches=[a for a in c['attempts'] if a['status']=='COMMITTED' and a['head']==d]
                    p.need(len(matches)==1 and matches[0]['key']==m['key'] and matches[0]['input']==m['input'])
                    association=self._record(identity)['association'];p.need({k:m['binding'][k] for k in association}==association)
                    p.need(set(self._entries(gen,3))=={'objects','pages','manifest.json'})
                    p.need(set(self._entries(objects,64))=={e['sha256']+'.blob' for e in m['objects']})
                    p.need(set(self._entries(pages,16))=={f'{i:04d}.json' for i in range(len(m['pages']))})
                    return m

    def _usage(self):
        """Bounded scan only of the dedicated format tree; unknown names block."""
        total=0;stages=0;physical={}
        registry=self._registry();known={x['id'] for x in registry['publications']}
        def walk(fd,level,pubid=None):
            nonlocal total,stages
            p.need(level<=5,'RECOVERY_PENDING')
            caps={0:4,1:4,2:6,3:4,4:3,5:64}
            names=self._entries(fd,caps[level])
            if level==0:p.need(set(names)=={'format.json','owner.lock','registry.json','publications'},'RECOVERY_PENDING')
            if level==1:p.need(set(names)==known,'RECOVERY_PENDING')
            if level==2:p.need(set(names)<= {'control.json','control.json.next','staging','generations'} and {'control.json','staging','generations'}<=set(names),'RECOVERY_PENDING')
            if level==3:
                with self._publication(pubid) as job:allowed={a['generation'] for a in self._control(job,pubid)['attempts']}
                p.need(set(names)<=allowed,'RECOVERY_PENDING')
            if level==4:p.need(set(names)<={'objects','pages','manifest.json'},'RECOVERY_PENDING')
            for name in names:
                prior_total=total
                s=os.stat(name,dir_fd=fd,follow_symlinks=False)
                if stat.S_ISDIR(s.st_mode):
                    if level==3: # staging/generations children are generation IDs
                        p.hexid(name)
                    with self._dir(fd,name) as child:walk(child,level+1,name if level==1 else pubid)
                    total+=4096
                else:
                    f=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=fd)
                    try:info=self._check(f,False);p.need(info.st_size<=p.MAX_OBJECT,'QUOTA');total+=max(4096,info.st_size)
                    finally:os.close(f)
                if level==3:physical[(pubid,name)]=physical.get((pubid,name),0)+total-prior_total
                p.need(total<=self.max_bytes,'QUOTA')
        # Format layout has a separate validation pass; count bounded actual bytes.
        walk(self._root_fd,0)
        with self._dir(self._root_fd,'publications') as pubs:
            for item in self._registry()['publications']:
                with self._dir(pubs,item['id']) as job,self._dir(job,'staging') as stage:
                    stages+=len(self._entries(stage,1))
        p.need(stages<=1,'QUOTA')
        for identity,c in self._pending():
            maintenance=c['maintenance'];plan=maintenance['plan'];at=maintenance['next'];files=plan['files']
            floor=sum(max(4096,e['size']) for e in files[at:])+max(0,len(files)+3-max(at,len(files)))*4096
            total+=max(0,floor-physical.get((identity,plan['target']['generation']),0))
        p.need(total<=self.max_bytes,'QUOTA')
        return total,stages

    def _recover(self):
        for item in self._registry()['publications']:
            identity=item['id']
            try:
                with self._publication(identity) as fd:
                    c=self._control(fd,identity)
                    if c['head']:self._recognize(fd,identity,c['head'])
                    if c['reserved']:
                        for a in c['attempts']:
                            if a['status']=='RESERVED':a['status']='INTERRUPTED'
                        c['reserved']=None;c['revision']+=1
                        # Ignore/remove only the exact known uncommitted control temp.
                        try:
                            proposed=p.load(self._read(fd,'control.json.next',p.MAX_META))
                            p.need(proposed.get('schema')==c['schema'] and proposed.get('root')==self.root_id and proposed.get('publication')==identity and proposed.get('head',{}).get('generation') in {a['generation'] for a in c['attempts']},'RECOVERY_PENDING')
                            p.need(proposed.get('checksum')==p.digest(p.canonical({k:v for k,v in proposed.items() if k!='checksum'})),'RECOVERY_PENDING')
                            os.unlink('control.json.next',dir_fd=fd);os.fsync(fd)
                        except FileNotFoundError:pass
                        self._replace(fd,'control.json',self._control_bytes(c))
                        self.recovery.append((identity,'INTERRUPTED'))
                    if c.get('maintenance') is not None:self.recovery.append((identity,'CLEANUP_PENDING'))
                    with self._dir(fd,'staging') as stage:
                        if self._entries(stage,1):self.recovery.append((identity,'ORPHAN_PRESERVED'))
            except (OSError,p.PublicationError):self.recovery.append((identity,'UNAVAILABLE'))
        try:self._usage()
        except (OSError,p.PublicationError):self._blocked=True

    def _admit_writer(self):
        # Nonblocking acquisition under the close/admission lock prevents FD
        # closure between checking store liveness and taking writer ownership.
        with self._lock:
            p.need(not self._closed,'UNAVAILABLE')
            p.need(self._writer.acquire(False),'BUSY')

    def create(self,candidate):
        self._admit_writer()
        try:return self._create(candidate)
        finally:self._writer.release()

    def _create(self,candidate):
        b=candidate.binding();association={k:b[k] for k in ('owner','camera','camera_binding','request')};self._association(association)
        with self._lock,self.authority.publish_guard(association):
            r=self._registry()
            for item in r['publications']:
                if item['association']==association:return item['id']
            p.need(not self._pending(),'CLEANUP_PENDING');p.need(not self._blocked,'RECOVERY_PENDING');used,_=self._usage();p.need(used+32768<=self.max_bytes,'QUOTA')
            p.need(len(r['publications'])<4,'QUOTA');identity=uuid.uuid4().hex
            with self._dir(self._root_fd,'publications') as pubs:
                with self._dir(pubs,identity,True) as fd:
                    for name in ('staging','generations'):os.mkdir(name,0o700,dir_fd=fd)
                    c={'schema':CONTROL,'root':self.root_id,'publication':identity,'revision':0,'head':None,'retained':[],'attempts':[],'reserved':None}
                    self._write(fd,'control.json',self._control_bytes(c));os.fsync(fd)
            r['publications'].append({'id':identity,'association':association});self._replace(self._root_fd,'registry.json',p.canonical(r));return identity

    def publish(self,identity,key,candidate,*,expected_head=None):
        p.text(key);key=p.digest(p.canonical(['idempotency',key]))
        self._admit_writer()
        self._head_possible=False
        try:
            record=self._record(identity);association=record['association']
            p.need(self.authority.authorize_publish(association) is True,'ACCESS_DENIED')
            inp=candidate.identity(validate=False)
            p.need({k:candidate.binding()[k] for k in association}==association,'ACCESS_DENIED')
            with self._publication(identity) as fd:
                c=self._control(fd,identity)
                for a in c['attempts']:
                    if a['key']==key:
                        p.need(a['input']==inp,'CONFLICT')
                        p.need(a['status']!='EXPIRED','EXPIRED');candidate.validate();p.need(a['status']=='COMMITTED','INTERRUPTED');self._recognize(fd,identity,a['head'])
                        with self.authority.publish_guard(association):return dict(a['head'])
                candidate.validate()
                p.need(not self._pending(),'CLEANUP_PENDING');p.need(not self._blocked,'RECOVERY_PENDING')
                if c['head']:self._recognize(fd,identity,c['head'])
                p.need(expected_head==(None if c['head'] is None else c['head']['sha256']),'CONFLICT')
                p.need(c['reserved'] is None and len(c['retained'])<3 and len(c['attempts'])<16,'QUOTA')
                used,stages=self._usage();p.need(stages==0,'RECOVERY_PENDING')
                seq=1 if c['head'] is None else c['head']['sequence']+1
                generation=p.build(identity,seq,c['head'],key,candidate);m=p.manifest(generation.manifest);gid=m['generation']
                charge=sum(max(4096,len(v)) for _,v in generation.objects)+sum(max(4096,len(v)) for v in generation.pages)+max(4096,len(generation.manifest))+65536
                p.need(used+charge<=self.max_bytes,'QUOTA')
                with self._dir(fd,'generations') as gens:p.need(len(self._entries(gens,4))<4,'QUOTA')
                c['attempts'].append({'key':key,'input':inp,'generation':gid,'status':'RESERVED','head':None});c['reserved']=gid;c['revision']+=1
                self._replace(fd,'control.json',self._control_bytes(c));self._fault('before-staging')
                with self._dir(fd,'staging') as stage:
                    with self._dir(stage,gid,True) as gen:
                        for name in ('objects','pages'):os.mkdir(name,0o700,dir_fd=gen)
                        with self._dir(gen,'objects') as objects:
                            seen=set()
                            for _,raw in generation.objects:
                                h=p.digest(raw)
                                if h not in seen:self._write(objects,h+'.blob',raw,'object');seen.add(h)
                            os.fsync(objects);self._fault('objects-synced')
                        with self._dir(gen,'pages') as pages:
                            for i,raw in enumerate(generation.pages):self._write(pages,f'{i:04d}.json',raw,'page'+str(i))
                            os.fsync(pages);self._fault('pages-synced')
                        self._write(gen,'manifest.json',generation.manifest,'manifest');os.fsync(gen)
                        with self._dir(gen,'objects') as objects,self._dir(gen,'pages') as pages:
                            p.recognize(generation.manifest,lambda d:self._read(objects,d['sha256']+'.blob',p.MAX_OBJECT),lambda d:self._read(pages,f"{d['ordinal']:04d}.json",wire.MAX_DOCUMENT_BYTES))
                        self._fault('validated')
                    with self._dir(fd,'generations') as gens:
                        os.rename(gid,gid,src_dir_fd=stage,dst_dir_fd=gens);self._fault('generation-renamed');os.fsync(gens);os.fsync(stage)
                head={'generation':gid,'sequence':seq,**p.descriptor(generation.manifest)}
                c['head']=head;c['retained'].append(head);c['attempts'][-1].update(status='COMMITTED',head=head);c['reserved']=None;c['revision']+=1
                with self.authority.publish_guard(association):
                    # Exclusive writer and authority guard serialize this transition.
                    self._replace(fd,'control.json',self._control_bytes(c),head=True,association=association)
                return dict(head)
        except BaseException as exc:
            if isinstance(exc,(KeyboardInterrupt,SystemExit)):raise
            if self._head_possible:raise p.PublicationError('COMMIT_OUTCOME_UNKNOWN') from exc
            if isinstance(exc,p.PublicationError):raise
            raise p.PublicationError('IO_FAILURE') from exc
        finally:self._writer.release()

    @contextmanager
    def pin(self,identity,caller,generation=None):
        key=None
        try:
            with self._lock:
                p.need(not self._closed,'UNAVAILABLE')
                association=self._record(identity)['association'];p.need(self.authority.authorize_retrieve(association,caller) is True,'ACCESS_DENIED')
                with self._publication(identity) as fd:
                    c=self._control(fd,identity)
                    d=c['head'] if generation is None else next((d for d in c['retained'] if d['generation']==generation),None)
                    if d is None:
                        p.need(not any(e['status']=='EXPIRED' and e['generation']==generation for e in c['attempts']),'EXPIRED')
                        raise p.PublicationError('UNAVAILABLE')
                    p.need(sum(self._pins.values())<4,'BUSY')
                    key=(identity,d['generation']);self._pins[key]=self._pins.get(key,0)+1
                    m=self._recognize(fd,identity,d)
            yield m
        except FileNotFoundError as exc:raise p.PublicationError('CORRUPT') from exc
        except OSError as exc:raise p.PublicationError('CORRUPT') from exc
        finally:
            if key is not None:
                with self._lock:
                    self._pins[key]-=1
                    if not self._pins[key]:del self._pins[key]

    def current(self,identity,caller):
        with self.pin(identity,caller) as m:
            association=self._record(identity)['association']
            with self.authority.retrieval_guard(association,caller):return p.load(p.canonical(m))

    def page(self,identity,caller,*,generation=None,index=0,next_cursor=None):
        p.need(self.authority.authorize_retrieve(self._record(identity)['association'],caller) is True,'ACCESS_DENIED')
        p.number(index,15)
        if next_cursor is not None:
            p.text(next_cursor);parts=next_cursor.split('.');p.need(len(parts)==4 and parts[0]=='d2p1','CORRUPT');p.hexid(parts[1]);p.hexid(parts[3]);p.need(parts[2].isdigit() and len(parts[2])<=2,'CORRUPT');n=int(parts[2]);p.number(n,15,1)
            p.need(next_cursor==p.cursor(identity,parts[1],n) and (generation is None or generation==parts[1]),'CORRUPT');generation=parts[1];index=n
        with self.pin(identity,caller,generation) as m:
            p.need(index<len(m['pages']),'UNAVAILABLE')
            with self._publication(identity) as fd,self._dir(fd,'generations') as gens,self._dir(gens,m['generation']) as gen,self._dir(gen,'pages') as pages:
                raw=self._read(pages,f'{index:04d}.json',wire.MAX_DOCUMENT_BYTES);p.check_bytes(raw,{k:m['pages'][index][k] for k in ('sha256','size')},wire.MAX_DOCUMENT_BYTES);wire.loads(raw)
            with self.authority.retrieval_guard(self._record(identity)['association'],caller):return raw

    def object(self,identity,caller,evidence_id,*,generation=None):
        p.text(evidence_id)
        with self.pin(identity,caller,generation) as m:
            entries=[d for d in m['objects'] if d['id']==evidence_id];p.need(len(entries)==1,'UNAVAILABLE');d=entries[0]
            with self._publication(identity) as fd,self._dir(fd,'generations') as gens,self._dir(gens,m['generation']) as gen,self._dir(gen,'objects') as objects:
                raw=self._read(objects,d['sha256']+'.blob',p.MAX_OBJECT);p.check_bytes(raw,{k:d[k] for k in ('sha256','size')},p.MAX_OBJECT)
            with self.authority.retrieval_guard(self._record(identity)['association'],caller):return raw

    def close(self):
        with self._lock:
            p.need(not self._writer.locked() and not self._pins,'BUSY')
            if self._owner_fd is not None:os.close(self._owner_fd);self._owner_fd=None
            if self._root_fd is not None:os.close(self._root_fd);self._root_fd=None
            self._closed=True
    def __enter__(self):return self
    def __exit__(self,*_):self.close()

    def _pending(self):
        pending=[]
        for item in self._registry()['publications']:
            with self._publication(item['id']) as fd:
                c=self._control(fd,item['id'])
                if c.get('maintenance') is not None:pending.append((item['id'],c))
        p.need(len(pending)<=1,'CORRUPT');return pending

    def _maintenance_context(self,identity,generation,revision):
        return {**self._record(identity)['association'],'root':self.root_id,
                'publication':identity,'target':generation,'revision':revision}

    @contextmanager
    def _maintenance_guard(self,context):
        p.need(callable(getattr(self.authority,'maintenance_guard',None)) and callable(getattr(self.authority,'authorize_maintenance',None)),'ACCESS_DENIED')
        with self.authority.maintenance_guard(context):
            p.need(self.authority.authorize_maintenance(context) is True,'ACCESS_DENIED');yield

    def _clear_maintenance_temp(self,fd,c):
        try:raw=self._read(fd,'control.json.next',p.MAX_META)
        except FileNotFoundError:return
        proposed=p.control(p.load(raw),self.root_id,c['publication'])
        p.need(proposed['revision']==c['revision']+1 and proposed['head']==c['head'],'RECOVERY_PENDING')
        # Only an authenticated maintenance call may discard its uncommitted
        # valid proposal. Unknown/partial temps are preserved and block it.
        p.need(proposed['schema']==p.CONTROL_V2 and c['reserved'] is None and proposed['reserved'] is None,'RECOVERY_PENDING')
        os.unlink('control.json.next',dir_fd=fd);os.fsync(fd)

    def _maintenance_replace(self,fd,c,kind,context):
        raw=self._control_bytes(c);p.control(p.load(raw),self.root_id,c['publication'])
        self._write(fd,'control.json.next',raw,kind+'-control')
        self._fault(kind+'-before-replace')
        p.need(self.authority.authorize_maintenance(context) is True,'ACCESS_DENIED');self._maintenance_possible=True
        os.replace('control.json.next','control.json',src_dir_fd=fd,dst_dir_fd=fd)
        self._fault(kind+'-transition');os.fsync(fd);self._fault(kind+'-directory-synced')

    def upgrade(self,identity,expected_revision):
        """Explicit one-way authenticated control upgrade; never done at open."""
        p.number(expected_revision,2**63-1);self._admit_writer();self._maintenance_possible=False
        try:
            with self._publication(identity) as fd:
                c=self._control(fd,identity);context=self._maintenance_context(identity,None,c['revision'])
                with self._lock,self._maintenance_guard(context):
                    if c['schema']==p.CONTROL_V2:return c['revision']
                    p.need(c['revision']==expected_revision,'CONFLICT');p.need(c['reserved'] is None and not self._pending(),'BUSY')
                    for d in c['retained']:self._recognize(fd,identity,d)
                    self._usage();self._clear_maintenance_temp(fd,c)
                    c.update(schema=p.CONTROL_V2,maintenance=None,revision=c['revision']+1)
                    self._maintenance_replace(fd,c,'upgrade',context);return c['revision']
        except Exception as exc:
            if self._maintenance_possible:raise p.PublicationError('UPGRADE_OUTCOME_UNKNOWN') from exc
            if isinstance(exc,p.PublicationError):raise
            raise p.PublicationError('IO_FAILURE') from exc
        finally:self._writer.release()

    def _plan(self,fd,identity,c,target,m):
        entries=p.deletion_files(m)+[{'kind':'manifest','sha256':target['sha256'],'size':target['size'],'ordinal':None}]
        with self._dir(fd,'generations') as gens,self._dir(gens,target['generation']) as gen:
            directories={'generation':os.fstat(gen).st_ino}
            with self._dir(gen,'objects') as objects,self._dir(gen,'pages') as pages:
                directories.update(objects=os.fstat(objects).st_ino,pages=os.fstat(pages).st_ino)
                for entry in entries:
                    parent=objects if entry['kind']=='object' else pages if entry['kind']=='page' else gen
                    name=entry['sha256']+'.blob' if entry['kind']=='object' else f"{entry['ordinal']:04d}.json" if entry['kind']=='page' else 'manifest.json'
                    f=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=parent)
                    try:s=self._check(f,False);p.need(s.st_size==entry['size']);entry['inode']=s.st_ino
                    finally:os.close(f)
        plan={'root':self.root_id,'publication':identity,'target':target,'key':m['key'],'input':m['input'],
              'incarnation':self.incarnation,'operation':p.digest(p.canonical([self.root_id,identity,target,c['revision']])),
              'protected':[d for d in c['retained'] if d!=target],'files':entries,'directories':directories}
        p.deletion_plan(plan)
        # Dependencies are generation-local. Check physical independence too;
        # predecessor descriptors are explicitly non-owning history only.
        inodes={e['inode'] for e in entries}|set(directories.values())
        for other in plan['protected']:
            with self._dir(fd,'generations') as gens,self._dir(gens,other['generation']) as gen:
                raw=self._read(gen,'manifest.json',p.MAX_META);p.check_bytes(raw,{k:other[k] for k in ('sha256','size')},p.MAX_META);other_manifest=p.manifest(raw)
                with self._dir(gen,'objects') as objects,self._dir(gen,'pages') as pages:
                    other_files=p.deletion_files(other_manifest)+[{'kind':'manifest','sha256':other['sha256'],'size':other['size'],'ordinal':None}]
                    for entry in other_files:
                        parent=objects if entry['kind']=='object' else pages if entry['kind']=='page' else gen
                        name=entry['sha256']+'.blob' if entry['kind']=='object' else f"{entry['ordinal']:04d}.json" if entry['kind']=='page' else 'manifest.json'
                        f=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=parent)
                        try:p.need(self._check(f,False).st_ino not in inodes,'OWNING_DEPENDENCY')
                        finally:os.close(f)
        return plan

    def retire(self,identity,generation,expected_revision):
        p.hexid(generation);p.number(expected_revision,2**63-1);self._admit_writer();self._maintenance_possible=False
        try:
            with self._publication(identity) as fd:
                c=self._control(fd,identity);context=self._maintenance_context(identity,generation,c['revision'])
                with self._maintenance_guard(context):pass
                p.need(c['schema']==p.CONTROL_V2,'UNSUPPORTED')
                prior=next((e for e in c['attempts'] if e['generation']==generation and e['status']=='EXPIRED'),None)
                if prior:return {'status':'EXPIRED','generation':generation,'pending':c['maintenance'] is not None and c['maintenance']['plan']['target']['generation']==generation}
                p.need(c['revision']==expected_revision,'CONFLICT');p.need(not self._pending() and c['reserved'] is None,'BUSY')
                target=next((d for d in c['retained'] if d['generation']==generation),None);p.need(target is not None,'UNAVAILABLE');p.need(target!=c['head'],'HEAD_PROTECTED')
                m=self._recognize(fd,identity,target);plan=self._plan(fd,identity,c,target,m)
                with self._lock,self._maintenance_guard(context):
                    current=self._control(fd,identity);p.need(current==c,'CONFLICT');p.need(not self._pins.get((identity,generation),0),'BUSY')
                    used,_=self._usage();p.need(used+2*p.MAX_META<=self.max_bytes,'QUOTA');self._clear_maintenance_temp(fd,c)
                    c['revision']+=1;c['retained'].remove(target)
                    for e in c['attempts']:
                        if e['status']=='COMMITTED' and e['head']==target:e.update(status='EXPIRED',retired_revision=c['revision'])
                    c['maintenance']={'plan':plan,'digest':p.digest(p.canonical(plan)),'next':0,'phase':'PENDING'}
                    self._maintenance_replace(fd,c,'retire',context)
                    return {'status':'EXPIRED','generation':generation,'pending':True}
        except Exception as exc:
            if self._maintenance_possible:raise p.PublicationError('RETIREMENT_OUTCOME_UNKNOWN') from exc
            if isinstance(exc,p.PublicationError):raise
            raise p.PublicationError('IO_FAILURE') from exc
        finally:self._writer.release()

    def _gc_layout(self,fd,plan,stack,next_index):
        gens=stack.enter_context(self._dir(fd,'generations'));gid=plan['target']['generation']
        try:gen=stack.enter_context(self._dir(gens,gid))
        except FileNotFoundError:return gens,None,None,None
        p.need(self._check(gen,True).st_ino==plan['directories']['generation'],'UNSAFE_PATH')
        p.need(set(self._entries(gen,4))<={'objects','pages','manifest.json'},'UNSAFE_PATH')
        dirs={}
        for kind in ('objects','pages'):
            try:d=stack.enter_context(self._dir(gen,kind))
            except FileNotFoundError:dirs[kind]=None;continue
            p.need(self._check(d,True).st_ino==plan['directories'][kind],'UNSAFE_PATH')
            allowed={e['sha256']+'.blob' if kind=='objects' else f"{e['ordinal']:04d}.json" for e in plan['files'] if e['kind']==('object' if kind=='objects' else 'page')}
            actual=set(self._entries(d,65 if kind=='objects' else 17));p.need(actual<=allowed,'UNSAFE_PATH')
            done={e['sha256']+'.blob' if kind=='objects' else f"{e['ordinal']:04d}.json" for e in plan['files'][:next_index] if e['kind']==('object' if kind=='objects' else 'page')}
            p.need(not actual & done,'CORRUPT');dirs[kind]=d
        return gens,gen,dirs['objects'],dirs['pages']

    def _gc_unlink(self,parent,name,entry,context):
        try:f=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=parent)
        except FileNotFoundError:return False
        try:
            before=self._check(f,False);p.need(before.st_ino==entry['inode'] and before.st_size==entry['size'],'UNSAFE_PATH')
            digest=hashlib.sha256();size=0
            while size<entry['size']:
                raw=os.read(f,min(65536,entry['size']-size))
                if not raw:break
                size+=len(raw);digest.update(raw)
            after=self._check(f,False)
            p.need(size==entry['size'] and digest.hexdigest()==entry['sha256'] and (before.st_ino,before.st_mtime_ns,before.st_ctime_ns)==(after.st_ino,after.st_mtime_ns,after.st_ctime_ns),'CORRUPT')
            p.need(self._maintenance_checks<128,'MAINTENANCE_LIMIT');self._maintenance_checks+=1
            current=os.stat(name,dir_fd=parent,follow_symlinks=False);p.need((current.st_dev,current.st_ino)==(before.st_dev,before.st_ino),'UNSAFE_PATH')
            self._fault('cleanup-before-unlink');p.need(self.authority.authorize_maintenance(context) is True,'ACCESS_DENIED')
            os.unlink(name,dir_fd=parent);self._fault('cleanup-file-unlinked')
            if entry['kind']=='manifest':self._fault('cleanup-manifest-removed')
            return True
        finally:os.close(f)

    def cleanup_step(self,identity):
        from contextlib import ExitStack
        self._admit_writer();self._maintenance_possible=False;self._maintenance_checks=0
        start=time.monotonic();files=0;verified=0;removed_dirs=0
        try:
            with self._publication(identity) as fd:
                c=self._control(fd,identity);p.need(c['schema']==p.CONTROL_V2,'UNSUPPORTED');maintenance=c['maintenance']
                context=self._maintenance_context(identity,None if maintenance is None else maintenance['plan']['target']['generation'],c['revision'])
                with self._lock,self._maintenance_guard(context):
                    pending=self._pending();p.need(not pending or pending[0][0]==identity,'BUSY')
                    if maintenance is None:return {'status':'DONE','files_unlinked':0,'bytes_verified':0,'metadata_checks':self._maintenance_checks,'directories_removed':0}
                    plan=maintenance['plan'];gid=plan['target']['generation'];p.need(not self._pins.get((identity,gid),0),'BUSY')
                    self._clear_maintenance_temp(fd,c)
                    # Resolve any uncertain retirement visibility and synchronize
                    # the observed expiry before permitting the first unlink.
                    os.fsync(fd);self._fault('cleanup-expiry-synced')
                    with ExitStack() as stack:
                        gens,gen,objects,pages=self._gc_layout(fd,plan,stack,maintenance['next']);at=maintenance['next'];entries=plan['files'];limit=len(entries)+3
                        while at<limit and time.monotonic()-start<2:
                            if self._maintenance_checks>112:break
                            if at<len(entries):
                                entry=entries[at]
                                if files>=16 or verified+entry['size']>4<<20:break
                                parent=objects if entry['kind']=='object' else pages if entry['kind']=='page' else gen
                                name=entry['sha256']+'.blob' if entry['kind']=='object' else f"{entry['ordinal']:04d}.json" if entry['kind']=='page' else 'manifest.json'
                                if parent is not None:
                                    unlinked=self._gc_unlink(parent,name,entry,context)
                                    if unlinked:files+=1;verified+=entry['size']
                                at+=1
                            else:
                                kind=('objects','pages','generation')[at-len(entries)];child=objects if kind=='objects' else pages if kind=='pages' else gen;parent=gen if kind!='generation' else gens
                                if child is not None:
                                    p.need(self._check(child,True).st_ino==plan['directories'][kind],'UNSAFE_PATH');p.need(not self._entries(child,1),'UNSAFE_PATH')
                                    p.need(self.authority.authorize_maintenance(context) is True,'ACCESS_DENIED')
                                    os.rmdir(gid if kind=='generation' else kind,dir_fd=parent);removed_dirs+=1;self._fault('cleanup-directory-removed')
                                    if kind=='generation':self._fault('cleanup-generation-removed')
                                at+=1
                        for d in (objects,pages,gen,gens):
                            if d is not None:os.fsync(d)
                        self._fault('cleanup-before-checkpoint')
                    c['revision']+=1
                    if at==limit:c['maintenance']=None
                    else:c['maintenance'].update(next=at,phase='PENDING' if at==0 else 'DELETING')
                    self._maintenance_replace(fd,c,'cleanup-checkpoint',context)
                    return {'status':'DONE' if c['maintenance'] is None else 'CLEANUP_PENDING','generation':gid,'next':at,
                            'files_unlinked':files,'bytes_verified':verified,'metadata_checks':self._maintenance_checks,'directories_removed':removed_dirs}
        except Exception as exc:
            if self._maintenance_possible:raise p.PublicationError('CLEANUP_OUTCOME_UNKNOWN') from exc
            if isinstance(exc,p.PublicationError):raise
            raise p.PublicationError('CLEANUP_PENDING') from exc
        finally:self._maintenance_checks=None;self._writer.release()
