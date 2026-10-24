"""Conservative supports only. Origin qualification and composition are on HOLD."""
from decimal import Decimal
from .contracts import Placement, PlacementStatus, Quality, Support


class AdmissionFailure(RuntimeError):
    pass


def unplaced(source_ref):
    return (Support('sample', (source_ref,), sample_ref=source_ref),
            Placement(PlacementStatus.UNPLACED, None, None))


def missing_order(scope_ref, left_ref=None, right_ref=None):
    # A synthetic missing entry has no received ordinal, packet, PTS or extent.
    return (Support('order', (scope_ref,), left_ref=left_ref, right_ref=right_ref,
                    missing_count=1), Placement(PlacementStatus.UNPLACED, None, None))


def metadata_gaps(page):
    """Gaps only in this complete metadata coordinate/cut, never signing gaps."""
    if page.completeness is not Quality.SATISFIED or page.consistency is not Quality.SATISFIED:
        raise AdmissionFailure('incomplete-inventory')
    if page.ambiguous_refs:
        raise AdmissionFailure('ambiguous-overlap')
    start, end = Decimal(page.scope.start), Decimal(page.scope.end)
    ranges = []
    for item in page.items:
        if item.metadata_interval is None or item.coordinate != 'host-metadata-unix-seconds':
            raise AdmissionFailure('unknown-metadata-coordinate')
        left, right = Decimal(item.metadata_interval.start), Decimal(item.metadata_interval.end)
        if left < end and right > start:
            ranges.append((max(start, left), min(end, right)))
    cursor, gaps = start, []
    for left, right in sorted(ranges):
        if left < cursor:
            raise AdmissionFailure('ambiguous-overlap')
        if left > cursor: gaps.append((cursor, left))
        cursor = right
    if cursor < end: gaps.append((cursor, end))
    return tuple(gaps)


def admit_single_file(page, item_ids):
    metadata_gaps(page)  # Completeness/overlap required even for admission.
    if len(item_ids) != 1:
        raise AdmissionFailure('multi-file-run-unqualified')
    selected = [i for i in page.items if i.item_id == item_ids[0]]
    if len(selected) != 1:
        raise AdmissionFailure('unknown-item')
    # Single-item execution is not proof of a complete signing epoch.
    return selected[0]
