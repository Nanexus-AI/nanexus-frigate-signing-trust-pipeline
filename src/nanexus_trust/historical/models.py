"""Data exchanged by metadata adapters, finite executors and the application."""

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Protocol

from ..contracts import CameraId, EvidenceReference, HistoricalResult, Interval, VerificationResult, identifier
from ..evidence import Component, canonical_json
import hashlib


class Availability(str, Enum):
    READY = "READY"
    NOT_READY = "NOT_READY"
    MISSING = "MISSING"
    UNAVAILABLE = "UNAVAILABLE"


class HistoricalError(Exception):
    """Operational failure with a portable reason, never a media INVALID verdict."""

    def __init__(self, reason: str):
        self.reason = identifier(reason)
        super().__init__(reason)


@dataclass(frozen=True)
class Limits:
    max_files: int = 3
    max_expansions: int = 2
    max_bytes: int = 16 * 1024 * 1024
    max_rows: int = 1000
    max_inventory_bytes: int = 64 * 1024 * 1024
    timeout_seconds: float = 15
    max_tool_output: int = 8 * 1024 * 1024

    def __post_init__(self):
        for value, ceiling in ((self.max_files, 128), (self.max_expansions, 127),
                               (self.max_bytes, 32 * 1024 * 1024), (self.max_rows, 10000),
                               (self.max_inventory_bytes, 128 * 1024 * 1024),
                               (self.max_tool_output, 32 * 1024 * 1024)):
            if type(value) is not int or not 0 <= value <= ceiling:
                raise ValueError("invalid resource bound")
        if min(self.max_files, self.max_bytes, self.max_rows, self.max_tool_output, self.max_inventory_bytes) == 0:
            raise ValueError("positive resource bounds required")
        if type(self.timeout_seconds) not in (float, int) or not 0 < self.timeout_seconds <= 60:
            raise ValueError("invalid tool timeout")


@dataclass(frozen=True)
class RetainedDescriptor:
    recording_id: str
    camera: CameraId
    interval: Interval
    relative_path: str
    availability: Availability
    size: int = 0
    sha256: str | None = None


@dataclass(frozen=True)
class Inventory:
    descriptors: tuple[RetainedDescriptor, ...]
    metadata_complete: bool


class MetadataSource(Protocol):
    def load(self, camera: CameraId, limits: Limits) -> Inventory: ...

    def read(self, descriptor: RetainedDescriptor, limits: Limits) -> bytes: ...


@dataclass(frozen=True)
class Candidate:
    descriptors: tuple[RetainedDescriptor, ...]

    @property
    def byte_count(self):
        return sum(d.size for d in self.descriptors)

    @property
    def identity(self):
        # Physical paths and mount locations are deliberately excluded.
        basis = [{"id": d.recording_id, "camera": d.camera.value,
                  "start": d.interval.start, "end": d.interval.end,
                  "sha256": d.sha256, "size": d.size} for d in self.descriptors]
        return hashlib.sha256(canonical_json(basis)).hexdigest()


@dataclass(frozen=True)
class BackendObservation:
    verification: VerificationResult
    integrity: str
    continuity: str
    completeness: str
    pending_frames: int | None = None
    pending_nals: int | None = None
    artifacts: dict[str, tuple[str, bytes]] = field(default_factory=dict)

    def __post_init__(self):
        if type(self.verification) is not VerificationResult:
            raise HistoricalError("BACKEND_UNSUPPORTED")
        pending = (self.pending_frames, self.pending_nals)
        if any(v is not None and (type(v) is not int or v < 0) for v in pending):
            raise HistoricalError("BACKEND_UNSUPPORTED")
        if self.verification is VerificationResult.VALID and (
                (self.integrity, self.continuity, self.completeness) != ("ok", "intact", "complete")
                or pending != (0, 0)):
            raise HistoricalError("BACKEND_UNSUPPORTED")
        if self.verification is VerificationResult.INVALID and self.integrity != "not_ok":
            raise HistoricalError("BACKEND_UNSUPPORTED")

    @property
    def expandable(self):
        return (self.verification is VerificationResult.PARTIAL and self.integrity == "ok"
                and self.continuity == "intact" and self.completeness == "incomplete"
                and type(self.pending_frames) is int and self.pending_frames > 0
                and type(self.pending_nals) is int and self.pending_nals > 0)


class VerificationBackend(Protocol):
    component: Component

    def verify(self, annex_b: Path, limits: Limits) -> BackendObservation: ...


class CandidateExecutor(Protocol):
    def execute(self, candidate: Candidate, source: MetadataSource, limits: Limits) -> BackendObservation: ...


@dataclass(frozen=True)
class HistoricalOperation:
    result: HistoricalResult
    progression: tuple[dict, ...]
    evidence_reference: EvidenceReference | None
    evidence_reason: str
