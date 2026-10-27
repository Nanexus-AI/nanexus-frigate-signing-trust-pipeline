"""Accepted query_coverage partition. Not a signing result and not a clock origin.

Live-session jobs pass the user query as both query and dataset so the document
has no corpus-manifest outside region. Captured-corpus manifest binding is not
implemented here.
"""
import re
from decimal import Decimal

_TEXT = re.compile(r'-?[0-9]{1,20}(?:\.[0-9]{1,9})?')


def decimal_text(value):
    number = value if isinstance(value, Decimal) else Decimal(value)
    if isinstance(value, bool) or not number.is_finite():
        raise ValueError('decimal')
    text = format(number, 'f')
    if '.' in text:
        whole, frac = text.split('.', 1)
        frac = frac.rstrip('0')
        text = whole if not frac else whole + '.' + frac
    if text in ('-0',):
        text = '0'
    if not _TEXT.fullmatch(text):
        raise ValueError('decimal')
    return text


def _cut(value, low, high):
    if value < low:
        return low
    if value > high:
        return high
    return value


def coverage_segments(query_start, query_end, dataset_start, dataset_end, spans, planned):
    """Partition the full query. Drawing clips at the query and dataset edges."""
    q0, q1 = Decimal(query_start), Decimal(query_end)
    d0, d1 = Decimal(dataset_start), Decimal(dataset_end)
    if not q0 < q1 or not d0 < d1:
        raise ValueError('interval')
    points = {q0, q1, _cut(d0, q0, q1), _cut(d1, q0, q1)}
    clipped = []
    for start, end, recording, finding in spans:
        s, e = Decimal(start), Decimal(end)
        if not s < e:
            continue
        left, right = max(s, q0, d0), min(e, q1, d1)
        if left < right:
            clipped.append((left, right, recording, finding))
            points.add(left)
            points.add(right)
    ordered = sorted(points)
    raw = []
    for left, right in zip(ordered, ordered[1:]):
        if not left < right:
            continue
        if right <= d0 or left >= d1:
            raw.append(('outside', left, right, None, None))
            continue
        cover = [item for item in clipped if item[0] <= left and item[1] >= right]
        if cover:
            findings = {item[3] for item in cover}
            finding = findings.pop() if len(findings) == 1 else None
            recording = cover[0][2] if len(cover) == 1 else None
            raw.append(('recording', left, right, recording, finding))
        elif planned:
            raw.append(('gap', left, right, None, None))
        else:
            raw.append(('undiscovered', left, right, None, None))
    merged = []
    for kind, left, right, recording, finding in raw:
        if merged and merged[-1][0] == kind and merged[-1][3] == recording and merged[-1][4] == finding and merged[-1][2] == left:
            merged[-1][2] = right
        else:
            merged.append([kind, left, right, recording, finding])
    return tuple(
        {'kind': kind, 'start': decimal_text(left), 'end': decimal_text(right),
         **({'recording': recording} if recording else {}),
         **({'finding': finding} if finding else {})}
        for kind, left, right, recording, finding in merged)


def coverage_document(query_start, query_end, dataset_start, dataset_end, spans, planned):
    return {
        'schema': 'nanexus-hv2-query-coverage/1',
        'query': {'start': decimal_text(query_start), 'end': decimal_text(query_end)},
        'dataset': {'start': decimal_text(dataset_start), 'end': decimal_text(dataset_end)},
        'segments': list(coverage_segments(query_start, query_end, dataset_start, dataset_end, spans, planned)),
    }
