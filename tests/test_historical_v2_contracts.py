"""Synthetic contract evidence, not verifier/timing/durability qualification."""
from dataclasses import replace
import json
from pathlib import Path
import unittest
from nanexus_trust.historical_v2 import contracts as c, wire

FIXTURES = Path(__file__).parent / 'fixtures' / 'historical-v2-contracts.json'


class ContractsTests(unittest.TestCase):
    def test_fixture_roundtrips_and_rejections(self):
        suite = json.loads(FIXTURES.read_text())
        for case in suite['valid']:
            with self.subTest(case=case['name']):
                value = wire.loads(json.dumps(case['document']).encode())
                self.assertEqual(wire.loads(wire.dumps(value)), value)
        for case in suite['invalid']:
            with self.subTest(case=case['name']), self.assertRaises(ValueError):
                wire.loads(json.dumps(case['document']).encode())

    def test_typed_axes_and_safety_bounds(self):
        with self.assertRaises(ValueError):
            c.Coverage(c.Interval('0', '1'), c.SigningState.INDETERMINATE, 'inventory', False)
        with self.assertRaises(ValueError):
            c.Support('unplaced', ('x',) * (c.MAX_ITEMS + 1))
        for value in (True, -1, c.MAX_INTEGER + 1):
            with self.subTest(value=value), self.assertRaises(ValueError):
                c.Progress(c.Lifecycle.VERIFYING, 'counts-only', 'p1', value, 0, None)
        for interval in (('1', '1'), ('NaN', '2'), ('0', 'Infinity'), ('1e2', '101')):
            with self.assertRaises(ValueError):
                c.Interval(*interval)

    def test_rational_ticks_preserve_integer_precision(self):
        value = c.Rational(-9007199254740993, 90000)
        self.assertEqual(wire.loads(wire.dumps(value)), value)
        with self.assertRaises(ValueError):
            c.Rational(1, 0)

    def test_no_fake_support_precision(self):
        for support in (lambda: c.Support('interval', ('source',)),
                        lambda: c.Support('sample', ('source',), interval=c.Interval('0', '1')),
                        lambda: c.Support('order', ('source',), missing_count=2),
                        lambda: c.Support('ambiguous', ('source',), alternatives=('a',))):
            with self.assertRaises(ValueError):
                support()
        with self.assertRaises(ValueError):
            c.Placement(c.PlacementStatus.UNPLACED, None, None, pts=1)

    def test_duplicate_fields_unknown_versions_and_byte_limit(self):
        for data in (b'{"schema":1,"schema":2}', b'[]', b'{' * 1000,
                     b'x' * (wire.MAX_DOCUMENT_BYTES + 1)):
            with self.assertRaises(ValueError):
                wire.loads(data)
        doc = json.loads(wire.dumps(c.Interval('0', '1')))
        for key, value in (('version', True), ('version', 2), ('schema', 'nanexus-recorded-evidence')):
            with self.assertRaises(ValueError):
                wire.loads(json.dumps(dict(doc, **{key: value})).encode())

    def test_partial_and_scan_complete_are_not_success(self):
        suite = json.loads(FIXTURES.read_text())
        stopped = next(wire.loads(json.dumps(x['document']).encode()) for x in suite['valid'] if x['name'] == 'stopped-partial')
        self.assertEqual(stopped.lifecycle, c.Lifecycle.STOPPED)
        self.assertTrue(stopped.observations)
        self.assertTrue(stopped.unprocessed_refs)
        with self.assertRaises(ValueError):
            replace(stopped, lifecycle=c.Lifecycle.COMPLETE,
                    progress=replace(stopped.progress, phase=c.Lifecycle.COMPLETE))
        provisional = replace(stopped.observations[0], state=c.SigningState.INDETERMINATE,
                              finality=c.Finality.PROVISIONAL)
        with self.assertRaises(ValueError):
            replace(stopped, observations=(provisional,))

    def test_complete_progress_matches_finished_work(self):
        suite = json.loads(FIXTURES.read_text())
        document = next(x['document'] for x in suite['valid'] if x['name'] == 'complete-with-negative')
        complete = wire.loads(json.dumps(document).encode())
        self.assertIs(complete.observations[0].state, c.SigningState.INTEGRITY_NEGATIVE)
        self.assertEqual(wire.loads(wire.dumps(complete)), complete)
        for scanned, finalized in ((0, 0), (50, 50), (100, 50)):
            with self.subTest(scanned=scanned, finalized=finalized):
                progress = replace(complete.progress, scanned=scanned, finalized=finalized)
                with self.assertRaises(ValueError):
                    replace(complete, progress=progress)
                invalid = json.loads(json.dumps(document))
                invalid['record']['progress'].update(scanned=scanned, finalized=finalized)
                with self.assertRaises(ValueError):
                    wire.loads(json.dumps(invalid).encode())

    def test_complete_unknown_denominator_and_nonpositive_signing(self):
        suite = json.loads(FIXTURES.read_text())
        complete = next(wire.loads(json.dumps(x['document']).encode())
                        for x in suite['valid'] if x['name'] == 'complete-with-negative')
        for basis in ('counts-only', 'stable-plan-file-size-weighted'):
            progress = replace(complete.progress, basis=basis, denominator=None)
            for state in (c.SigningState.INTEGRITY_NEGATIVE, c.SigningState.INDETERMINATE):
                with self.subTest(basis=basis, state=state):
                    result = replace(complete, progress=progress,
                                     observations=(replace(complete.observations[0], state=state),))
                    self.assertEqual(wire.loads(wire.dumps(result)), result)
            # Omitting a denominator cannot hide known unfinished finalization.
            with self.assertRaises(ValueError):
                replace(complete, progress=replace(progress, finalized=0))
        empty = replace(complete, progress=replace(complete.progress, scanned=0,
                                                 finalized=0, denominator=0),
                        observations=(), processed_refs=())
        self.assertEqual(wire.loads(wire.dumps(empty)), empty)

    def test_four_states_and_legacy_separation(self):
        from nanexus_trust.contracts import VerificationResult, SigningStatus
        from nanexus_trust import evidence
        self.assertEqual(len(c.SigningState), 4)
        self.assertNotIn('NO_RETAINED_VIDEO', c.SigningState.__members__)
        self.assertEqual([v.value for v in VerificationResult], ['VALID', 'PARTIAL', 'INVALID', 'UNKNOWN'])
        self.assertEqual([v.value for v in SigningStatus], ['ACTIVE', 'DEGRADED', 'UNKNOWN'])
        self.assertEqual(evidence.VERSION, 1)
        with self.assertRaises(ValueError):
            wire.dumps(c.Progress(c.Lifecycle.VERIFYING, 'counts-only', 'p', 0, 0, None).__dict__)
