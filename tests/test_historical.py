"""Portable contract fixtures; no private media or installed Toolkit dependency."""

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

from nanexus_trust.contracts import CameraId, MetadataCoverage, RequestedCoverage, VerificationResult
from nanexus_trust.evidence import Component, RecognitionStatus
from nanexus_trust.frigate.metadata import FrozenFrigateSource
from nanexus_trust.historical.backend import ToolkitBackend, parse_toolkit, run_tool
from nanexus_trust.historical.executor import FiniteExecutor, reconstruct_mp4
from nanexus_trust.historical.models import (Availability, BackendObservation, Candidate, HistoricalError, Limits)
from nanexus_trust.historical.service import HistoricalService


def observation(verdict, pending=0, nals=0):
    return BackendObservation(VerificationResult(verdict), "not_ok" if verdict == "INVALID" else "ok",
                              "broken" if verdict == "INVALID" else "intact",
                              "incomplete" if pending else "complete", pending, nals)


class SequenceExecutor:
    def __init__(self, observations):
        self.observations = list(observations)
        self.candidates = []

    def execute(self, candidate, source, limits):
        self.candidates.append(candidate)
        for descriptor in candidate.descriptors:
            source.read(descriptor, limits)
        current = self.observations.pop(0)
        if isinstance(current, Exception):
            raise current
        return current


def reports(verdict="PARTIAL", pending=15):
    verify = {"schema_version": "0.1", "codec": "h264", "media_signing": {"present": True},
              "certificate_status": "ok", "public_key_has_changed": False, "source_authenticity": "not_established",
              "findings": [{"code": "SIGNING_KEY_PROVENANCE_OK", "message": "Public trust checked"}],
              "overall": verdict, "signature_integrity": "not_ok" if verdict == "INVALID" else "ok",
              "continuity": "broken" if verdict == "INVALID" else "intact",
              "verification_completeness": "incomplete" if pending else "complete"}
    counts = {}
    for unit, scale in (("frames", 1), ("nalus", 2)):
        counts.update({"number_of_received_" + unit: 180 * scale,
                       "number_of_validated_" + unit: (180 - pending) * scale,
                       "number_of_pending_" + unit: pending * scale})
    inspect = {"verification": verify, "schema_version": "0.1", "document_type": "media_signing_inspection",
               "accumulated_validation": counts}
    return verify, inspect


class HistoricalTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name); self.media = self.root / "media"; self.media.mkdir()
        self.fixtures = json.loads((Path(__file__).parent / "fixtures/historical-scenarios.json").read_text())

    def source(self, scenario="complete", **kwargs):
        fixture = self.fixtures[scenario]
        self.rows = []
        for index, (start, end) in enumerate(fixture["intervals"]):
            name = f"recording-{index}.mp4"
            (self.media / name).write_bytes(bytes([index + 1]) * 10)
            self.rows.append({"id": f"recording-{index}", "camera": "front", "start_time": start,
                              "end_time": end, "path": "/media/frigate/recordings/" + name, "closed": True})
        return FrozenFrigateSource(tuple(reversed(self.rows)), self.media, **kwargs)

    def service(self, source, observations, **kwargs):
        executor = SequenceExecutor(observations)
        service = HistoricalService(source, executor, Component("test-backend", "1"),
                                    self.root / "evidence", "trust-revision-1", **kwargs)
        return service, executor

    def test_portable_complete_and_incomplete_regression_fixtures(self):
        for scenario in ("complete", "incomplete"):
            with self.subTest(scenario=scenario):
                source = self.source(scenario); fixture = self.fixtures[scenario]
                observations = [observation(v, p, n) for v, p, n in zip(
                    fixture["verdicts"], fixture["pending_frames"], fixture["pending_nals"])]
                service, executor = self.service(source, observations)
                outcome = service.verify("front", *fixture["request"])
                self.assertIs(outcome.result.metadata_coverage, MetadataCoverage.FULL)
                self.assertEqual(outcome.result.verification.value, fixture["verdicts"][-1])
                self.assertEqual([len(c.descriptors) for c in executor.candidates], list(range(1, len(observations) + 1)))
                self.assertEqual(outcome.result.reason, "VALID_CONTEXT" if scenario == "complete" else "MEDIA_EXHAUSTED")
                expected = RequestedCoverage.FULL if scenario == "complete" else RequestedCoverage.INDETERMINATE
                self.assertIs(outcome.result.requested_coverage, expected)
                self.assertNotEqual(outcome.result.context.intervals[0], outcome.result.request.interval)
                with patch.object(executor, "execute", side_effect=AssertionError("reverification")):
                    recognized = service.recognize(outcome.evidence_reference)
                self.assertIs(recognized.status, RecognitionStatus.SUPPORTED)
                self.assertEqual(recognized.result, outcome.result)

    def test_partial_to_valid_then_stop(self):
        service, executor = self.service(self.source(), [observation("PARTIAL", 15, 30), observation("VALID")])
        result = service.verify("front", "2", "5")
        self.assertEqual(len(executor.candidates), 2)
        self.assertEqual(result.result.reason, "VALID_CONTEXT")

    def test_invalid_is_terminal_and_recognizable(self):
        service, executor = self.service(self.source(), [observation("INVALID")])
        result = service.verify("front", "2", "5")
        self.assertEqual(len(executor.candidates), 1)
        self.assertIs(result.result.verification, VerificationResult.INVALID)
        self.assertEqual(service.recognize(result.evidence_reference).result, result.result)

    def test_resource_bounds_are_unknown_not_media_invalid(self):
        for limits in (Limits(max_files=1), Limits(max_expansions=0), Limits(max_bytes=15)):
            with self.subTest(limits=limits):
                service, executor = self.service(self.source(), [observation("PARTIAL", 15, 30)], limits=limits)
                result = service.verify("front", "2", "5")
                self.assertEqual(result.result.reason, "RESOURCE_LIMIT")
                self.assertIs(result.result.verification, VerificationResult.UNKNOWN)
                self.assertEqual(len(executor.candidates), 1)

    def test_missing_unready_and_changed_media(self):
        for kind in ("missing", "unready", "changed"):
            source = self.source()
            if kind == "missing": (self.media / "recording-0.mp4").unlink()
            if kind == "unready":
                self.rows[0]["closed"] = False
                source = FrozenFrigateSource(tuple(self.rows), self.media)
            if kind == "changed":
                original = source.read
                def changed(descriptor, limits):
                    (self.media / "recording-0.mp4").write_bytes(b"different")
                    return original(descriptor, limits)
                source.read = changed
            service, _ = self.service(source, [observation("VALID")])
            result = service.verify("front", "2", "5")
            self.assertIs(result.result.verification, VerificationResult.UNKNOWN)
            self.assertIs(result.result.metadata_coverage, MetadataCoverage.FULL)
            self.assertIn(result.result.reason, ("MEDIA_UNAVAILABLE", "MEDIA_NOT_READY", "MEDIA_CHANGED"))

    def test_metadata_unknown_gap_and_absent_camera(self):
        source = self.source(coherent=False)
        service, _ = self.service(source, [])
        outcome = service.verify("front", "2", "5")
        self.assertIs(outcome.result.metadata_coverage, MetadataCoverage.INDETERMINATE)
        source = self.source()
        service, executor = self.service(source, [observation("VALID")])
        outcome = service.verify("front", "20", "25")
        self.assertIs(outcome.result.metadata_coverage, MetadataCoverage.NOT_FULL)
        self.assertIs(outcome.result.requested_coverage, RequestedCoverage.INDETERMINATE)
        self.assertEqual(len(executor.candidates[0].descriptors), 2)
        missing = service.verify("absent", "2", "5")
        self.assertEqual(missing.result.reason, "NO_MEDIA")

    def test_incomplete_metadata_never_promises_full(self):
        service, _ = self.service(self.source(complete=False), [observation("VALID")])
        outcome = service.verify("front", "2", "5")
        self.assertIs(outcome.result.verification, VerificationResult.VALID)
        self.assertIs(outcome.result.metadata_coverage, MetadataCoverage.INDETERMINATE)
        self.assertIs(outcome.result.requested_coverage, RequestedCoverage.INDETERMINATE)

    def test_backend_failures_and_inconsistent_partial_stop_conservatively(self):
        for item in (HistoricalError("BACKEND_UNAVAILABLE"), HistoricalError("BACKEND_TIMEOUT"),
                     BackendObservation(VerificationResult.PARTIAL, "ok", "intact", "incomplete", None, None),
                     BackendObservation(VerificationResult.UNKNOWN, "unknown", "unknown", "unknown")):
            service, executor = self.service(self.source(), [item])
            outcome = service.verify("front", "2", "5")
            self.assertIs(outcome.result.verification, VerificationResult.UNKNOWN)
            self.assertEqual(len(executor.candidates), 1)

    def test_ordering_candidate_identity_and_initial_multi_file_context(self):
        source = self.source(); inventory = source.load(CameraId("front"), Limits())
        self.assertEqual([d.recording_id for d in inventory.descriptors], ["recording-0", "recording-1", "recording-2"])
        service, executor = self.service(source, [observation("VALID")])
        service.verify("front", "2", "15")
        self.assertEqual(len(executor.candidates[0].descriptors), 2)
        first = executor.candidates[0]
        relocated = Candidate(tuple(replace(d, relative_path="different.mp4") for d in first.descriptors))
        self.assertEqual(first.identity, relocated.identity)

    def test_traversal_mount_escape_symlinks_and_duplicate_rows(self):
        self.source()
        for path in ("/media/frigate/recordings/../outside", "/other/file", "/media/frigate/recordings//file"):
            rows = [dict(self.rows[0], path=path)]
            service, executor = self.service(FrozenFrigateSource(tuple(rows), self.media), [])
            self.assertEqual(service.verify("front", "2", "5").result.reason, "UNSAFE_MEDIA_PATH")
            self.assertFalse(executor.candidates)
        link = self.media / "link.mp4"; link.symlink_to(self.media / "recording-0.mp4")
        row = dict(self.rows[0], path="/media/frigate/recordings/link.mp4")
        service, _ = self.service(FrozenFrigateSource((row,), self.media), [])
        self.assertEqual(service.verify("front", "2", "5").result.reason, "UNSAFE_MEDIA_PATH")
        service, _ = self.service(FrozenFrigateSource((self.rows[0], self.rows[0]), self.media), [])
        self.assertEqual(service.verify("front", "2", "5").result.reason, "METADATA_UNAVAILABLE")

    def test_evidence_relocation_tampering_and_missing_no_execution(self):
        service, executor = self.service(self.source("incomplete"), [observation("PARTIAL", 15, 30)] * 2)
        result = service.verify("front", "2", "5")
        relocated = self.root / "relocated"; shutil.move(service.evidence_root, relocated)
        service.evidence_root = relocated
        with patch.object(executor, "execute", side_effect=AssertionError("no fallback")), \
             patch.object(service.source, "load", side_effect=AssertionError("no metadata")):
            self.assertEqual(service.recognize(result.evidence_reference).result, result.result)
            artifact = next(relocated.glob("*/artifacts/progression.json")); artifact.write_bytes(b"bad")
            self.assertIs(service.recognize(result.evidence_reference).status, RecognitionStatus.UNKNOWN)

    def test_evidence_storage_failure_does_not_rewrite_verdict(self):
        service, _ = self.service(self.source(), [observation("VALID")])
        service.evidence_root.write_bytes(b"not a directory")
        outcome = service.verify("front", "2", "5")
        self.assertIs(outcome.result.verification, VerificationResult.VALID)
        self.assertIsNone(outcome.evidence_reference)
        self.assertEqual(outcome.evidence_reason, "EVIDENCE_UNAVAILABLE")

    def test_metadata_read_failure_and_inventory_bounds(self):
        service, executor = self.service(self.source(), [])
        with patch.object(service.source, "load", side_effect=OSError("unreadable")):
            outcome = service.verify("front", "2", "5")
        self.assertEqual(outcome.result.reason, "METADATA_UNAVAILABLE")
        for limits in (Limits(max_rows=1), Limits(max_inventory_bytes=1)):
            service, executor = self.service(self.source(), [], limits=limits)
            self.assertEqual(service.verify("front", "2", "5").result.reason, "RESOURCE_LIMIT")
            self.assertFalse(executor.candidates)

    def test_failed_execution_attempt_is_preserved(self):
        service, _ = self.service(self.source(), [HistoricalError("BACKEND_TIMEOUT")])
        outcome = service.verify("front", "2", "5")
        self.assertEqual(outcome.progression[0]["reason"], "BACKEND_TIMEOUT")
        self.assertEqual(outcome.progression[0]["verification"], "UNKNOWN")
        self.assertEqual(service.recognize(outcome.evidence_reference).result, outcome.result)


class ExecutorBoundaryTests(unittest.TestCase):
    def test_reconstruction_preserves_sei_only_sample(self):
        nals = [b"\x65picture", b"\x06signing"]
        packets = [len(n).to_bytes(4, "big") + n for n in nals]
        data = b"".join(packets)
        probe = {"streams": [{"index": 0, "codec_name": "h264", "nal_length_size": "4"}],
                 "packets": [{"stream_index": 0, "pos": "0", "size": str(len(packets[0]))},
                             {"stream_index": 0, "pos": str(len(packets[0])), "size": str(len(packets[1]))}]}
        self.assertEqual(reconstruct_mp4(data, probe, 100), b"".join(b"\0\0\0\1" + n for n in nals))
        with self.assertRaises(HistoricalError): reconstruct_mp4(data, probe, 4)
        probe["packets"][1]["pos"] = "0"
        with self.assertRaises(HistoricalError): reconstruct_mp4(data, probe, 100)

    def test_toolkit_parser_positive_partial_invalid_and_unmapped(self):
        for verdict, pending, codes in (("VALID", 0, (0, 0)), ("PARTIAL", 15, (4, 4)), ("INVALID", 0, (1, 1))):
            v, i = reports(verdict, pending)
            self.assertEqual(parse_toolkit(v, i, codes).verification.value, verdict)
        v, i = reports(); i["accumulated_validation"]["number_of_pending_frames"] = -1
        with self.assertRaises(HistoricalError): parse_toolkit(v, i, (4, 4))
        v, i = reports(); v["schema_version"] = "future"
        with self.assertRaises(HistoricalError): parse_toolkit(v, i, (4, 4))

    def test_configured_missing_tool_is_operational_failure(self):
        with self.assertRaises(HistoricalError) as error:
            run_tool(["/nonexistent-verification-tool"], Limits())
        self.assertEqual(error.exception.reason, "BACKEND_UNAVAILABLE")

    def test_tool_timeout_and_output_budget(self):
        for command, limits, reason in (
                ([sys.executable, "-c", "import time; time.sleep(3)"], Limits(timeout_seconds=0.05), "BACKEND_TIMEOUT"),
                ([sys.executable, "-c", "print('x' * 4096)"], Limits(max_tool_output=128), "RESOURCE_LIMIT")):
            with self.subTest(reason=reason), self.assertRaises(HistoricalError) as error:
                run_tool(command, limits)
            self.assertEqual(error.exception.reason, reason)

    def test_toolkit_adapter_records_reports_without_commands(self):
        v, i = reports()
        with patch("nanexus_trust.historical.backend.run_tool", side_effect=[(4, json.dumps(v).encode()), (4, json.dumps(i).encode())]):
            backend = ToolkitBackend(Path("tool"), Path("anchor.pem"), "1")
            observation = backend.verify(Path("candidate.h264"), Limits())
        self.assertEqual(set(observation.artifacts), {"verify.json", "inspect.json"})

    def test_finite_executor_reconstructs_and_delegates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); nal = b"\x06terminal"; raw = len(nal).to_bytes(4, "big") + nal
            (root / "a.mp4").write_bytes(raw)
            source = FrozenFrigateSource(({"id": "a", "camera": "front", "start_time": "0", "end_time": "10",
                                           "path": "/media/frigate/recordings/a.mp4", "closed": True},), root)
            inventory = source.load(CameraId("front"), Limits())
            probe = {"streams": [{"index": 0, "codec_name": "h264", "nal_length_size": "4"}],
                     "packets": [{"stream_index": 0, "pos": "0", "size": str(len(raw))}]}
            class Backend:
                def verify(self, path, limits):
                    assert path.read_bytes() == b"\0\0\0\1" + nal
                    return observation("VALID")
            with patch("nanexus_trust.historical.executor.run_tool", return_value=(0, json.dumps(probe).encode())):
                result = FiniteExecutor(Backend(), Path("probe")).execute(Candidate(inventory.descriptors), source, Limits())
            self.assertIs(result.verification, VerificationResult.VALID)
