"""Auxiliary Catalog coverage projection. Not a signing finding or a timeline."""
import hashlib
from decimal import Decimal
from .composition import canonical

# First-version presentation policy. A gap is shown only when it is strictly longer.
PROJECTION_TOLERANCE = Decimal('10.0')


def project_catalog_gaps(camera, request_start, request_end, intervals, *, tolerance=PROJECTION_TOLERANCE):
    """Significant interior gaps between clipped Catalog coverage. No leading or trailing marker.

    Invalid interval rows are skipped. They do not become coverage. A bad request bound or
    camera raises ValueError so the caller can keep the projection unavailable.
    """
    if type(camera) is not str or not camera or any(ord(c) < 32 for c in camera):
        raise ValueError('camera')
    if not isinstance(tolerance, Decimal) or not tolerance.is_finite() or tolerance < 0:
        raise ValueError('tolerance')
    left_bound = _bound(request_start)
    right_bound = _bound(request_end)
    if left_bound >= right_bound:
        raise ValueError('request interval')
    spans = []
    for item in intervals:
        pair = _pair(item)
        if pair is None:
            continue
        start, end = _finite(pair[0]), _finite(pair[1])
        if start is None or end is None or start >= end:
            continue
        left, right = max(start, left_bound), min(end, right_bound)
        if left < right:
            spans.append((left, right))
    spans.sort(key=lambda span: (span[0], span[1]))
    merged = []
    for left, right in spans:
        if not merged or left > merged[-1][1]:
            merged.append([left, right])
        elif right > merged[-1][1]:
            merged[-1][1] = right
    gaps = []
    for earlier, later in zip(merged, merged[1:]):
        start, end = earlier[1], later[0]
        duration = end - start
        if duration > tolerance:
            gaps.append(_gap(camera, start, end, duration))
    return gaps


def _gap(camera, start, end, duration):
    start_text, end_text = _text(start), _text(end)
    return dict(type='GAP', gap_id=_gap_id(camera, start_text, end_text), camera=camera,
                start=start_text, end=end_text, duration=_text(duration))


def _gap_id(camera, start, end):
    return hashlib.sha256(canonical({'camera': camera, 'start': start, 'end': end})).hexdigest()


def _bound(value):
    parsed = _finite(value)
    if parsed is None:
        raise ValueError('request interval')
    return parsed


def _pair(item):
    if item is None:
        return None
    if type(item) is dict:
        return item.get('start'), item.get('end')
    if type(item) in (tuple, list) and len(item) == 2:
        return item[0], item[1]
    start, end = getattr(item, 'start', None), getattr(item, 'end', None)
    if start is None and end is None:
        return None
    return start, end


def _finite(value):
    if isinstance(value, bool) or isinstance(value, float) or value is None or type(value) not in (str, int, Decimal):
        return None
    try:
        parsed = value if isinstance(value, Decimal) else Decimal(value)
    except Exception:
        return None
    if not parsed.is_finite():
        return None
    return parsed


def _text(value):
    return format(value, 'f')
