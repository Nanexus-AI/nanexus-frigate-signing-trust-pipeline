"""No implicit timing calibration or universal timing defaults."""
from dataclasses import dataclass,field
import math
from ..contracts import identifier

@dataclass(frozen=True)
class ParserLimits:
    max_nal_bytes: int=1024*1024
    max_chunk_bytes: int=65536
    max_messages_per_nal: int=64

    def __post_init__(self):
        for name,ceiling in [('max_nal_bytes',1024*1024),('max_chunk_bytes',65536),('max_messages_per_nal',64)]:
            value=getattr(self,name)
            if type(value) is not int or not 1<=value<=ceiling:
                raise ValueError(f'{name} must be an integer in [1,{ceiling}]')

@dataclass(frozen=True)
class LiveSigningPolicy:
    policy_id: str
    evaluation_interval: float
    active_freshness_limit: float
    observation_horizon: float
    media_silence_limit: float
    parser_limits: ParserLimits=field(default_factory=ParserLimits)

    def __post_init__(self):
        identifier(self.policy_id)
        for name in ('evaluation_interval','active_freshness_limit','observation_horizon','media_silence_limit'):
            value=getattr(self,name)
            if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<=0:
                raise ValueError(f'{name} must be finite and positive')
        if self.observation_horizon<=self.active_freshness_limit or self.observation_horizon<self.media_silence_limit:
            raise ValueError('require H > F and H >= M')
        if not isinstance(self.parser_limits,ParserLimits): raise ValueError('parser_limits must be ParserLimits')
