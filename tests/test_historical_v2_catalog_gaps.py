"""Catalog GAP projection only. Saved findings are read and not rewritten."""
import copy
import hashlib
import json
import unittest
from decimal import Decimal
from pathlib import Path
from nanexus_trust.historical_v2.catalog_gaps import project_catalog_gaps
from nanexus_trust.historical_v2.composition import canonical
from nanexus_trust.companion.window_http import finding

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / '.local/governance/ai-dlc/evidence/m1-hv2-d-planning'
MULTIGAP = ROOT / '.local/runtime/hv2-cont26r2/result.json'
REQUEST = ('1792854293.7659404', '1792855193.7659404')
CONTINUOUS_REQUEST = ('1792764943', '1792766743')


def gaps(start, end, intervals, **kwargs):
    return project_catalog_gaps('cam', start, end, intervals, **kwargs)


class ProjectionTests(unittest.TestCase):
    def test_contiguous_sliver_threshold_overlap_and_nesting(self):
        self.assertEqual(gaps('0', '30', [('0', '10'), ('10', '20'), ('20', '30')]), [])
        self.assertEqual(gaps('0', '30', [('0', '10'), ('10.033', '20')]), [])
        self.assertEqual(gaps('0', '40', [('0', '10'), ('20', '30')]), [])
        self.assertEqual(gaps('0', '100', [('0', '50'), ('40', '90')]), [])
        self.assertEqual(gaps('0', '100', [('0', '100'), ('10', '20')]), [])
        shown = gaps('0', '40', [('0', '10'), ('20.000000001', '30')])
        self.assertEqual(len(shown), 1)
        self.assertGreater(Decimal(shown[0]['duration']), Decimal('10.0'))

    def test_order_clip_and_request_edges(self):
        ordered = gaps('0', '100', [('0', '10'), ('40', '50')])
        reversed_input = gaps('0', '100', [('40', '50'), ('0', '10')])
        self.assertEqual(ordered, reversed_input)
        self.assertEqual(ordered[0]['start'], '10')
        self.assertEqual(ordered[0]['end'], '40')
        clipped = gaps('100', '200', [('90', '130'), ('160', '210'), ('0', '50')])
        self.assertEqual([(g['start'], g['end']) for g in clipped], [('130', '160')])
        edges = gaps('0', '100', [('20', '30'), ('50', '60')])
        self.assertEqual([(g['start'], g['end']) for g in edges], [('30', '50')])
        self.assertNotEqual(edges[0]['start'], '0')
        self.assertNotEqual(edges[0]['end'], '100')

    def test_empty_invalid_and_identity(self):
        self.assertEqual(gaps('0', '10', []), [])
        kept = gaps('0', '50', [None, ('5', '1'), ('nope', '3'), ('nan', '4'), ('4', '4'),
                                 ('0', '10'), ('30', '40'), float('inf')])
        self.assertEqual([(g['start'], g['end']) for g in kept], [('10', '30')])
        self.assertEqual(set(kept[0]), {'type', 'gap_id', 'camera', 'start', 'end', 'duration'})
        self.assertNotIn('recording', kept[0])
        self.assertNotIn('finding', kept[0])
        self.assertEqual(kept[0]['type'], 'GAP')
        self.assertEqual(kept[0]['gap_id'], hashlib.sha256(canonical({
            'camera': 'cam', 'start': '10', 'end': '30'})).hexdigest())
        again = gaps('0', '50', [('30', '40'), ('0', '10')])
        self.assertEqual(again[0]['gap_id'], kept[0]['gap_id'])
        for bad in ((None, '1', '2', []), ('cam', '2', '2', []), ('cam', '5', '1', []), ('cam', 'x', '2', [])):
            with self.assertRaises(ValueError):
                project_catalog_gaps(*bad)

    def test_saved_multigap_a_five_interior_gaps_leave_findings(self):
        if not MULTIGAP.is_file():
            self.skipTest('preserved MultiGap-A result unavailable')
        document = json.loads(MULTIGAP.read_text())
        files = document['result']['files']
        before = copy.deepcopy(files)
        projected = project_catalog_gaps('hv2_corpus_signed', *REQUEST,
                                          [f['metadata_interval'] for f in files])
        self.assertEqual(files, before)
        self.assertEqual(len(projected), 5)
        self.assertEqual([(g['start'], g['end'], g['duration']) for g in projected], [
            ('1792854374.016667', '1792854404', '29.983333'),
            ('1792854523.966667', '1792854594', '70.033333'),
            ('1792854694.016667', '1792854724', '29.983333'),
            ('1792854853.966667', '1792854924', '70.033333'),
            ('1792855024.016667', '1792855054', '29.983333')])
        self.assertEqual(len({g['gap_id'] for g in projected}), 5)
        for gap in projected:
            self.assertEqual(set(gap), {'type', 'gap_id', 'camera', 'start', 'end', 'duration'})
            self.assertEqual(gap['camera'], 'hv2_corpus_signed')
            self.assertGreater(Decimal(gap['duration']), Decimal('10'))
        last = files[-1]
        self.assertEqual(last['recording'], '1792855184.0-sksnyh')
        self.assertEqual(last['finding'], 'AMBER')
        self.assertEqual(last['unresolved'], 16)
        self.assertTrue(all(f['state'] == 'COMPLETED' for f in files))
        colors = [f['finding'] for f in files]
        self.assertEqual(colors, [f['finding'] for f in before])
        self.assertNotIn('GAP', colors)

    def test_saved_continuous_30min_file_results_unchanged(self):
        summary_path = EVIDENCE / 'hr6b-artifacts/summary.json'
        findings_path = EVIDENCE / 'hr6b-artifacts/files.json'
        if not summary_path.is_file() or not findings_path.is_file():
            self.skipTest('preserved continuous-30min diagnostics unavailable')
        summary = json.loads(summary_path.read_text())
        saved = json.loads(findings_path.read_text())
        files = summary['files']
        before = copy.deepcopy(files)
        projected = project_catalog_gaps('hv2_corpus_signed', *CONTINUOUS_REQUEST,
                                          [f['metadata_interval'] for f in files])
        self.assertEqual(files, before)
        self.assertEqual(projected, [])
        self.assertEqual([f['recording'] for f in files], [row['recording'] for row in saved])
        for row, source in zip(files, saved):
            self.assertEqual(finding({}, dict(finalized_counts=source['counts'], unresolved=source['unresolved'],
                                               unprocessed=0, missing_evidence=False)), 'AMBER')
            self.assertEqual(row['recording'], source['recording'])
            self.assertEqual(row['unresolved'], source['unresolved'])
        for gap in projected:
            self.assertGreater(Decimal(gap['duration']), Decimal('10'))
            self.assertNotIn('recording', gap)
            self.assertNotIn('finding', gap)
        self.assertEqual(projected, project_catalog_gaps('hv2_corpus_signed', *CONTINUOUS_REQUEST,
                                                          list(reversed([f['metadata_interval'] for f in files]))))


if __name__ == '__main__':
    unittest.main()
