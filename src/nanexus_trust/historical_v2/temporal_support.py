"""Independent synthetic temporal contracts; accepted A/B/C remain unchanged.

Decoding is not recognition. Only recognize() binds an entire publication. The
only supported proof policy is an explicitly synthetic exhaustive interval
model, never a real Frigate origin or cryptographic verification authority.
No storage, IO resolver, API, worker or renderer is implemented here.
"""
from dataclasses import dataclass, fields, is_dataclass
from fractions import Fraction
import hashlib
import json
from math import gcd
from typing import Callable

from . import contracts as a, wire

BOUNDS_SCHEMA = 'nanexus-historical-v2-temporal-support-bounds'
PUBLICATION_SCHEMA = 'nanexus-historical-v2-temporal-publication'
VERSION = 1
BOUNDS_PROFILE = 'nanexus-historical-v2-temporal-support-bounds-v1'
BASE_PROFILE = 'nanexus-historical-v2-result-page-v1'
SYNTHETIC_POLICY = 'synthetic-temporal-extent-v1'
SYNTHETIC_PROOF_PROFILE = 'synthetic-temporal-extent-proof-v1'
COORDINATE = 'host-metadata-unix-seconds'
MAX_DOCUMENT_BYTES = 64 * 1024
MAX_PUBLICATION_BYTES = 1024 * 1024
MAX_DEPTH = 12


class TemporalError(ValueError):
    """Unsupported/invalid semantic publication; no legacy fallback implied."""


def _require(ok, message):
    if not ok:
        raise TemporalError(message)


def _text(value):
    try:
        a.text(value)
    except ValueError as exc:
        raise TemporalError('bounded text required') from exc


def _digest(value):
    _require(type(value) is str and len(value) == 64 and
             all(c in '0123456789abcdef' for c in value), 'invalid digest')


def _refs(values, *, nonempty=True):
    _require(type(values) is tuple and (not nonempty or bool(values)) and
             len(values) <= a.MAX_ITEMS, 'bounded references required')
    for value in values:
        _text(value)
    _require(len(set(values)) == len(values), 'duplicate references')


def _descriptor(ref):
    _require(type(ref) is a.EvidenceReference, 'typed evidence descriptor required')
    _require(ref.size > 0, 'empty evidence')


def _descriptors(values):
    _require(type(values) is tuple and 0 < len(values) <= a.MAX_ITEMS,
             'bounded evidence descriptors required')
    for ref in values:
        _descriptor(ref)
    _require(len({r.evidence_id for r in values}) == len(values), 'duplicate evidence IDs')


def _rational(value):
    _require(type(value) is a.Rational, 'exact typed rational required')
    _require(gcd(value.numerator, value.denominator) == 1, 'reduced rational required')
    return Fraction(value.numerator, value.denominator)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class ParentBinding:
    job_id: str
    request_sha256: str
    run_id: str
    observation_id: str
    observation_sha256: str

    def __post_init__(self):
        for value in (self.job_id, self.run_id, self.observation_id): _text(value)
        _digest(self.request_sha256)
        _digest(self.observation_sha256)


@dataclass(frozen=True)
class TemporalSupportBounds:
    attachment_id: str
    parent: ParentBinding
    coordinate: str
    policy: str
    start_min: a.Rational
    start_max: a.Rational
    end_min: a.Rational
    end_max: a.Rational
    source_refs: tuple[str, ...]
    dependency_refs: tuple[str, ...]
    evidence: tuple[a.EvidenceReference, ...]

    def __post_init__(self):
        _text(self.attachment_id)
        _require(type(self.parent) is ParentBinding, 'typed parent binding required')
        _text(self.coordinate)
        _require(self.coordinate == COORDINATE, 'unsupported coordinate')
        _text(self.policy)
        lo, hi, elo, ehi = map(_rational, (self.start_min, self.start_max, self.end_min, self.end_max))
        _require(lo <= hi and elo <= ehi and lo < ehi, 'impossible bounds')
        _refs(self.source_refs)
        _refs(self.dependency_refs)
        _descriptors(self.evidence)
        _require(self.attachment_id not in {r.evidence_id for r in self.evidence}, 'self-dependent evidence')

    @property
    def definite_core(self):
        """Geometry only; never render it before publication recognition."""
        start, end = _rational(self.start_max), _rational(self.end_min)
        return (start, end) if start < end else None

    @property
    def possible_envelope(self):
        return (_rational(self.start_min), _rational(self.end_max))

    @property
    def uncertainty_bands(self):
        lo, hi = self.possible_envelope
        core = self.definite_core
        if core is None:
            return ((lo, hi),)
        return tuple(pair for pair in ((lo, core[0]), (core[1], hi)) if pair[0] < pair[1])


@dataclass(frozen=True)
class TemporalPublication:
    publication_id: str
    generation: int
    job_id: str
    request_sha256: str
    base_page: a.EvidenceReference
    attachments: tuple[a.EvidenceReference, ...]

    def __post_init__(self):
        _text(self.publication_id)
        _text(self.job_id)
        _require(type(self.generation) is int and 0 <= self.generation <= a.MAX_INTEGER,
                 'invalid publication generation')
        _digest(self.request_sha256)
        _descriptor(self.base_page)
        _require(self.base_page.profile == BASE_PROFILE, 'unsupported base profile')
        _descriptors(self.attachments)
        _require(all(ref.profile == BOUNDS_PROFILE for ref in self.attachments), 'unsupported attachment profile')
        _require(self.base_page.evidence_id not in {r.evidence_id for r in self.attachments}, 'evidence ID collision')


def _plain(value):
    if is_dataclass(value):
        return {f.name: _plain(getattr(value, f.name)) for f in fields(value)}
    if type(value) is tuple:
        return [_plain(v) for v in value]
    return value


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def dumps(value):
    if type(value) is TemporalSupportBounds:
        schema = BOUNDS_SCHEMA
    elif type(value) is TemporalPublication:
        schema = PUBLICATION_SCHEMA
    else:
        raise TemporalError('unsupported temporal record')
    data = _canonical({'schema': schema, 'version': VERSION, 'type': type(value).__name__,
                       'record': _plain(value)})
    _require(len(data) <= MAX_DOCUMENT_BYTES, 'document byte cap')
    return data


def _keys(value, names):
    _require(type(value) is dict and set(value) == set(names), 'missing or unknown fields')


def _list(value):
    _require(type(value) is list and len(value) <= a.MAX_ITEMS, 'collection cap')
    return value


def _reject_number(_):
    raise TemporalError('noninteger JSON number')


def _json(data):
    _require(type(data) is bytes and 0 < len(data) <= MAX_DOCUMENT_BYTES, 'document byte cap')
    def integer(text):
        _require(len(text.lstrip('-')) <= 19, 'integer cap')
        value = int(text)
        _require(abs(value) <= a.MAX_INTEGER, 'integer cap')
        return value
    def pairs(items):
        result = {}
        for key, value in items:
            _require(key not in result, 'duplicate JSON key')
            result[key] = value
        return result
    try:
        value = json.loads(data, object_pairs_hook=pairs, parse_float=_reject_number,
                           parse_constant=_reject_number, parse_int=integer)
    except (ValueError, RecursionError, UnicodeError) as exc:
        raise TemporalError('malformed temporal JSON') from exc
    pending = [(value, 0)]
    while pending:
        node, depth = pending.pop()
        _require(depth <= MAX_DEPTH, 'nesting cap')
        if type(node) is dict:
            pending.extend((v, depth + 1) for v in node.values())
        elif type(node) is list:
            _list(node)
            pending.extend((v, depth + 1) for v in node)
    return value


def _decode_rational(value):
    _keys(value, ('numerator', 'denominator'))
    try:
        rational = a.Rational(**value)
    except ValueError as exc:
        raise TemporalError('invalid rational') from exc
    _rational(rational)
    return rational


def _decode_descriptor(value):
    _keys(value, ('evidence_id', 'sha256', 'size', 'profile'))
    try:
        ref = a.EvidenceReference(**value)
    except ValueError as exc:
        raise TemporalError('invalid evidence descriptor') from exc
    _descriptor(ref)
    return ref


def loads(data):
    value = _json(data)
    _keys(value, ('schema', 'version', 'type', 'record'))
    _require(type(value['version']) is int and value['version'] == VERSION, 'unsupported temporal version')
    kind = (value['schema'], value['type'])
    record = value['record']
    if kind == (BOUNDS_SCHEMA, 'TemporalSupportBounds'):
        _keys(record, (f.name for f in fields(TemporalSupportBounds)))
        record = dict(record)
        _keys(record['parent'], (f.name for f in fields(ParentBinding)))
        record['parent'] = ParentBinding(**record['parent'])
        for name in ('start_min', 'start_max', 'end_min', 'end_max'):
            record[name] = _decode_rational(record[name])
        for name in ('source_refs', 'dependency_refs'):
            record[name] = tuple(_list(record[name]))
        record['evidence'] = tuple(_decode_descriptor(r) for r in _list(record['evidence']))
        return TemporalSupportBounds(**record)
    if kind == (PUBLICATION_SCHEMA, 'TemporalPublication'):
        _keys(record, (f.name for f in fields(TemporalPublication)))
        record = dict(record)
        record['base_page'] = _decode_descriptor(record['base_page'])
        record['attachments'] = tuple(_decode_descriptor(r) for r in _list(record['attachments']))
        return TemporalPublication(**record)
    raise TemporalError('unsupported temporal schema/type')


@dataclass(frozen=True)
class ValidationLimits:
    max_bytes: int = MAX_PUBLICATION_BYTES
    max_resolver_calls: int = 256
    max_cases: int = 128

    def __post_init__(self):
        for name, cap in (('max_bytes', MAX_PUBLICATION_BYTES), ('max_resolver_calls', 256), ('max_cases', 128)):
            value = getattr(self, name)
            _require(type(value) is int and 0 < value <= cap, 'validation resource limit')


@dataclass(frozen=True)
class RecognizedPublication:
    """Immutable pure recognition result, not durable state or a trust token.

    Prior results supplied to recognize() must come from a trusted caller's
    earlier successful recognition. D/E must implement authorized durable heads.
    """
    manifest: TemporalPublication
    root_sha256: str
    base_page: a.ResultPage
    attachments: tuple[TemporalSupportBounds, ...]
    resolved_bytes: int
    resolver_calls: int


class _Reader:
    def __init__(self, resolver, limits, root_bytes, check, prior_descriptors):
        _require(callable(resolver) and callable(check), 'trusted bounded resolver/check required')
        self.resolver, self.limits, self.check = resolver, limits, check
        self.bytes, self.calls, self.cases = root_bytes, 0, 0
        self.cache = {}
        self.prior_descriptors = prior_descriptors
        _require(self.bytes <= limits.max_bytes, 'publication byte cap')

    def read(self, ref, profile, cap):
        self.check()
        prior = self.prior_descriptors.get(ref.evidence_id)
        _require(prior is None or prior == ref, 'immutable evidence ID conflict')
        _require(ref.profile == profile, 'unsupported evidence profile')
        cached = self.cache.get(ref.evidence_id)
        if cached is not None:
            _require(cached[0] == ref, 'conflicting evidence descriptor')
            return cached[1]
        _require(self.calls < self.limits.max_resolver_calls, 'resolver work cap')
        _require(0 < ref.size <= min(cap, self.limits.max_bytes - self.bytes), 'publication byte cap')
        self.calls += 1
        # Resolver is trusted IO/authority infrastructure: it must honor max_bytes
        # and its own deadline. This pure contract cannot preempt a blocked call.
        try:
            data = self.resolver(ref, ref.size)
        except (KeyError, OSError, TimeoutError) as exc:
            raise TemporalError('artifact unavailable') from exc
        self.check()
        _require(type(data) is bytes and len(data) == ref.size and sha256(data) == ref.sha256,
                 'artifact digest/size mismatch')
        self.bytes += len(data)
        self.cache[ref.evidence_id] = (ref, data)
        return data


def _binding(bounds, page, parent):
    key = bounds.parent
    _require((key.job_id, key.request_sha256, key.run_id, key.observation_id, key.observation_sha256) ==
             (page.job_id, sha256(wire.dumps(page.request)), parent.run_id,
              parent.observation_id, sha256(wire.dumps(parent))), 'parent/request binding mismatch')
    _require(parent.finality is a.Finality.FINALIZED and parent.placement.status is a.PlacementStatus.AMBIGUOUS,
             'finalized ambiguous parent required')
    _require(parent.support.kind in ('sample', 'order', 'ambiguous') and
             parent.support.interval is None and parent.support.point is None,
             'nonnumeric parent support required')
    _require(all(v is None for v in (parent.placement.pts, parent.placement.time_base,
                                    parent.placement.origin, parent.placement.anchor)), 'contradictory numeric placement')
    _require((parent.placement.coordinate, parent.placement.policy) == (bounds.coordinate, bounds.policy),
             'coordinate/policy mismatch')
    _require(bounds.policy == SYNTHETIC_POLICY, 'unsupported temporal policy')
    _require(bounds.source_refs == parent.support.source_refs, 'source scope mismatch')
    dependencies = tuple(dict.fromkeys((*parent.context_refs,
                         *([parent.support.left_ref] if parent.support.left_ref else []),
                         *([parent.support.right_ref] if parent.support.right_ref else []),
                         *parent.support.alternatives)))
    _require(bool(dependencies) and set(dependencies) == set(bounds.dependency_refs), 'dependency scope mismatch')
    _require(set(parent.context_refs) <= set(page.actual_context_refs), 'context absent from actual page scope')
    _require(parent.observation_id in page.unresolved_refs, 'ambiguous parent obligation missing')
    _require(bounds.attachment_id in parent.evidence_refs, 'parent attachment reference missing')
    _require(set(parent.evidence_refs) == {bounds.attachment_id, *(ref.evidence_id for ref in bounds.evidence)},
             'missing or undeclared parent proof evidence')


def _proof(bounds, parent, descriptors, reader):
    """A closed synthetic exhaustive model only; no real proof-policy adapter.

    Authored fixture cases define continuous intervals as axioms in a synthetic
    model. They never attest actual source origin, trust or signing continuity.
    """
    cases, seen = [], set()
    alternatives = set()
    for ref in bounds.evidence:
        _require(descriptors.get(ref.evidence_id) == ref, 'proof descriptor missing/mismatch')
        data = reader.read(ref, SYNTHETIC_PROOF_PROFILE, MAX_DOCUMENT_BYTES)
        proof = _json(data)
        _keys(proof, ('model', 'observation_sha256', 'coordinate', 'policy', 'source_refs',
                      'dependency_refs', 'cases'))
        _require(proof['model'] == 'synthetic-exhaustive-continuous-intervals-v1', 'unsupported proof model')
        _require(proof['observation_sha256'] == bounds.parent.observation_sha256 and
                 proof['coordinate'] == bounds.coordinate and proof['policy'] == bounds.policy,
                 'proof identity/coordinate/policy mismatch')
        _require(tuple(_list(proof['source_refs'])) == bounds.source_refs and
                 tuple(_list(proof['dependency_refs'])) == bounds.dependency_refs, 'proof scope mismatch')
        proof_cases = _list(proof['cases'])
        _require(bool(proof_cases), 'missing extent proof')
        for case in proof_cases:
            reader.check()
            reader.cases += 1
            _require(reader.cases <= reader.limits.max_cases, 'proof case work cap')
            _keys(case, ('start', 'end', 'alternative_ref'))
            start, end = _rational(_decode_rational(case['start'])), _rational(_decode_rational(case['end']))
            _require(start < end, 'noncontinuous/empty synthetic extent')
            alt = case['alternative_ref']
            if parent.support.kind == 'ambiguous':
                _text(alt)
                _require(alt in parent.support.alternatives, 'unqualified alternative')
                alternatives.add(alt)
            else:
                _require(alt is None, 'unexpected alternative')
            key = (start, end, alt)
            _require(key not in seen, 'duplicate synthetic case')
            seen.add(key)
            cases.append((start, end))
    _require(parent.support.kind != 'ambiguous' or alternatives == set(parent.support.alternatives),
             'unresolved alternatives')
    _require((min(s for s,e in cases), max(s for s,e in cases), min(e for s,e in cases), max(e for s,e in cases)) ==
             tuple(map(_rational, (bounds.start_min,bounds.start_max,bounds.end_min,bounds.end_max))),
             'bounds not supported by exhaustive model')


def recognize(data: bytes, resolver: Callable, *, previous=None,
              limits=ValidationLimits(), check=lambda: None):
    """Pure all-or-nothing recognition. Failure never mutates previous evidence.

    resolver(descriptor, max_bytes) supplies already-authorized immutable bytes.
    No fallback, persistence, cryptographic verification or actual rendering.
    """
    _require(type(limits) is ValidationLimits, 'typed validation limits required')
    manifest = loads(data)
    _require(type(manifest) is TemporalPublication and dumps(manifest) == data, 'canonical publication root required')
    root = sha256(data)
    if previous is None:
        _require(manifest.generation == 0, 'initial publication generation must be zero')
    else:
        _require(type(previous) is RecognizedPublication, 'trusted recognized prior required')
        prior = previous.manifest
        _require((manifest.publication_id,manifest.job_id,manifest.request_sha256) ==
                 (prior.publication_id,prior.job_id,prior.request_sha256), 'publication identity conflict')
        if manifest.generation == prior.generation:
            _require(root == previous.root_sha256, 'publication generation conflict')
        else:
            _require(manifest.generation == prior.generation + 1, 'stale/skipped publication generation')
    prior_descriptors = {} if previous is None else {r.evidence_id:r for r in
                         (previous.manifest.base_page, *previous.base_page.evidence)}
    reader = _Reader(resolver, limits, len(data), check, prior_descriptors)
    base_bytes = reader.read(manifest.base_page, BASE_PROFILE, wire.MAX_DOCUMENT_BYTES)
    try:
        page = wire.loads(base_bytes)
    except ValueError as exc:
        raise TemporalError('invalid A base page') from exc
    _require(type(page) is a.ResultPage and wire.dumps(page) == base_bytes, 'canonical A base page required')
    _require(page.job_id == manifest.job_id and sha256(wire.dumps(page.request)) == manifest.request_sha256,
             'publication request/job mismatch')
    _require(page.lifecycle is not a.Lifecycle.COMPLETE, 'ambiguous temporal publication cannot be COMPLETE')
    descriptors = {}
    for ref in page.evidence:
        _require(ref.evidence_id not in descriptors, 'duplicate page evidence')
        prior = prior_descriptors.get(ref.evidence_id)
        _require(prior is None or prior == ref, 'immutable evidence ID conflict')
        descriptors[ref.evidence_id] = ref
    required = {r.evidence_id: r for r in page.evidence if r.profile == BOUNDS_PROFILE}
    _require(required == {r.evidence_id:r for r in manifest.attachments}, 'missing/extra attachments')
    observations = {}
    for obs in page.observations:
        key = (obs.run_id, obs.observation_id)
        _require(key not in observations, 'duplicate parent observation')
        observations[key] = obs
    recognized, active = [], set()
    for ref in manifest.attachments:
        attachment_bytes = reader.read(ref, BOUNDS_PROFILE, MAX_DOCUMENT_BYTES)
        bounds = loads(attachment_bytes)
        _require(type(bounds) is TemporalSupportBounds and dumps(bounds) == attachment_bytes and
                 bounds.attachment_id == ref.evidence_id, 'canonical attachment ID/type mismatch')
        parent = observations.get((bounds.parent.run_id,bounds.parent.observation_id))
        _require(parent is not None, 'orphan attachment')
        _require((parent.run_id,parent.observation_id) not in active, 'conflicting active attachments')
        active.add((parent.run_id,parent.observation_id))
        _require(sum(ref.evidence_id in obs.evidence_refs for obs in page.observations) == 1,
                 'attachment referenced by wrong/duplicate parent')
        _refs(parent.evidence_refs)
        _refs(parent.support.source_refs)
        _refs(parent.context_refs, nonempty=False)
        _binding(bounds, page, parent)
        _proof(bounds, parent, descriptors, reader)
        recognized.append(bounds)
    # Every parent reference to a mandatory bound needs a descriptor, including
    # references accidentally omitted from both the manifest and base evidence.
    declared = {r.evidence_id for r in manifest.attachments}
    for obs in page.observations:
        if obs.placement.policy == SYNTHETIC_POLICY:
            _require((obs.run_id,obs.observation_id) in active, 'missing parent attachment')
        _require(not (set(obs.evidence_refs) & declared) or (obs.run_id,obs.observation_id) in active,
                 'orphan parent attachment reference')
    reader.check()
    return RecognizedPublication(manifest, root, page, tuple(recognized), reader.bytes, reader.calls)
