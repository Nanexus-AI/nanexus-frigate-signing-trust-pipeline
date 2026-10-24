"""Protocol surface only. Authentication is trusted-adapter owned.

Job IDs and browser eligibility are never authority. Every operation/read must
be authorized for the provider-qualified camera; credentials stay adapter-local.
No real provider, filesystem access, staging or media reader is implemented.
"""
from dataclasses import dataclass
from typing import Protocol
from .contracts import Camera, Interval, Quality, Rational, Record


@dataclass(frozen=True)
class Capabilities(Record):
    camera: Camera
    provider_profile: str
    authorization_eligible: bool
    bounded_discovery: bool
    stable_access: bool
    qualified_historical_anchor: bool
    compressed_order: bool
    # Unknown profile facts remain unknown, including before media inspection.
    codec_hint: str | None
    layout_hint: str | None


@dataclass(frozen=True)
class Bounds(Record):
    max_items: int
    max_item_bytes: int
    max_context_items: int
    max_context_bytes: int
    max_runs: int
    max_work_units: int
    deadline_monotonic: Rational
    resource_profile: str

    def __post_init__(self):
        super().__post_init__()
        if min(self.max_items, self.max_item_bytes, self.max_runs, self.max_work_units) <= 0:
            raise ValueError('positive work bounds required')


@dataclass(frozen=True)
class DiscoveryRequest(Record):
    camera: Camera
    requested: Interval
    coordinate: str
    bounds: Bounds
    cursor: str | None


@dataclass(frozen=True)
class Item(Record):
    item_id: str
    generation: str
    camera: Camera
    metadata_interval: Interval | None
    coordinate: str | None
    size_bytes: int | None
    size_exact: bool
    codec_hint: str | None
    layout_hint: str | None
    order_quality: Quality
    anchor: Rational | None
    origin_policy: str | None

    def __post_init__(self):
        super().__post_init__()
        if self.size_exact and self.size_bytes is None:
            raise ValueError('exact size cannot be unknown')


@dataclass(frozen=True)
class InventoryPage(Record):
    inventory_id: str
    cut_id: str
    provider_profile: str
    camera: Camera
    scope: Interval
    completeness: Quality
    consistency: Quality
    items: tuple[Item, ...]
    predecessor_refs: tuple[str, ...]
    successor_refs: tuple[str, ...]
    ambiguous_refs: tuple[str, ...]
    next_cursor: str | None
    terminal: bool

    def __post_init__(self):
        super().__post_init__()
        if self.terminal != (self.next_cursor is None):
            raise ValueError('explicit page termination required')
        if any(item.camera != self.camera for item in self.items):
            raise ValueError('inventory camera mismatch')


@dataclass(frozen=True)
class StableHandle(Record):
    camera: Camera
    item_id: str
    generation: str
    handle_id: str
    actual_bytes: int
    sha256: str
    qualification: Quality
    cleanup_owner: str

    def __post_init__(self):
        super().__post_init__()
        import re
        if not re.fullmatch('[0-9a-f]{64}', self.sha256):
            raise ValueError('stable processing-byte digest required')


class Cancellation(Protocol):
    def cancelled(self) -> bool: ...


class HistoricalProvider(Protocol):
    """Trusted adapter must enforce bounds, cancellation and access revocation.

    Read returns at most max_bytes, acquire validates source generation and
    processing-byte identity, and release cleans owned staging. These are
    obligations for later implementation, not guarantees proved by these types.
    Incomplete discovery never proves absence; ambiguous composition stops.
    """
    def capabilities(self, camera: Camera) -> Capabilities: ...
    def discover(self, request: DiscoveryRequest, cancel: Cancellation) -> InventoryPage: ...
    def acquire(self, item: Item, bounds: Bounds, cancel: Cancellation) -> StableHandle: ...
    def read(self, handle: StableHandle, offset: int, max_bytes: int,
             cancel: Cancellation) -> bytes: ...
    def release(self, handle: StableHandle) -> None: ...
