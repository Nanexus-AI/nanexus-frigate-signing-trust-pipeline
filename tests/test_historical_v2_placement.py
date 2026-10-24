from dataclasses import replace
from decimal import Decimal
import unittest
from nanexus_trust.historical_v2.contracts import Interval, Quality, PlacementStatus
from nanexus_trust.historical_v2.placement import (
    unplaced, missing_order, metadata_gaps, admit_single_file, AdmissionFailure,
)
import test_frigate_historical_v2 as provider_tests


class SupportTests(unittest.TestCase):
    def test_unplaced_sample_and_synthetic_order_have_no_numeric_time(self):
        support, placement = unplaced('opaque-source')
        self.assertEqual(support.sample_ref, 'opaque-source')
        self.assertEqual(placement.status, PlacementStatus.UNPLACED)
        self.assertIsNone(support.point)
        support, placement = missing_order('affected-scope', left_ref='received-left')
        self.assertEqual(support.missing_count, 1)
        self.assertIsNone(support.sample_ref)
        self.assertIsNone(placement.pts)
        with self.assertRaises(ValueError): missing_order('scope')

    def test_metadata_gaps_overlap_incomplete_and_multifile(self):
        # Reuse owned synthetic provider setup without inheriting/rerunning tests.
        fixture = provider_tests.ProviderTests(); fixture.setUp()
        try:
            page = fixture.page()
            self.assertEqual(metadata_gaps(page), ((Decimal(12),Decimal(14)),))
            self.assertEqual(admit_single_file(page, ('a',)).item_id, 'a')
            with self.assertRaisesRegex(AdmissionFailure,'multi-file'): admit_single_file(page, ('a','b'))
            for quality in (Quality.UNKNOWN,Quality.INCOMPLETE):
                with self.assertRaises(AdmissionFailure): metadata_gaps(replace(page,completeness=quality))
            with self.assertRaises(AdmissionFailure): metadata_gaps(replace(page,ambiguous_refs=('a','b')))
            touching = replace(page, items=(page.items[0],replace(page.items[1],metadata_interval=Interval('12','16'))))
            self.assertEqual(metadata_gaps(touching), ())
            with self.assertRaisesRegex(AdmissionFailure,'multi-file'): admit_single_file(touching,('a','b'))
            overlap = replace(page, items=(page.items[0],replace(page.items[1],metadata_interval=Interval('11','16'))))
            with self.assertRaisesRegex(AdmissionFailure,'overlap'): metadata_gaps(overlap)
        finally: fixture.doCleanups()
