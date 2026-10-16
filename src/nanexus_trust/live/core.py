"""Session-aware bounded observation; no transport, crypto, files or history."""

from dataclasses import asdict, dataclass
from decimal import Decimal, localcontext

from ..contracts import ObservationTime, SigningStatus, decimal_seconds
from .parser import Incremental, Unsupported, accepted
from .policy import LiveSigningPolicy, ParserLimits


@dataclass(frozen=True)
class LiveResult:
    camera: str | None
    rendition: str | None
    status: SigningStatus
    reason: str
    observed_at: str
    evidence_age_ms: Decimal | None
    last_evidence_at: str | None
    session_id: str | None
    mapping_id: str | None
    policy_id: str | None
    media_observed: bool
    signing_evidence_observed: bool

    def to_dict(self):
        result = asdict(self)
        result["status"] = self.status.value
        result["evidence_age_ms"] = str(self.evidence_age_ms) if self.evidence_age_ms is not None else None
        return result


class LiveObserver:
    def __init__(self, policy: LiveSigningPolicy | None):
        if policy is not None and not isinstance(policy, LiveSigningPolicy):
            raise ValueError("explicit Live policy required")
        self._policy = policy
        self._parser = Incremental(policy.parser_limits if policy else ParserLimits())
        self.session_id = None
        self._last_time = Decimal(-1)
        self._media = self._evidence = None
        self._reason = "startup"
        self._open = False
        self._media_count = self._signing_count = 0

    def _time(self, at: ObservationTime, *, new_session=False):
        if not isinstance(at, ObservationTime):
            raise ValueError("explicit observation time required")
        now = decimal_seconds(at.monotonic_seconds)
        if now < self._last_time:
            raise ValueError("receipt clock moved backwards")
        if not new_session and self.session_id is not None and at.session_id != self.session_id:
            raise ValueError("observation belongs to another session")
        self._last_time = now
        return now

    def begin(self, at: ObservationTime):
        self._time(at, new_session=True)
        if at.session_id == self.session_id:
            raise ValueError("replacement requires a new session identity")
        self._reason = "startup" if self.session_id is None else "reconnect"
        self.session_id = at.session_id
        self._media = self._evidence = None
        self._media_count = self._signing_count = 0
        self._open = True
        self._parser.clear()

    def feed(self, data: bytes, at: ObservationTime):
        self._time(at)
        if not self._open or self.session_id is None:
            raise ValueError("begin a session before feeding data")
        if type(data) is not bytes:
            raise ValueError("immutable bounded byte input required")
        if self._reason not in (None, "startup", "reconnect"):
            return  # A supported parser uncertainty latches until a fresh session.
        try:
            for nal in self._parser.feed(data):
                structures = accepted(nal, self._parser.limits)
                if 1 <= nal[0] & 31 <= 5:
                    self._media = at
                    self._media_count = min(2**63 - 1, self._media_count + 1)
                if structures:
                    self._evidence = at
                    self._signing_count = min(2**63 - 1, self._signing_count + len(structures))
                self._reason = None
        except (ValueError, Unsupported) as exc:
            self._reason = "unsupported_signing_structure" if isinstance(exc, Unsupported) else "parser_uncertainty"
            self._parser.clear()

    def end(self, at: ObservationTime, reason="observation_discontinuity"):
        self._time(at)
        self._open = False
        self._reason = reason
        self._parser.clear()
        # Ended observations are factual diagnostics only; begin clears them.

    def snapshot(self, at: ObservationTime, *, camera=None, rendition=None, mapping_id=None):
        now = self._time(at)
        age = at.elapsed_since(self._evidence) if self._evidence else None
        status = SigningStatus.UNKNOWN
        reason = self._reason
        policy = self.policy
        if reason is None:
            if policy is None:
                reason = "freshness_unconfigured"
            elif self._media is None:
                reason = "no_media_observed"
            elif at.elapsed_since(self._media) > Decimal(str(policy.media_silence_limit)):
                reason = "observer_unavailable"
            elif age is None:
                reason = "no_signing_evidence_in_window"
            elif age > Decimal(str(policy.observation_horizon)):
                reason = "evidence_stale"
            elif age > Decimal(str(policy.active_freshness_limit)):
                status, reason = SigningStatus.DEGRADED, "signing_evidence_overdue"
            else:
                status, reason = SigningStatus.ACTIVE, "recent_signing_evidence"
        elif policy is None and reason == "startup":
            reason = "freshness_unconfigured"
        horizon = Decimal(str(policy.observation_horizon)) if policy else None

        def within(event):
            return event is not None and (horizon is None or at.elapsed_since(event) <= horizon)

        with localcontext() as context:
            context.prec = 40
            age_ms = age * 1000 if age is not None else None
        return LiveResult(camera, rendition, status, reason, at.observed_at,
                          age_ms,
                          self._evidence.observed_at if self._evidence else None,
                          self.session_id, mapping_id, policy.policy_id if policy else None,
                          within(self._media), within(self._evidence))

    @property
    def policy(self):
        return self._policy

    @property
    def diagnostics(self):
        """Deployment/test-only bounded scalars; never included in caller results."""
        return {"buffer_bytes": len(self._parser.buf), "peak_buffer_bytes": self._parser.peak,
                "media_count": self._media_count, "signing_count": self._signing_count}
