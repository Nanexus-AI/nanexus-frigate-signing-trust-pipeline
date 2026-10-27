"""Accepted nanexus-hv2-query-coverage/1 partition. Not a signing result."""
import unittest
from nanexus_trust.historical_v2.query_coverage import coverage_document, coverage_segments


class QueryCoverageTests(unittest.TestCase):
    def document(self, query, dataset, spans, planned=True):
        return coverage_document(query[0], query[1], dataset[0], dataset[1], spans, planned)

    def test_live_session_dataset_equals_query_never_emits_outside(self):
        value = self.document(('50', '250'), ('50', '250'),
                              (('90', '110', 'rec-a', 'PENDING'),))
        self.assertEqual(value['schema'], 'nanexus-hv2-query-coverage/1')
        self.assertEqual(value['query'], {'start': '50', 'end': '250'})
        self.assertEqual(value['dataset'], value['query'])
        self.assertEqual([s['kind'] for s in value['segments']], ['gap', 'recording', 'gap'])
        self.assertEqual(value['segments'][0], {'kind': 'gap', 'start': '50', 'end': '90'})
        self.assertEqual(value['segments'][1],
                         {'kind': 'recording', 'start': '90', 'end': '110',
                          'recording': 'rec-a', 'finding': 'PENDING'})
        self.assertEqual(value['segments'][2], {'kind': 'gap', 'start': '110', 'end': '250'})

    def test_overlap_clips_to_the_query_without_changing_file_identity(self):
        value = self.document(('5', '25'), ('5', '25'), (
            ('0', '10', 'rec0', 'GREEN'),
            ('10', '20', 'rec1', 'PENDING'),
            ('20', '30', 'rec2', 'PENDING'),
        ))
        self.assertEqual([s['kind'] for s in value['segments']], ['recording'] * 3)
        self.assertEqual([(s['start'], s['end'], s['recording']) for s in value['segments']],
                         [('5', '10', 'rec0'), ('10', '20', 'rec1'), ('20', '25', 'rec2')])
        self.assertEqual(value['segments'][0]['finding'], 'GREEN')
        self.assertEqual(value['segments'][1]['finding'], 'PENDING')

    def test_interior_holes_are_catalog_gaps_not_invalid(self):
        value = self.document(('0', '40'), ('0', '40'), (
            ('0', '10', 'rec0', 'AMBER'),
            ('20', '30', 'rec2', 'PENDING'),
        ))
        self.assertEqual([s['kind'] for s in value['segments']],
                         ['recording', 'gap', 'recording', 'gap'])
        self.assertEqual(value['segments'][1], {'kind': 'gap', 'start': '10', 'end': '20'})
        self.assertEqual(value['segments'][3], {'kind': 'gap', 'start': '30', 'end': '40'})
        self.assertNotIn('INVALID', str(value))

    def test_pending_to_finalized_keeps_recording_kind(self):
        spans = (('0', '10', 'rec0', 'PENDING'), ('10', '20', 'rec1', 'PENDING'))
        pending = coverage_segments('0', '20', '0', '20', spans, True)
        done = coverage_segments('0', '20', '0', '20',
                                 (('0', '10', 'rec0', 'GREEN'), ('10', '20', 'rec1', 'AMBER')), True)
        self.assertEqual([s['kind'] for s in pending], ['recording', 'recording'])
        self.assertEqual([s['kind'] for s in done], ['recording', 'recording'])
        self.assertEqual([s['recording'] for s in pending], ['rec0', 'rec1'])
        self.assertEqual([s['recording'] for s in done], ['rec0', 'rec1'])
        self.assertEqual([s['finding'] for s in pending], ['PENDING', 'PENDING'])
        self.assertEqual([s['finding'] for s in done], ['GREEN', 'AMBER'])

    def test_empty_planned_spans_would_be_a_full_query_gap(self):
        value = self.document(('1', '29'), ('1', '29'), (), True)
        self.assertEqual(value['segments'], [{'kind': 'gap', 'start': '1', 'end': '29'}])

    def test_established_outside_kind_still_exists_for_corpus_dataset(self):
        value = self.document(('0', '40'), ('100', '200'), (), True)
        self.assertEqual([s['kind'] for s in value['segments']], ['outside'])
        self.assertEqual(value['segments'][0], {'kind': 'outside', 'start': '0', 'end': '40'})


if __name__ == '__main__':
    unittest.main()
