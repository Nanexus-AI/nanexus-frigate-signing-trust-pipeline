"""Historical V2 domain vocabulary. These records do not verify media.

Bounds protect serialization, not deployment capacity or qualified support.
Optional evidence means unknown/unavailable, never an inferred runtime fact.
"""
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
import re

MAX_TEXT = 256
MAX_ITEMS = 128
MAX_INTEGER = 2**63 - 1


class SigningState(str, Enum):
    VERIFIED_SIGNING = 'VERIFIED_SIGNING'
    INTEGRITY_NEGATIVE = 'INTEGRITY_NEGATIVE'
    NO_RECOGNIZED_SIGNING_EVIDENCE = 'NO_RECOGNIZED_SIGNING_EVIDENCE'
    INDETERMINATE = 'INDETERMINATE'


class CoverageState(str, Enum):
    AVAILABLE = 'AVAILABLE'
    UNAVAILABLE = 'UNAVAILABLE'
    NO_RETAINED_VIDEO = 'NO_RETAINED_VIDEO'
    UNKNOWN = 'UNKNOWN'


class Quality(str, Enum):
    SATISFIED = 'SATISFIED'
    INCOMPLETE = 'INCOMPLETE'
    UNKNOWN = 'UNKNOWN'


class PlacementStatus(str, Enum):
    QUALIFIED_METADATA_TIME = 'QUALIFIED_METADATA_TIME'
    UNPLACED = 'UNPLACED'
    AMBIGUOUS = 'AMBIGUOUS'


class Finality(str, Enum):
    PROVISIONAL = 'PROVISIONAL'
    FINALIZED = 'FINALIZED'


class Lifecycle(str, Enum):
    QUEUED = 'QUEUED'
    DISCOVERING = 'DISCOVERING'
    VERIFYING = 'VERIFYING'
    RESOLVING_CONTEXT = 'RESOLVING_CONTEXT'
    FINALIZING = 'FINALIZING'
    COMPLETE = 'COMPLETE'
    INCOMPLETE = 'INCOMPLETE'
    STOPPED = 'STOPPED'
    CANCELLED = 'CANCELLED'
    FAILED = 'FAILED'
    INTERRUPTED = 'INTERRUPTED'


class StopReason(str, Enum):
    RESOURCE_POLICY = 'RESOURCE_POLICY'
    INPUT_QUALIFICATION = 'INPUT_QUALIFICATION'
    CONTEXT_POLICY = 'CONTEXT_POLICY'


def text(value):
    if type(value) is not str or not 0 < len(value) <= MAX_TEXT or any(ord(c) < 32 for c in value):
        raise ValueError('bounded nonempty text required')


def count(value):
    if type(value) is not int or not 0 <= value <= MAX_INTEGER:
        raise ValueError('bounded nonnegative integer required')


class Record:
    def __post_init__(self):
        # One strict validator is shared by direct construction and wire decoding.
        from .wire import validate_record
        validate_record(self)


@dataclass(frozen=True)
class Camera(Record):
    provider_instance: str
    native_key: str
    binding_generation: str
    display_name: str


@dataclass(frozen=True)
class Rational(Record):
    numerator: int
    denominator: int

    def __post_init__(self):
        super().__post_init__()
        if abs(self.numerator) > MAX_INTEGER or not 0 < self.denominator <= MAX_INTEGER:
            raise ValueError('invalid rational')


@dataclass(frozen=True)
class Interval(Record):
    start: str
    end: str

    def __post_init__(self):
        super().__post_init__()
        for value in (self.start, self.end):
            if not re.fullmatch(r'-?[0-9]{1,20}(?:\.[0-9]{1,9})?', value):
                raise ValueError('finite decimal seconds required')
        if Decimal(self.start) >= Decimal(self.end):
            raise ValueError('nonempty half-open interval required')


@dataclass(frozen=True)
class Request(Record):
    request_id: str
    camera: Camera
    interval: Interval
    coordinate: str
    operation: str = 'historical-v2'

    def __post_init__(self):
        super().__post_init__()
        if self.coordinate != 'host-metadata-unix-seconds' or self.operation != 'historical-v2':
            raise ValueError('unsupported request coordinate/operation')


@dataclass(frozen=True)
class Support(Record):
    kind: str
    source_refs: tuple[str, ...]
    interval: Interval | None = None
    point: Rational | None = None
    sample_ref: str | None = None
    left_ref: str | None = None
    right_ref: str | None = None
    missing_count: int | None = None
    alternatives: tuple[str, ...] = ()

    def __post_init__(self):
        super().__post_init__()
        if not self.source_refs:
            raise ValueError('explicit affected scope required')
        if self.kind not in ('interval', 'sample', 'order', 'unplaced', 'ambiguous'):
            raise ValueError('unknown support form')
        allowed = {
            'interval': {'interval'}, 'sample': {'point', 'sample_ref'},
            'order': {'left_ref', 'right_ref', 'missing_count'},
            'unplaced': set(), 'ambiguous': {'alternatives'},
        }[self.kind]
        for name in ('interval', 'point', 'sample_ref', 'left_ref', 'right_ref', 'missing_count', 'alternatives'):
            if getattr(self, name) is not None and getattr(self, name) != () and name not in allowed:
                raise ValueError('incompatible support field')
        if self.kind == 'interval' and self.interval is None:
            raise ValueError('interval extent required')
        if self.kind == 'sample' and self.point is None and self.sample_ref is None:
            raise ValueError('sample identity or point required')
        if self.kind == 'order' and self.left_ref is None and self.right_ref is None:
            raise ValueError('order anchor required; missing items have no invented ordinal')
        if self.kind == 'ambiguous' and len(self.alternatives) < 2:
            raise ValueError('competing references required')


@dataclass(frozen=True)
class Placement(Record):
    status: PlacementStatus
    coordinate: str | None
    policy: str | None
    pts: int | None = None
    time_base: Rational | None = None
    origin: Rational | None = None
    anchor: Rational | None = None

    def __post_init__(self):
        super().__post_init__()
        if self.status is PlacementStatus.UNPLACED and any(v is not None for v in (self.coordinate, self.pts, self.time_base, self.origin, self.anchor)):
            raise ValueError('unplaced evidence cannot assert numeric placement')
        if self.status is PlacementStatus.QUALIFIED_METADATA_TIME and (self.coordinate is None or self.policy is None):
            raise ValueError('qualified placement requires coordinate and policy provenance')


@dataclass(frozen=True)
class ReportReference(Record):
    generation: str
    generating_feed_ordinal: int
    revision: int
    received_refs: tuple[str, ...]
    retired_prefix_ordinal: int | None = None


@dataclass(frozen=True)
class Observation(Record):
    observation_id: str
    run_id: str
    state: SigningState
    support: Support
    placement: Placement
    context_quality: Quality
    context_refs: tuple[str, ...]
    finality: Finality
    report: ReportReference | None
    trust_profile: str | None
    evidence_refs: tuple[str, ...]
    reasons: tuple[str, ...] = ()

    def __post_init__(self):
        super().__post_init__()
        if self.support.point is not None and self.placement.status is not PlacementStatus.QUALIFIED_METADATA_TIME:
            raise ValueError('numeric point needs qualified placement')
        if self.state is SigningState.NO_RECOGNIZED_SIGNING_EVIDENCE and (self.finality is not Finality.FINALIZED or not self.evidence_refs):
            raise ValueError('absence of recognition needs finalized inspected-scope evidence')
        if self.support.kind == 'interval' and self.placement.status is not PlacementStatus.QUALIFIED_METADATA_TIME:
            raise ValueError('timed interval needs qualified placement')
        if self.support.kind == 'unplaced' and self.placement.status is not PlacementStatus.UNPLACED:
            raise ValueError('unplaced support cannot assert time')
        if self.support.kind == 'ambiguous' and self.placement.status is not PlacementStatus.AMBIGUOUS:
            raise ValueError('ambiguous support cannot assert certain placement')
        if self.state in (SigningState.VERIFIED_SIGNING, SigningState.INTEGRITY_NEGATIVE):
            if self.finality is not Finality.FINALIZED or self.report is None or not self.evidence_refs:
                raise ValueError('resolved signing claim requires finalized evidence and report')
        if self.state is SigningState.VERIFIED_SIGNING and (self.trust_profile is None or self.context_quality is not Quality.SATISFIED):
            raise ValueError('positive claim requires trust and supported context')


@dataclass(frozen=True)
class Coverage(Record):
    scope: Interval
    state: CoverageState
    inventory_id: str
    complete_discovery: bool
    stable_source_refs: tuple[str, ...] = ()

    def __post_init__(self):
        super().__post_init__()
        if self.state is CoverageState.NO_RETAINED_VIDEO and not self.complete_discovery:
            raise ValueError('incomplete listing cannot establish a gap')
        if self.state is CoverageState.AVAILABLE and not self.stable_source_refs:
            raise ValueError('available requires stable accessed source identity')


@dataclass(frozen=True)
class Progress(Record):
    phase: Lifecycle
    basis: str
    plan_revision: str
    scanned: int
    finalized: int
    denominator: int | None
    context_scanned: int = 0

    def __post_init__(self):
        super().__post_init__()
        if self.basis not in ('counts-only', 'stable-plan-file-size-weighted'):
            raise ValueError('unsupported progress basis')
        if self.finalized > self.scanned or (self.denominator is not None and self.scanned > self.denominator):
            raise ValueError('inconsistent work counters')
        if self.basis == 'counts-only' and self.denominator is not None:
            raise ValueError('unknown denominator must stay unknown')


@dataclass(frozen=True)
class EvidenceReference(Record):
    evidence_id: str
    sha256: str
    size: int
    profile: str

    def __post_init__(self):
        super().__post_init__()
        if not re.fullmatch('[0-9a-f]{64}', self.sha256):
            raise ValueError('invalid evidence digest')


@dataclass(frozen=True)
class ResultPage(Record):
    request: Request
    job_id: str
    engine_profile: str
    lifecycle: Lifecycle
    reason: StopReason | None
    progress: Progress
    actual_context_refs: tuple[str, ...]
    processed_refs: tuple[str, ...]
    unresolved_refs: tuple[str, ...]
    unprocessed_refs: tuple[str, ...]
    output_complete: bool
    coverage: tuple[Coverage, ...]
    observations: tuple[Observation, ...]
    evidence: tuple[EvidenceReference, ...]
    next_cursor: str | None

    def __post_init__(self):
        super().__post_init__()
        if any(o.finality is not Finality.FINALIZED for o in self.observations):
            raise ValueError('result pages contain finalized observations only')
        if self.progress.phase is not self.lifecycle:
            raise ValueError('phase/lifecycle mismatch')
        if self.lifecycle is Lifecycle.COMPLETE and (not self.output_complete or self.unresolved_refs or self.unprocessed_refs):
            raise ValueError('complete cannot contain unresolved or unprocessed work')
        if self.lifecycle is Lifecycle.COMPLETE:
            if self.progress.finalized != self.progress.scanned:
                raise ValueError('complete requires all scanned work finalized')
            if self.progress.denominator is not None and self.progress.scanned != self.progress.denominator:
                raise ValueError('complete requires declared planned work finished')
        if self.lifecycle is Lifecycle.STOPPED and self.reason is None:
            raise ValueError('controlled stop requires policy reason')
