"""Closed, bounded V2 wire family; no legacy conversion or runtime integration."""
from dataclasses import fields, is_dataclass
from enum import Enum
import json
import types
from typing import get_args, get_origin, get_type_hints
from . import contracts as c

SCHEMA = 'nanexus-historical-v2-contracts'
VERSION = 1
MAX_DOCUMENT_BYTES = 1024 * 1024
MAX_DEPTH = 24


def _typed(value, annotation, decode=False, depth=0):
    if depth > MAX_DEPTH:
        raise ValueError('record nesting limit')
    origin, args = get_origin(annotation), get_args(annotation)
    if origin is types.UnionType:
        for option in args:
            try:
                return _typed(value, option, decode, depth + 1)
            except (ValueError, TypeError):
                pass
        raise ValueError('invalid optional/union value')
    if annotation is type(None):
        if value is not None:
            raise ValueError('expected null')
        return None
    if origin is tuple:
        if type(value) is not (list if decode else tuple) or len(value) > c.MAX_ITEMS:
            raise ValueError('bounded collection required')
        return tuple(_typed(v, args[0], decode, depth + 1) for v in value)
    if isinstance(annotation, type) and issubclass(annotation, Enum):
        if decode:
            if type(value) is not str:
                raise ValueError('enum string required')
            return annotation(value)
        if type(value) is not annotation:
            raise ValueError('distinct typed enum required')
        return value
    if is_dataclass(annotation):
        if decode:
            names = {f.name for f in fields(annotation)}
            if type(value) is not dict or set(value) != names:
                raise ValueError('missing or unknown fields')
            hints = get_type_hints(annotation)
            return annotation(**{k: _typed(v, hints[k], True, depth + 1) for k, v in value.items()})
        if type(value) is not annotation:
            raise ValueError('typed record required')
        return value
    if type(value) is not annotation:
        raise ValueError('wrong scalar type')
    if annotation is str:
        c.text(value)
    if annotation is int and abs(value) > c.MAX_INTEGER:
        raise ValueError('integer ceiling')
    return value


def validate_record(record):
    hints = get_type_hints(type(record))
    for field in fields(record):
        value = getattr(record, field.name)
        _typed(value, hints[field.name])
        # Signed ticks/numerators are valid; work/size/ordinal counters are not.
        if type(value) is int and field.name not in ('pts', 'numerator'):
            c.count(value)


def _plain(value):
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {f.name: _plain(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, tuple):
        return [_plain(v) for v in value]
    return value


def dumps(record):
    if not isinstance(record, c.Record):
        raise ValueError('V2 record required')
    data = json.dumps({'schema': SCHEMA, 'version': VERSION, 'type': type(record).__name__,
                       'record': _plain(record)}, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    if len(data) > MAX_DOCUMENT_BYTES:
        raise ValueError('document byte ceiling')
    return data


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate field')
        result[key] = value
    return result


def loads(data):
    if type(data) is not bytes or len(data) > MAX_DOCUMENT_BYTES:
        raise ValueError('bounded bytes required')
    try:
        obj = json.loads(data, object_pairs_hook=_object,
                         parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite number')))
        if type(obj) is not dict or set(obj) != {'schema', 'version', 'type', 'record'}:
            raise ValueError('closed envelope required')
        if obj['schema'] != SCHEMA or type(obj['version']) is not int or obj['version'] != VERSION:
            raise ValueError('unsupported schema/version')
        # Explicit module family, including provider contract records, never arbitrary imports.
        from . import provider
        registry = {cls.__name__: cls for module in (c, provider) for cls in vars(module).values()
                    if isinstance(cls, type) and issubclass(cls, c.Record) and is_dataclass(cls)}
        if type(obj['type']) is not str or obj['type'] not in registry:
            raise ValueError('unknown record type')
        return _typed(obj['record'], registry[obj['type']], True)
    except (TypeError, KeyError, UnicodeError, RecursionError) as exc:
        raise ValueError('malformed V2 document') from exc
