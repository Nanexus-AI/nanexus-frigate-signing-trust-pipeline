"""Domain contracts: no transport, storage-file or VMS dependencies."""

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, localcontext
from enum import Enum
import re


class VerificationResult(str, Enum):
    VALID = "VALID"
    PARTIAL = "PARTIAL"
    INVALID = "INVALID"
    UNKNOWN = "UNKNOWN"


class MetadataCoverage(str, Enum):
    FULL = "FULL"
    NOT_FULL = "NOT_FULL"
    INDETERMINATE = "INDETERMINATE"


class RequestedCoverage(str, Enum):
    FULL = "FULL"
    INDETERMINATE = "INDETERMINATE"


class SigningStatus(str, Enum):
    ACTIVE = "ACTIVE"
    DEGRADED = "DEGRADED"
    UNKNOWN = "UNKNOWN"


def identifier(value: str) -> str:
    """Portable logical identifier, never an endpoint or filesystem path."""
    if type(value) is not str or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", value):
        raise ValueError("invalid logical identifier")
    return value


@dataclass(frozen=True)
class CameraId:
    value: str

    def __post_init__(self):
        identifier(self.value)


@dataclass(frozen=True)
class RenditionId:
    value: str

    def __post_init__(self):
        identifier(self.value)


def decimal_seconds(value: str) -> Decimal:
    # Decimal strings avoid silently rounding metadata timestamps through floats.
    if type(value) is not str or not re.fullmatch(r"-?[0-9]{1,20}(?:\.[0-9]{1,9})?", value):
        raise ValueError("expected decimal Unix seconds, up to nanosecond precision")
    try:
        return Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("invalid decimal seconds") from exc


@dataclass(frozen=True)
class Interval:
    """Nonempty half-open interval in metadata Unix UTC seconds, not authenticated time."""

    start: str
    end: str

    def __post_init__(self):
        if decimal_seconds(self.start) >= decimal_seconds(self.end):
            raise ValueError("interval must be finite, ordered and nonempty")

    def overlaps(self, other: "Interval") -> bool:
        return max(decimal_seconds(self.start), decimal_seconds(other.start)) < min(
            decimal_seconds(self.end), decimal_seconds(other.end)
        )


def covers(request: Interval, intervals: tuple[Interval, ...]) -> bool:
    """Check actual interval union, never just the surrounding envelope."""
    cursor = decimal_seconds(request.start)
    for span in sorted(intervals, key=lambda span: decimal_seconds(span.start)):
        if decimal_seconds(span.start) > cursor:
            return False
        cursor = max(cursor, decimal_seconds(span.end))
        if cursor >= decimal_seconds(request.end):
            return True
    return False


@dataclass(frozen=True)
class RequestedRange:
    request_id: str
    camera: CameraId
    interval: Interval
    rendition: RenditionId | None = None

    def __post_init__(self):
        identifier(self.request_id)
        if not isinstance(self.camera, CameraId) or not isinstance(self.interval, Interval):
            raise ValueError("typed camera and interval required")
        if self.rendition is not None and not isinstance(self.rendition, RenditionId):
            raise ValueError("typed rendition required")


@dataclass(frozen=True)
class VerificationContext:
    camera: CameraId
    intervals: tuple[Interval, ...]

    def __post_init__(self):
        if not isinstance(self.camera, CameraId) or type(self.intervals) is not tuple:
            raise ValueError("typed camera and immutable intervals required")
        if not 1 <= len(self.intervals) <= 128 or not all(isinstance(i, Interval) for i in self.intervals):
            raise ValueError("context requires 1..128 actual intervals")
        if list(self.intervals) != sorted(self.intervals, key=lambda i: decimal_seconds(i.start)):
            raise ValueError("context intervals must be ordered")


def utc_timestamp(value: str) -> str:
    if type(value) is not str or len(value) > 40:
        raise ValueError("expected aware wall-clock timestamp")
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("invalid wall-clock timestamp") from exc
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError("wall-clock timestamp must be timezone-aware")
    try:
        return stamp.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except OverflowError as exc:
        raise ValueError("wall-clock timestamp outside UTC range") from exc


@dataclass(frozen=True)
class ObservationTime:
    """Monotonic seconds are comparable only within an identified runtime session."""

    session_id: str
    monotonic_seconds: str
    observed_at: str

    def __post_init__(self):
        identifier(self.session_id)
        if decimal_seconds(self.monotonic_seconds) < 0:
            raise ValueError("negative monotonic time")
        utc_timestamp(self.observed_at)

    def elapsed_since(self, earlier: "ObservationTime") -> Decimal:
        if self.session_id != earlier.session_id:
            raise ValueError("monotonic clocks belong to different sessions")
        with localcontext() as context:
            context.prec = 40  # Preserve all 20 integer + 9 fractional digits.
            delta = decimal_seconds(self.monotonic_seconds) - decimal_seconds(earlier.monotonic_seconds)
        if delta < 0:
            raise ValueError("monotonic clock moved backwards")
        return delta


@dataclass(frozen=True)
class EvidenceReference:
    """Content identity; a deployment-owned store resolves it to a bundle directory."""

    manifest_sha256: str

    def __post_init__(self):
        if type(self.manifest_sha256) is not str or not re.fullmatch(r"[0-9a-f]{64}", self.manifest_sha256):
            raise ValueError("invalid evidence digest")

    @property
    def value(self) -> str:
        return "sha256:" + self.manifest_sha256


@dataclass(frozen=True)
class HistoricalResult:
    request: RequestedRange
    context: VerificationContext | None
    verification: VerificationResult
    metadata_coverage: MetadataCoverage
    requested_coverage: RequestedCoverage
    reason: str
    detail: str = ""

    def __post_init__(self):
        if not isinstance(self.request, RequestedRange):
            raise ValueError("typed request required")
        for value, expected in ((self.verification, VerificationResult),
                                (self.metadata_coverage, MetadataCoverage),
                                (self.requested_coverage, RequestedCoverage)):
            if type(value) is not expected:
                raise ValueError("result axes must use their distinct enum types")
        identifier(self.reason)
        if type(self.detail) is not str or len(self.detail) > 2048:
            raise ValueError("detail must be bounded text")
        if self.context is None:
            if self.verification != VerificationResult.UNKNOWN:
                raise ValueError("a non-UNKNOWN verification requires an actual context")
        else:
            if not isinstance(self.context, VerificationContext):
                raise ValueError("typed context required")
            if self.context.camera != self.request.camera:
                raise ValueError("context camera differs from request")
            if not any(i.overlaps(self.request.interval) for i in self.context.intervals):
                raise ValueError("context does not intersect requested range")
        if self.requested_coverage == RequestedCoverage.FULL:
            if (self.verification != VerificationResult.VALID
                    or self.metadata_coverage != MetadataCoverage.FULL
                    or self.context is None
                    or not covers(self.request.interval, self.context.intervals)):
                raise ValueError("requested FULL requires VALID, metadata FULL and actual context coverage")
