from contextlib import ExitStack
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import socket
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

from nanexus_trust.contracts import MetadataCoverage, RequestedCoverage, VerificationResult
from nanexus_trust.evidence import (
    Component, PROFILE, Provenance, RecognitionStatus, canonical_json,
    issue_bundle, recognize_bundle,
)
from test_contracts import example_result


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bundle = self.root / "bundle"
        self.provenance = Provenance(Component("companion", "0.1.0"), PROFILE,
                                     Component("verification-backend", "1.0.0"),
                                     "bounded-h264-v1", "sha256:" + "a" * 64)
        self.artifacts = {"artifacts/backend-report.json": ("verification-report", b'{"recorded":true}')}

    def issue(self, verdict=VerificationResult.VALID):
        return issue_bundle(self.bundle, example_result(verdict), self.provenance,
                            "2026-01-01T00:00:00Z", self.artifacts)

    def rewrite(self, change, rehash=True):
        path = self.bundle / "receipt.json"
        document = json.loads(path.read_bytes())
        change(document["manifest"])
        if rehash:
            document["manifest_sha256"] = hashlib.sha256(canonical_json(document["manifest"])).hexdigest()
        path.write_bytes(canonical_json(document))

    def assert_unknown(self):
        result = recognize_bundle(self.bundle)
        self.assertIs(result.status, RecognitionStatus.UNKNOWN)
        self.assertIsNone(result.result)  # Never turn recognition failure into media INVALID.
        self.assertIsNone(result.reference)

    def test_all_results_roundtrip_unchanged(self):
        for verdict in VerificationResult:
            with self.subTest(verdict=verdict):
                target = self.root / verdict.value
                source = example_result(verdict)
                ref = issue_bundle(target, source, self.provenance, "2026-01-01T00:00:00Z", self.artifacts)
                read = recognize_bundle(target, ref)
                self.assertIs(read.status, RecognitionStatus.SUPPORTED)
                self.assertEqual(read.result, source)
                self.assertEqual(read.provenance, self.provenance)

    def test_unknown_without_context_or_backend_roundtrips(self):
        result = replace(example_result(VerificationResult.UNKNOWN), context=None,
                         metadata_coverage=MetadataCoverage.INDETERMINATE)
        provenance = replace(self.provenance, backend=None, trust_config_id=None)
        issue_bundle(self.bundle, result, provenance, "2026-01-01T00:00:00Z", self.artifacts)
        self.assertEqual(recognize_bundle(self.bundle).result, result)

    def test_valid_with_incomplete_metadata_roundtrips(self):
        result = replace(example_result(), metadata_coverage=MetadataCoverage.NOT_FULL,
                         requested_coverage=RequestedCoverage.INDETERMINATE)
        issue_bundle(self.bundle, result, self.provenance, "2026-01-01T00:00:00Z", self.artifacts)
        self.assertEqual(recognize_bundle(self.bundle).result, result)

    def test_relocation_preserves_every_byte_identity_and_interpretation(self):
        ref = self.issue(VerificationResult.PARTIAL)
        before = recognize_bundle(self.bundle, ref)
        moved = self.root / "new-location"
        shutil.copytree(self.bundle, moved)
        shutil.rmtree(self.bundle)
        self.assertEqual(recognize_bundle(moved, ref), before)

    def test_supported_component_versions_are_not_frozen_source_hashes(self):
        self.issue()
        self.rewrite(lambda m: m["provenance"]["issuer"].update(version="2.0.0"))
        self.assertIs(recognize_bundle(self.bundle).status, RecognitionStatus.SUPPORTED)

    def test_unsupported_schema_version(self):
        self.issue()
        self.rewrite(lambda m: m.update(version=2))
        self.assert_unknown()

    def test_unknown_profile(self):
        self.issue()
        self.rewrite(lambda m: m["provenance"].update(profile="future-profile"))
        self.assert_unknown()

    def test_missing_required_manifest_field(self):
        self.issue()
        self.rewrite(lambda m: m.pop("provenance"))
        self.assert_unknown()

    def test_missing_bundle_and_artifact(self):
        self.assert_unknown()
        self.issue()
        (self.bundle / "artifacts/backend-report.json").unlink()
        self.assert_unknown()

    def test_artifact_digest_mismatch_at_same_size(self):
        self.issue()
        path = self.bundle / "artifacts/backend-report.json"
        path.write_bytes(b"x" * path.stat().st_size)
        self.assert_unknown()

    def test_manifest_digest_mismatch(self):
        self.issue()
        self.rewrite(lambda m: m["result"].update(reason="changed"), rehash=False)
        self.assert_unknown()

    def test_expected_external_reference_rejects_self_consistent_rewrite(self):
        ref = self.issue()
        self.rewrite(lambda m: m["result"].update(reason="changed"))
        self.assertIs(recognize_bundle(self.bundle).status, RecognitionStatus.SUPPORTED)
        self.assertIs(recognize_bundle(self.bundle, ref).status, RecognitionStatus.UNKNOWN)

    def test_unsafe_paths(self):
        self.issue()
        for path in ("../outside", "/outside", "artifacts/../../outside", "artifacts/./report",
                     "artifacts//report", "artifacts\\report", "C:/report"):
            with self.subTest(path=path):
                self.rewrite(lambda m: m["artifacts"][0].update(path=path))
                self.assert_unknown()

    def test_writer_rejects_unsafe_paths_before_creating_destination(self):
        with self.assertRaises(ValueError):
            issue_bundle(self.bundle, example_result(), self.provenance, "2026-01-01T00:00:00Z",
                         {"artifacts/../outside": ("report", b"x")})
        self.assertFalse(self.bundle.exists())

    def test_symlink_artifact_and_receipt_are_rejected(self):
        self.issue()
        artifact = self.bundle / "artifacts/backend-report.json"
        outside = self.root / "outside"
        outside.write_bytes(artifact.read_bytes())
        artifact.unlink()
        artifact.symlink_to(outside)
        self.assert_unknown()
        receipt = self.bundle / "receipt.json"
        other = self.root / "receipt-copy"
        other.write_bytes(receipt.read_bytes())
        receipt.unlink()
        receipt.symlink_to(other)
        self.assert_unknown()

    def test_malformed_interval_and_inconsistent_context(self):
        self.issue()
        self.rewrite(lambda m: m["result"]["request"]["interval"].update(start="21"))
        self.assert_unknown()
        self.rewrite(lambda m: m["result"]["request"]["interval"].update(start="10"))
        self.rewrite(lambda m: m["result"]["context"].update(camera="different"))
        self.assert_unknown()

    def test_gap_cannot_be_hidden_by_context_envelope(self):
        self.issue()
        self.rewrite(lambda m: m["result"]["context"].update(
            intervals=[{"start": "8", "end": "14"}, {"start": "15", "end": "25"}]))
        self.assert_unknown()

    def test_unsupported_fields_and_duplicate_json_keys(self):
        self.issue()
        self.rewrite(lambda m: m.update(unrecognized_extension=True))
        self.assert_unknown()
        (self.bundle / "receipt.json").write_text('{"manifest":{},"manifest":{}}')
        self.assert_unknown()

    def test_reference_covers_manifest_and_all_artifacts(self):
        ref = self.issue()
        receipt = json.loads((self.bundle / "receipt.json").read_bytes())
        self.assertEqual(ref.manifest_sha256, hashlib.sha256(canonical_json(receipt["manifest"])).hexdigest())
        artifact = receipt["manifest"]["artifacts"][0]
        self.assertEqual(artifact["sha256"], hashlib.sha256(self.artifacts[artifact["path"]][1]).hexdigest())

    def test_existing_destination_is_never_overwritten(self):
        self.issue()
        original = (self.bundle / "receipt.json").read_bytes()
        with self.assertRaises(FileExistsError):
            self.issue()
        self.assertEqual((self.bundle / "receipt.json").read_bytes(), original)

    def test_resource_bounds_and_wrong_numeric_types(self):
        self.issue()
        for size in (-1, True, 32 * 1024 * 1024 + 1):
            with self.subTest(size=size):
                self.rewrite(lambda m: m["artifacts"][0].update(size=size))
                self.assert_unknown()
        (self.bundle / "receipt.json").write_bytes(b" " * (1024 * 1024 + 1))
        self.assert_unknown()

    def test_writer_checks_artifact_limit_before_creation(self):
        with patch("nanexus_trust.evidence.MAX_ARTIFACT_BYTES", 8):
            with self.assertRaises(ValueError):
                self.issue()
        self.assertFalse(self.bundle.exists())

    def test_zero_execution_network_database_and_writes(self):
        self.issue(VerificationResult.PARTIAL)
        before = {str(p.relative_to(self.bundle)): p.read_bytes() for p in self.bundle.rglob("*") if p.is_file()}
        forbidden = self.root / "unsupported"
        shutil.copytree(self.bundle, forbidden)
        (forbidden / "receipt.json").write_text("{}")
        audited_reads = []
        active = False

        def audit(event, args):
            if not active:
                return
            if event == "open":
                path, _, flags = args
                self.assertIsInstance(path, (str, bytes, os.PathLike))
                self.assertTrue(Path(path).is_relative_to(self.root))
                self.assertFalse(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC))
                audited_reads.append(str(path))
            if event.startswith(("subprocess.", "socket.", "sqlite3.", "os.exec", "os.spawn")):
                self.fail("recognition attempted external execution/access")

        sys.addaudithook(audit)
        with ExitStack() as stack:
            guards = [stack.enter_context(patch.object(module, name, side_effect=AssertionError("forbidden")))
                      for module, name in ((subprocess, "run"), (subprocess, "Popen"),
                                           (sqlite3, "connect"), (socket, "socket"),
                                           (socket, "create_connection"), (Path, "write_bytes"),
                                           (Path, "write_text"))]
            active = True
            try:
                self.assertIs(recognize_bundle(self.bundle).status, RecognitionStatus.SUPPORTED)
                self.assertIs(recognize_bundle(forbidden).status, RecognitionStatus.UNKNOWN)
            finally:
                active = False
            for guard in guards:
                guard.assert_not_called()
        self.assertEqual(len(audited_reads), 3)  # Receipt + artifact + unsupported receipt.
        after = {str(p.relative_to(self.bundle)): p.read_bytes() for p in self.bundle.rglob("*") if p.is_file()}
        self.assertEqual(before, after)
