"""Synthetic structural evidence only: signatures are deliberately not verified."""

from contextlib import ExitStack
from dataclasses import replace
from decimal import Decimal
import json
import socket
import sqlite3
import subprocess
import unittest
from unittest.mock import patch

from nanexus_trust.contracts import CameraId, ObservationTime, RenditionId, SigningStatus
from nanexus_trust.live import Binding, LiveObserver, LiveService, LiveSigningPolicy, ObservationEvent, ParserLimits

PREFIX = b"\0\0\0\1"
MEDIA = b"\x65\x88" + PREFIX


def structural_sei(version=2):
    general = bytearray(91)
    general[0] = version
    general[4] = 1
    general[21:25] = (1).to_bytes(4, "big")
    general[25:27] = (15).to_bytes(2, "big")
    signature = b"\x01\x00\x01\xaa"  # Structural placeholder, no cryptographic claim.
    payload = (bytes.fromhex("005bc93f2d715e95ada4796f90877a6f") + b"\0"
               + b"\x01" + len(general).to_bytes(2, "big") + general
               + b"\x03" + len(signature).to_bytes(2, "big") + signature)
    rbsp = bytes([5, len(payload)]) + payload + b"\x80"
    ebsp = bytearray(); zeros = 0
    for value in rbsp:
        if zeros >= 2 and value <= 3:
            ebsp.append(3); zeros = 0
        ebsp.append(value)
        zeros = zeros + 1 if value == 0 else 0
    return b"\x06" + ebsp + PREFIX


SIGNING = structural_sei()
POLICY = LiveSigningPolicy("test-policy", 0.1, 1.0, 4.0, 1.0)


def at(value, session="session-1"):
    return ObservationTime(session, str(value), "2026-01-01T00:00:00Z")


class CoreTests(unittest.TestCase):
    def observer(self, policy=POLICY):
        core = LiveObserver(policy)
        core.begin(at(0))
        core.feed(PREFIX, at(0))
        return core

    def test_initial_media_only_and_first_fresh_evidence(self):
        core = self.observer()
        self.assertIs(core.snapshot(at(0)).status, SigningStatus.UNKNOWN)
        core.feed(MEDIA, at(0))
        self.assertEqual(core.snapshot(at(0)).reason, "no_signing_evidence_in_window")
        core.feed(SIGNING, at(0))
        self.assertIs(core.snapshot(at(0)).status, SigningStatus.ACTIVE)

    def test_fresh_stale_resumed_and_horizon_edges(self):
        core = self.observer(); core.feed(MEDIA + SIGNING, at(0))
        core.feed(MEDIA, at(1))
        self.assertIs(core.snapshot(at(1)).status, SigningStatus.ACTIVE)
        core.feed(MEDIA, at("1.000000001"))
        self.assertIs(core.snapshot(at("1.000000001")).status, SigningStatus.DEGRADED)
        core.feed(MEDIA, at(4))
        self.assertIs(core.snapshot(at(4)).status, SigningStatus.DEGRADED)
        core.feed(MEDIA, at("4.000000001"))
        self.assertEqual(core.snapshot(at("4.000000001")).reason, "evidence_stale")
        core.feed(SIGNING, at(5))
        self.assertIs(core.snapshot(at(5)).status, SigningStatus.ACTIVE)

    def test_no_media_progress_overrides_freshness(self):
        core = self.observer(); core.feed(MEDIA + SIGNING, at(0))
        self.assertIs(core.snapshot(at(1)).status, SigningStatus.ACTIVE)
        self.assertEqual(core.snapshot(at("1.000000001")).reason, "observer_unavailable")

    def test_disconnect_eof_replacement_and_old_session_cannot_revive(self):
        for reason in ("observation_discontinuity", "observer_unavailable"):
            core = self.observer(); core.feed(MEDIA + SIGNING, at(0))
            core.end(at(1), reason)
            self.assertIs(core.snapshot(at(1)).status, SigningStatus.UNKNOWN)
            core.begin(at(2, "session-2"))
            self.assertIsNone(core.snapshot(at(2, "session-2")).evidence_age_ms)
            with self.assertRaises(ValueError): core.feed(MEDIA + SIGNING, at(2))
            self.assertIs(core.snapshot(at(2, "session-2")).status, SigningStatus.UNKNOWN)
            core.feed(PREFIX + MEDIA + SIGNING, at(2, "session-2"))
            self.assertIs(core.snapshot(at(2, "session-2")).status, SigningStatus.ACTIVE)

    def test_explicit_session_replacement_without_disconnect(self):
        core = self.observer(); core.feed(MEDIA + SIGNING, at(0))
        core.begin(at(1, "replacement"))
        self.assertIs(core.snapshot(at(1, "replacement")).status, SigningStatus.UNKNOWN)
        with self.assertRaises(ValueError): core.begin(at(1, "replacement"))

    def test_fragmentation_supported_structure_and_uuid_only(self):
        core = self.observer()
        for value in MEDIA + SIGNING:
            core.feed(bytes([value]), at(0))
        self.assertIs(core.snapshot(at(0)).status, SigningStatus.ACTIVE)
        core = self.observer()
        core.feed(MEDIA + b"\x06\x05\x10" + bytes.fromhex("005bc93f2d715e95ada4796f90877a6f") + b"\x80" + PREFIX, at(0))
        self.assertIs(core.snapshot(at(0)).status, SigningStatus.UNKNOWN)

    def test_unsupported_structure_latches_unknown_until_new_session(self):
        core = self.observer(); core.feed(MEDIA + structural_sei(3), at(0))
        self.assertEqual(core.snapshot(at(0)).reason, "unsupported_signing_structure")
        core.feed(MEDIA + SIGNING, at(1))
        self.assertIs(core.snapshot(at(1)).status, SigningStatus.UNKNOWN)
        core.begin(at(2, "new")); core.feed(PREFIX + MEDIA + SIGNING, at(2, "new"))
        self.assertIs(core.snapshot(at(2, "new")).status, SigningStatus.ACTIVE)

    def test_missing_policy_never_active_and_policy_is_readonly(self):
        core = self.observer(None); core.feed(MEDIA + SIGNING, at(0))
        self.assertEqual(core.snapshot(at(0)).reason, "freshness_unconfigured")
        with self.assertRaises(AttributeError): core.policy = POLICY
        for values in ((0, 1, 4, 1), (0.1, 1, 1, 1), (0.1, float("nan"), 4, 1)):
            with self.assertRaises(ValueError): LiveSigningPolicy("bad", *values)
        with self.assertRaises(ValueError): replace(POLICY, policy_id="rtsp://user:secret@example/stream")

    def test_continued_fresh_evidence_and_decimal_context_independence(self):
        from decimal import localcontext
        core = self.observer(); core.feed(MEDIA + SIGNING, at(0))
        core.feed(MEDIA + SIGNING, at(1))
        with localcontext() as context:
            context.prec = 4
            result = core.snapshot(at("1.123456789"))
        self.assertIs(result.status, SigningStatus.ACTIVE)
        self.assertEqual(result.evidence_age_ms, Decimal("123.456789000"))

    def test_chunk_nal_bounds_and_constant_scalar_state(self):
        policy = replace(POLICY, parser_limits=ParserLimits(64, 32, 4))
        core = self.observer(policy)
        core.feed(b"x" * 33, at(0))
        self.assertIs(core.snapshot(at(0)).status, SigningStatus.UNKNOWN)
        core = self.observer(policy)
        for _ in range(3): core.feed(b"x" * 32, at(0))
        self.assertIs(core.snapshot(at(0)).status, SigningStatus.UNKNOWN)
        self.assertEqual(core.diagnostics["buffer_bytes"], 0)
        self.assertLessEqual(core.diagnostics["peak_buffer_bytes"], 64 + 32 + 4)
        core = self.observer()
        for _ in range(2000): core.feed(MEDIA + SIGNING, at(0))
        self.assertEqual(core.diagnostics["media_count"], 2000)
        self.assertLessEqual(core.diagnostics["buffer_bytes"], 4)
        self.assertFalse(any(isinstance(value, (list, dict)) for value in vars(core).values()))

    def test_clock_and_result_serialization(self):
        core = self.observer(); core.feed(MEDIA + SIGNING, at(1))
        with self.assertRaises(ValueError): core.snapshot(at(0))
        result = core.snapshot(at(1))
        self.assertEqual(result.evidence_age_ms, Decimal(0))
        json.dumps(result.to_dict())


class Clock:
    def __init__(self): self.value = "0"
    def __call__(self): return self.value, "2026-01-01T00:00:00Z"


class Source:
    def __init__(self, events): self.events = list(events); self.closed = False
    def next_event(self, now):
        if self.closed or not self.events or Decimal(self.events[0].at.monotonic_seconds) > Decimal(now): return None
        return self.events.pop(0)
    def close(self): self.closed = True


def events(session="session-1", value="0", *, evidence=True):
    stamp = at(value, session)
    return [ObservationEvent("begin", stamp), ObservationEvent("bytes", stamp, PREFIX),
            ObservationEvent("bytes", stamp, MEDIA + (SIGNING if evidence else b""))]


class FacadeTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.binding = Binding(CameraId("front"), RenditionId("record"), "source-1", "rev-1", POLICY)

    def service(self, source): return LiveService((self.binding,), lambda binding: source, clock=self.clock)

    def test_caller_only_camera_rendition_and_no_raw_configuration(self):
        service = self.service(Source(events()))
        result = service.get_status("front", "record")
        self.assertIs(result.status, SigningStatus.ACTIVE)
        self.assertEqual(result.session_id, "session-1")
        self.assertEqual(result.mapping_id, self.binding.identity)
        serialized = json.dumps(result.to_dict())
        for value in ("rtsp", "source-1", "max_nal", "freshness_limit"):
            self.assertNotIn(value, serialized)

    def test_disconnect_reconnect_then_fresh_evidence(self):
        source = Source(events() + [ObservationEvent("disconnect", at(1)),
                                  ObservationEvent("begin", at(2, "new")),
                                  ObservationEvent("bytes", at(3, "new"), PREFIX + MEDIA + SIGNING)])
        service = self.service(source)
        self.assertIs(service.get_status("front", "record").status, SigningStatus.ACTIVE)
        for value in ("1", "2"):
            self.clock.value = value
            result = service.get_status("front", "record")
            self.assertIs(result.status, SigningStatus.UNKNOWN)
        self.assertIsNone(result.evidence_age_ms)
        self.clock.value = "3"
        self.assertIs(service.get_status("front", "record").status, SigningStatus.ACTIVE)

    def test_ambiguity_missing_mapping_and_invalid_request(self):
        source = Source(events()); service = self.service(source)
        service.get_status("front", "record")
        service.configure((self.binding, self.binding))
        self.assertTrue(source.closed)
        self.assertEqual(service.get_status("front", "record").reason, "mapping_unavailable")
        self.assertEqual(service.get_status("front", "missing").reason, "mapping_unavailable")
        result = service.get_status("invalid/path", "record")
        self.assertEqual(result.reason, "invalid_request"); self.assertIsNone(result.camera)

    def test_source_rendition_policy_and_revision_remaps_reset(self):
        for changed in (replace(self.binding, source_id="source-2"),
                        replace(self.binding, rendition=RenditionId("other")),
                        replace(self.binding, revision="rev-2"),
                        replace(self.binding, policy=replace(POLICY, active_freshness_limit=2))):
            sources = [Source(events()), Source(events("new", evidence=False))]
            service = LiveService((self.binding,), lambda binding: sources.pop(0), clock=self.clock)
            old = service.get_status("front", "record")
            service.configure((changed,))
            result = service.get_status(changed.camera.value, changed.rendition.value)
            self.assertIs(result.status, SigningStatus.UNKNOWN)
            self.assertIsNone(result.evidence_age_ms)
            self.assertNotEqual(old.session_id, result.session_id)

    def test_old_session_event_and_backdated_event_disable_eligibility(self):
        for stale in (ObservationEvent("bytes", at(1, "old"), MEDIA + SIGNING),
                      ObservationEvent("bytes", at(0), MEDIA + SIGNING)):
            source = Source(events()); service = self.service(source)
            service.get_status("front", "record")
            self.clock.value = "1"; service.get_status("front", "record")
            source.events.append(stale)
            self.assertIs(service.get_status("front", "record").status, SigningStatus.UNKNOWN)
            self.assertTrue(source.closed)

    def test_stop_close_and_factory_unavailability(self):
        source = Source(events()); service = self.service(source)
        service.get_status("front", "record"); service.stop("front", "record")
        self.assertTrue(source.closed)
        self.assertEqual(service.get_status("front", "record").reason, "observation_stopped")
        service.close()
        self.assertEqual(service.get_status("front", "record").reason, "service_closed")
        def unavailable(binding): raise OSError("not available")
        service = LiveService((self.binding,), unavailable, clock=self.clock)
        self.assertEqual(service.get_status("front", "record").reason, "observer_unavailable")

    def test_mapping_and_per_query_event_bounds(self):
        with self.assertRaises(ValueError): LiveService((self.binding,) * 17, lambda b: None)
        source = Source(events() + [ObservationEvent("bytes", at(0), MEDIA)] * 260)
        service = self.service(source)
        self.assertEqual(service.get_status("front", "record").reason, "adapter_backlog")
        self.assertTrue(source.closed)

    def test_no_query_can_reuse_old_active_as_new_observation(self):
        service = self.service(Source(events()))
        self.assertIs(service.get_status("front", "record").status, SigningStatus.ACTIVE)
        self.clock.value = "2"
        self.assertIs(service.get_status("front", "record").status, SigningStatus.UNKNOWN)

    def test_live_never_calls_historical_crypto_files_database_network(self):
        service = self.service(Source(events()))
        with ExitStack() as stack:
            guards = [stack.enter_context(patch.object(module, name, side_effect=AssertionError("forbidden")))
                      for module, name in ((subprocess, "run"), (subprocess, "Popen"), (sqlite3, "connect"),
                                           (socket, "socket"), (socket, "create_connection"))]
            guards.append(stack.enter_context(patch("nanexus_trust.historical.service.HistoricalService.verify", side_effect=AssertionError("Historical"))))
            guards.append(stack.enter_context(patch("nanexus_trust.evidence.issue_bundle", side_effect=AssertionError("Historical evidence"))))
            self.assertIs(service.get_status("front", "record").status, SigningStatus.ACTIVE)
            for guard in guards: guard.assert_not_called()
