"""Server-owned camera abstraction over existing exact playback mappings.

Metadata resolution never observes media. Display names are not binding keys.
"""
from dataclasses import dataclass, asdict
import hashlib
import json

from ..contracts import identifier


@dataclass(frozen=True)
class CanonicalSelector:
    camera_id: str
    stream: str
    context: str
    binding_identity: str

    def __post_init__(self):
        for value in (self.camera_id, self.stream, self.context, self.binding_identity):
            identifier(value)


def resolve_camera(camera_id, mappings, selectors=()):
    identifier(camera_id)
    peers = [m for m in mappings if m.frigate_camera == camera_id]
    choices = [s for s in selectors if s.camera_id == camera_id]
    base = {'camera_id': camera_id}
    if not peers:
        return base | {'coverage_state': 'NOT_CONFIGURED'}, None
    selected = None
    if not choices and len(peers) == 1:
        selected = peers[0]
    elif len(choices) == 1:
        s = choices[0]
        matches = [m for m in peers if (m.stream, m.context, m.binding.identity) ==
                   (s.stream, s.context, s.binding_identity)]
        if len(matches) == 1:
            selected = matches[0]
    # Duplicate exact tuples and conflicting logical identities cannot be canonical.
    if selected is not None:
        if (sum(m.key == selected.key for m in mappings) != 1 or
            any(m.binding != selected.binding for m in mappings if
                (m.binding.camera, m.binding.rendition) ==
                (selected.binding.camera, selected.binding.rendition))):
            selected = None
    if selected is None:
        return base | {'coverage_state': 'CONFIGURATION_AMBIGUOUS',
                       'reason': 'canonical_binding_unavailable'}, None
    identity = {'camera_id': camera_id, 'stream': selected.stream,
                'context': selected.context, 'binding_identity': selected.binding.identity,
                'policy': asdict(selected.binding.policy) if selected.binding.policy is not None else None}
    guard = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    return base | {'coverage_state': 'CONFIGURED', 'stream': selected.stream,
                   'context': selected.context, 'binding_guard': guard}, selected
