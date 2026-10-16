from dataclasses import replace
from decimal import Decimal
import unittest

from nanexus_trust.contracts import (
    CameraId, HistoricalResult, Interval, MetadataCoverage, ObservationTime,
    RequestedCoverage, RequestedRange, RenditionId, SigningStatus,
    VerificationContext, VerificationResult, covers, utc_timestamp,
)


def example_result(verdict=VerificationResult.VALID):
    request = RequestedRange("request-1", CameraId("front"), Interval("10", "20"), RenditionId("record"))
    context = VerificationContext(request.camera, (Interval("8", "25"),))
    return HistoricalResult(
        request, context, verdict, MetadataCoverage.FULL,
        RequestedCoverage.FULL if verdict == VerificationResult.VALID else RequestedCoverage.INDETERMINATE,
        "context_complete" if verdict == VerificationResult.VALID else "context_incomplete",
    )


class ContractTests(unittest.TestCase):
    def test_all_verdicts_remain_separate_from_coverage(self):
        for verdict in VerificationResult:
            with self.subTest(verdict=verdict):
                result = example_result(verdict)
                self.assertIs(result.verification, verdict)
                self.assertIs(result.metadata_coverage, MetadataCoverage.FULL)

    def test_valid_does_not_imply_requested_full(self):
        result = replace(example_result(), metadata_coverage=MetadataCoverage.NOT_FULL,
                         requested_coverage=RequestedCoverage.INDETERMINATE)
        self.assertIs(result.verification, VerificationResult.VALID)
        self.assertIs(result.requested_coverage, RequestedCoverage.INDETERMINATE)

    def test_distinct_types_enforced_even_when_enum_values_match(self):
        with self.assertRaises(ValueError):
            replace(example_result(), requested_coverage=MetadataCoverage.FULL)
        with self.assertRaises(ValueError):
            replace(example_result(), verification=SigningStatus.UNKNOWN)

    def test_requested_full_requires_each_prerequisite(self):
        for changes in ({"verification": VerificationResult.PARTIAL},
                        {"metadata_coverage": MetadataCoverage.INDETERMINATE},
                        {"context": VerificationContext(CameraId("front"), (Interval("15", "25"),))}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(example_result(), **changes)

    def test_union_preserves_gaps_and_half_open_edges(self):
        request = Interval("10", "20")
        self.assertTrue(covers(request, (Interval("8", "15"), Interval("15", "25"))))
        self.assertFalse(covers(request, (Interval("8", "14"), Interval("15", "25"))))
        self.assertFalse(Interval("0", "10").overlaps(request))
        self.assertTrue(covers(request, (Interval("0", "5"), Interval("8", "25"))))

    def test_decimal_precision_is_not_float_rounded(self):
        tiny = Interval("1792105204.000000001", "1792105204.000000002")
        self.assertFalse(covers(tiny, (Interval("1792105204", "1792105204.000000001"),)))

    def test_malformed_intervals(self):
        for start, end in (("1", "1"), ("2", "1"), ("NaN", "3"), ("1", "Infinity"),
                           (1.0, "3"), ("1e2", "101"), ("0", "1.0000000001")):
            with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                Interval(start, end)

    def test_context_must_correspond_to_request(self):
        for context in (VerificationContext(CameraId("other"), (Interval("8", "25"),)),
                        VerificationContext(CameraId("front"), (Interval("30", "40"),))):
            with self.assertRaises(ValueError):
                replace(example_result(), context=context)
        with self.assertRaises(ValueError):
            VerificationContext(CameraId("front"), ())

    def test_unknown_without_context_is_explicit(self):
        result = replace(example_result(VerificationResult.UNKNOWN), context=None,
                         metadata_coverage=MetadataCoverage.INDETERMINATE)
        self.assertIsNone(result.context)
        with self.assertRaises(ValueError):
            replace(result, verification=VerificationResult.PARTIAL)

    def test_identity_is_logical_not_an_endpoint_or_path(self):
        for value in ("", "/private/camera", "rtsp://example/stream", "../front", "camera name"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                CameraId(value)

    def test_runtime_clocks_require_same_session_and_forward_time(self):
        a = ObservationTime("session-1", "2.1", "2026-01-01T00:00:00Z")
        b = ObservationTime("session-1", "2.2", "2025-12-31T23:00:00Z")
        self.assertEqual(b.elapsed_since(a), Decimal("0.1"))
        with self.assertRaises(ValueError):
            a.elapsed_since(b)
        with self.assertRaises(ValueError):
            replace(b, session_id="session-2").elapsed_since(a)

    def test_wall_clock_requires_timezone(self):
        self.assertEqual(utc_timestamp("2026-01-01T01:00:00+01:00"), "2026-01-01T00:00:00Z")
        with self.assertRaises(ValueError):
            utc_timestamp("2026-01-01T00:00:00")
        with self.assertRaises(ValueError):
            utc_timestamp("0001-01-01T00:00:00+01:00")

    def test_elapsed_time_is_independent_of_decimal_ambient_precision(self):
        from decimal import localcontext
        a = ObservationTime("session-1", "0", "2026-01-01T00:00:00Z")
        b = ObservationTime("session-1", "12345678901234567890.123456789", "2026-01-01T00:00:00Z")
        with localcontext() as context:
            context.prec = 4
            self.assertEqual(b.elapsed_since(a), Decimal("12345678901234567890.123456789"))
