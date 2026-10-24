"""Private lossless B admission. No native execution, clock mapping or publication IO."""
import json
from .worker_protocol import ProtocolError, pairs, sha, CONFIG_DIGEST

MAX_UPDATE = 1 << 20
MAX_UPDATES_BYTES = 2 << 20
UPDATE = {'run_id','attempt','phase','trust','configuration','stop','error','consumed',
          'generating_feed_ordinal','report_generation','fresh_report','retired_prefix_ordinal','items','missing'}
ITEM = {'received_ordinal','source_token_hex','nalu_type','raw_status','revision','report_generation','finalized','state','axes'}
AXES = {'authenticity','provenance','combined','key_changed','raw_signing_start','raw_signing_end'}

def require(ok, why):
    if not ok: raise ProtocolError(why)

def integer(v, high=2**63-1, low=0):
    require(type(v) is int and low <= v <= high, 'update-integer')

def token_hex(token):
    require(type(token) is str, 'token-type')
    try: raw = token.encode('utf-8')
    except UnicodeError as e: raise ProtocolError('token-utf8') from e
    require(0 < len(raw) <= 256, 'token-cap')
    return raw.hex()

def token_text(value):
    require(type(value) is str and 0 < len(value) <= 512 and len(value)%2 == 0
            and all(c in '0123456789abcdef' for c in value), 'token-hex')
    try: text = bytes.fromhex(value).decode('utf-8')
    except (ValueError, UnicodeError) as e: raise ProtocolError('token-utf8') from e
    require(token_hex(text) == value, 'token-canonical')
    return text

def decode(raw):
    require(type(raw) is bytes and 0 < len(raw) <= MAX_UPDATE, 'update-size')
    try: u = json.loads(raw, object_pairs_hook=pairs)
    except (ValueError, UnicodeError, RecursionError) as e: raise ProtocolError('update-json') from e
    require(type(u) is dict and set(u) == UPDATE, 'update-fields')
    for k in ('run_id','attempt','trust','configuration'):
        require(type(u[k]) is str and 0 < len(u[k]) <= 256, 'update-text')
    for k in ('trust','configuration'):
        require(len(u[k]) == 64 and all(c in '0123456789abcdef' for c in u[k]), 'update-digest')
    require(u['phase'] in ('feed','finish','error'), 'update-phase')
    for k in ('generating_feed_ordinal','report_generation','consumed'): integer(u[k],1024)
    integer(u['stop'],4); integer(u['error'],6)
    require(type(u['fresh_report']) is bool, 'update-bool')
    if u['retired_prefix_ordinal'] is not None: integer(u['retired_prefix_ordinal'],1023)
    for k in ('items','missing'):
        require(type(u[k]) is list and len(u[k]) <= 2048, 'update-collection')
    for i in u['items']:
        require(type(i) is dict and set(i) == ITEM, 'item-fields')
        for k in ('received_ordinal','revision','report_generation'): integer(i[k],1024)
        token_text(i['source_token_hex'])
        require(type(i['nalu_type']) is str and len(i['nalu_type']) == 1 and i['nalu_type'] in 'IiPpCSszv_oU ', 'nal-type')
        require(type(i['raw_status']) is str and len(i['raw_status']) == 1 and i['raw_status'] in 'PUNEM_.', 'raw-status')
        require(i['nalu_type'] != ' ' and i['raw_status'] != 'M', 'received-not-missing')
        integer(i['state'],3);require(type(i['finalized']) is bool, 'item-finality')
        a=i['axes'];require(type(a) is dict and set(a) == AXES, 'axes-fields')
        integer(a['authenticity'],5);integer(a['provenance'],3);integer(a['combined'],3)
        require(type(a['key_changed']) is bool, 'axes-bool')
        for k in ('raw_signing_start','raw_signing_end'):
            if a[k] is not None: integer(a[k],2**63-1,-2**63)
    for m in u['missing']:
        require(type(m) is dict and set(m) == {'left_received','right_received','report_generation'}, 'missing-fields')
        integer(m['report_generation'],1024)
        for k in ('left_received','right_received'):
            if m[k] is not None: integer(m[k],1023)
    return u

class Admission:
    """Transactional bounded in-memory admission; immutable raw bytes retained."""
    def __init__(self, spec, tokens):
        require(0 < len(tokens) <= 1024 and len(set(tokens)) == len(tokens), 'source-tokens')
        self.tokens=tuple(tokens)
        for t in tokens: token_hex(t)
        self.spec=spec;self.raws=();self.total=0;self.emitted=0;self.consumed=0
        self.generation=0;self.retired=None;self.last={};self.reports={};self.ended=False
    def add(self, raw):
        require(not self.ended and len(self.raws)<1025 and self.total+len(raw)<=MAX_UPDATES_BYTES, 'update-total-cap')
        u=decode(raw);s=self.spec
        require((u['run_id'],u['attempt'],u['trust'],u['configuration']) ==
                (s.run,s.attempt,s.trust_digest,s.configuration), 'update-binding')
        require(s.configuration == CONFIG_DIGEST, 'configuration')
        n=u['consumed'];g=self.generation;r=self.retired
        if u['phase']=='feed':
            require(n==self.consumed+1 and n<=len(self.tokens) and u['generating_feed_ordinal']==n-1 and u['stop']==0 and u['error']==0, 'feed-order')
            if u['fresh_report']:
                require(u['report_generation']==g+1, 'fresh-generation');g+=1
            else:
                require(u['report_generation']==0 and not u['items'] and not u['missing'] and u['retired_prefix_ordinal'] is None, 'nonfresh')
        else:
            require(n==self.consumed and u['generating_feed_ordinal']==max(0,n-1) and not u['fresh_report'] and u['report_generation']==g, 'terminal-identity')
            require((u['phase']=='finish' and u['stop']==4 and u['error']==0) or
                    (u['phase']=='error' and u['stop'] in (1,2,3) and u['error']!=0), 'terminal-stop')
            require(not u['missing'], 'terminal-missing')
        if u['retired_prefix_ordinal'] is not None:
            new=u['retired_prefix_ordinal'];require(new<n and (r is None or new>=r), 'retirement');r=new
        elif u['fresh_report'] or u['phase']!='feed': require(r is None, 'lost-retirement')
        require(self.emitted+len(u['items'])+len(u['missing'])<=8192, 'emission-cap')
        last=dict(self.last);seen=set();pending=False;key_changed=any(i['axes']['key_changed'] for i in self.last.values())
        expected_active=set(range(0 if self.retired is None else self.retired+1,n))
        for i in u['items']:
            ordinal=i['received_ordinal'];require(ordinal<n and ordinal not in seen and ordinal in expected_active, 'received-ordinal');seen.add(ordinal)
            require(token_text(i['source_token_hex'])==self.tokens[ordinal], 'source-token')
            old=last.get(ordinal)
            if old is not None:
                require(not old['finalized'], 'retired-revision')
                if u['phase']=='feed':
                    changed=(old['raw_status'],old['nalu_type'])!=(i['raw_status'],i['nalu_type'])
                    require(i['revision']==old['revision']+int(changed), 'revision')
                else:
                    require(all(i[k]==old[k] for k in ITEM-{'finalized','state'}), 'finish-rewrite')
            else: require(i['revision']==0, 'initial-revision')
            if u['phase']=='feed':
                require(i['report_generation']==g and g>0, 'item-report')
                pending |= i['raw_status']=='P'
                require(i['finalized']==(not pending), 'finalized-prefix')
            else:
                require(not i['finalized'] and i['state']==3 and i['report_generation']<=g, 'provisional-tail')
            if i['finalized']:require(r is not None and ordinal<=r, 'finality-retirement')
            a=i['axes'];key_changed |= a['key_changed']
            if i['state'] in (0,1):
                require(i['finalized'] and i['nalu_type'] in 'IiPp' and g>0, 'resolved-state')
                if i['state']==0:
                    require(not key_changed and a['authenticity'] in (3,4) and a['provenance']==3 and a['combined'] in (1,2) and i['raw_status']=='.', 'positive-axes')
                else: require(i['raw_status']=='N' and a['authenticity']==2, 'negative-axes')
            if i['finalized'] and not key_changed and a['authenticity']!=5 and i['nalu_type'] in 'IiPp':
                if i['raw_status']=='N' and a['authenticity']==2:require(i['state']==1, 'negative-preservation')
                if i['raw_status']=='.' and a['authenticity'] in (3,4) and a['provenance']==3 and a['combined'] in (1,2):require(i['state']==0, 'positive-preservation')
            require(i['state']!=2, 'absence-unqualified')
            last[ordinal]=i
        if u['fresh_report'] or u['phase']!='feed':
            require(seen==expected_active, 'incomplete-active-set')
        if u['fresh_report']:
            require([i['received_ordinal'] for i in u['items']]==sorted(seen), 'item-order')
            finalized=[i['received_ordinal'] for i in u['items'] if i['finalized']]
            require(r==(finalized[-1] if finalized else self.retired), 'retired-prefix')
        for m in u['missing']:
            left,right=m['left_received'],m['right_received']
            require(m['report_generation']==g and (left is not None or right is not None), 'missing-generation')
            require(all(v is None or 0<=v<n for v in (left,right)), 'missing-anchor')
            require(left is None or right is None or right==left+1, 'missing-order')
            require(left is not None or right==0, 'missing-left-edge')
            require(right is not None or left==n-1, 'missing-right-edge')
        # Nothing mutates until every check succeeds.
        self.raws+= (raw,);self.total+=len(raw);self.emitted+=len(u['items'])+len(u['missing'])
        self.last=last;self.consumed=n;self.generation=g;self.retired=r
        if u['fresh_report']: self.reports[g]=u
        self.ended=u['phase']!='feed'
        return u

class Reassembly:
    def __init__(self): self.next=0;self.begin=None;self.buffer=bytearray()
    def accept(self, h, payload):
        kind=h['type']
        if kind=='UPDATE_BEGIN':
            require(self.begin is None and h['update']==self.next and 0<h['size']<=MAX_UPDATE and h['items']<=2048 and h['missing']<=2048, 'update-begin')
            require(len(h['digest'])==64 and all(c in '0123456789abcdef' for c in h['digest']), 'update-digest')
            self.begin=dict(h);return None
        require(self.begin is not None and h['update']==self.next, 'update-interleave')
        if kind=='UPDATE_CHUNK':
            require(h['offset']==len(self.buffer) and payload and len(self.buffer)+len(payload)<=self.begin['size'], 'update-offset')
            self.buffer.extend(payload);return None
        require(kind=='UPDATE_END' and not payload and len(self.buffer)==self.begin['size'] and h['digest']==self.begin['digest']==sha(self.buffer), 'update-end')
        raw=bytes(self.buffer);u=decode(raw)
        require(len(u['items'])==self.begin['items'] and len(u['missing'])==self.begin['missing'], 'update-counts')
        self.next+=1;self.begin=None;self.buffer.clear();return raw
