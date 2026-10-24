"""Bounded immutable experimental delivery bytes; no persistence or media IO."""
import base64
import json
from collections import Counter
from . import composition as c, wire
from .worker_protocol import sha

SUMMARY_CAP = 256 << 10
CHUNK_CAP = 1 << 20
STORE_CAP = 256 << 20
FILE_CAP = 4 << 20


class ResultLimit(ValueError): pass


class Bundle:
    def __init__(self):
        self._objects = {}
        self.files = []
        self.bytes = 0
        self._recordings = set()

    def clear(self):
        self._objects.clear(); self.files.clear(); self._recordings.clear(); self.bytes = 0

    def add_file(self, entry, request, snapshot):
        """Called only after existing compose/validate; recheck delivery bindings."""
        result = entry['result']
        page_raw = c.canonical(result['page'])
        if len(page_raw) != result['size'] or sha(page_raw) != result['sha256']:
            raise ValueError('page binding')
        page = wire.loads(page_raw)
        if page.request != request or entry['recording'] in self._recordings:
            raise ValueError('request or duplicate file')
        pending = {}
        descriptors = {}
        for obj in result['objects']:
            if obj['encoding'] != 'base64' or obj['id'] in pending:
                raise ValueError('duplicate or unsupported object')
            if type(obj['data']) is not str or len(obj['data']) > ((FILE_CAP+2)//3)*4:
                raise ResultLimit('file evidence cap')
            raw = base64.b64decode(obj['data'], validate=True)
            if len(raw) != obj['size'] or sha(raw) != obj['sha256']:
                raise ValueError('object binding')
            if sum(map(len,pending.values())) + len(raw) > FILE_CAP:
                raise ResultLimit('file evidence cap')
            pending[obj['id']] = raw
            descriptors[obj['id']] = {'id':obj['id'],'size':len(raw),'sha256':sha(raw)}
        expected = {ref.evidence_id:(ref.size,ref.sha256) for ref in page.evidence}
        if set(expected) != set(pending) or any(expected[key] != (len(raw),sha(raw)) for key,raw in pending.items()):
            raise ValueError('incomplete evidence')
        observations = list(page.observations)
        if page.next_cursor:
            continuation = json.loads(pending[page.next_cursor])
            observations.extend(wire.loads(c.canonical(obj)) for obj in continuation['observations'])
        if len({obj.observation_id for obj in observations}) != len(observations):
            raise ValueError('duplicate observation')
        tails = [json.loads(raw) for key,raw in pending.items() if key.startswith('tail-')]
        if len(tails) != 1: raise ValueError('tail binding')
        counts = dict(Counter(obj.state.value for obj in observations))
        metadata = dict(entry)
        page_id = 'page-' + sha(page_raw)
        pending[page_id] = page_raw
        metadata['result'] = {key:value for key,value in result.items() if key not in ('page','objects')}
        metadata['result'].update(page_ref=page_id,objects=list(descriptors.values()))
        raw = c.canonical({'request_id':request.request_id,'camera':request.camera.native_key,
                           'snapshot':snapshot,'file':metadata})
        identity = 'file-' + sha(raw)
        pending[identity] = raw
        if sum(map(len,pending.values())) > FILE_CAP: raise ResultLimit('file evidence cap')
        for key,raw in pending.items():
            if key in self._objects and self._objects[key] != raw: raise ValueError('conflicting object')
        extra = sum(len(raw) for key,raw in pending.items() if key not in self._objects)
        row = dict(recording=entry['recording'],generation=entry['generation'],metadata_interval=entry['metadata_interval'],
                   evidence={'id':identity,'sha256':sha(pending[identity]),'size':len(pending[identity])},
                   finalized_counts=counts,unresolved=len(tails[0]['unresolved_tokens']),
                   unprocessed=len(tails[0]['unprocessed_tokens']),missing_evidence=bool(tails[0]['missing']))
        # Charge summary descriptors as well as immutable complete evidence.
        extra += len(c.canonical(row))
        if self.bytes + extra > STORE_CAP: raise ResultLimit('retained evidence cap')
        self._objects.update(pending); self.bytes += extra
        self._recordings.add(entry['recording']); self.files.append(row)

    def chunk(self, identity, offset, count):
        if type(identity) is not str or identity not in self._objects: raise KeyError('unavailable')
        raw = self._objects[identity]
        if type(offset) is not int or not 0 <= offset <= len(raw) or type(count) is not int or not 0 < count <= CHUNK_CAP:
            raise ValueError('chunk bounds')
        value = raw[offset:offset+count]
        return dict(id=identity,sha256=sha(raw),size=len(raw),offset=offset,bytes=len(value),
                    chunk_sha256=sha(value),encoding='base64',data=base64.b64encode(value).decode(),
                    end=offset+len(value)==len(raw))


def summary_bytes(value):
    raw = c.canonical(value)
    if len(raw) > SUMMARY_CAP: raise ResultLimit('summary cap')
    return raw
