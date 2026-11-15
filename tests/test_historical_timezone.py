"""Portable Catalog day/timezone checks; no runtime or real recordings."""
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from urllib.parse import parse_qs, urlsplit

from nanexus_trust.companion.window_http import (
    catalog_recording_dates, recording_zone, session_recording_dates,
)


def stamp(iso):
    return datetime.fromisoformat(iso.replace('Z', '+00:00')).timestamp()


class CatalogTimezoneTests(unittest.TestCase):
    def dates(self, intervals, month, zone):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'catalog.sqlite'
            with sqlite3.connect(path) as db:
                db.execute('CREATE TABLE recordings(camera TEXT,start_time REAL,end_time REAL)')
                db.executemany('INSERT INTO recordings VALUES (?,?,?)',
                               [('cam', stamp(a), stamp(b)) for a, b in intervals])
            return catalog_recording_dates(path, 'cam', month, zone)['dates']

    def test_D_utc_midnight_is_previous_local_month(self):
        rows = [('2026-07-01T01:00:00Z', '2026-07-01T01:01:00Z')]
        self.assertEqual(self.dates(rows, '2026-06', 'America/Toronto'), ['2026-06-30'])
        self.assertEqual(self.dates(rows, '2026-07', 'America/Toronto'), [])
        self.assertEqual(self.dates(rows, '2026-07', 'UTC'), ['2026-07-01'])

    def test_D_half_open_local_midnight(self):
        rows = [('2026-07-01T03:59:00Z', '2026-07-01T04:00:00Z')]
        self.assertEqual(self.dates(rows, '2026-06', 'America/Toronto'), ['2026-06-30'])
        self.assertEqual(self.dates(rows, '2026-07', 'America/Toronto'), [])
        rows = [('2026-07-01T03:59:00Z', '2026-07-01T04:01:00Z')]
        self.assertEqual(self.dates(rows, '2026-07', 'America/Toronto'), ['2026-07-01'])

    def test_winter_and_summer_day_boundaries(self):
        for mon, offset in [('01', '05'), ('07', '04')]:
            rows = [(f'2026-{mon}-15T{offset}:00:00Z', f'2026-{mon}-15T{offset}:01:00Z')]
            self.assertEqual(self.dates(rows, f'2026-{mon}', 'America/Toronto'), [f'2026-{mon}-15'])

    def test_DST_days_and_fractional_offset(self):
        for rows, month, zone, dates in [
            ([('2026-03-08T06:59:00Z', '2026-03-08T07:01:00Z')], '2026-03', 'America/Toronto', ['2026-03-08']),
            ([('2026-11-01T05:15:00Z', '2026-11-01T06:45:00Z')], '2026-11', 'America/Toronto', ['2026-11-01']),
            ([('2026-01-14T18:15:00Z', '2026-01-14T18:16:00Z')], '2026-01', 'Asia/Kathmandu', ['2026-01-15']),
        ]:
            with self.subTest(zone=zone, month=month):
                self.assertEqual(self.dates(rows, month, zone), dates)

    def test_native_authenticated_summary_receives_same_IANA_zone(self):
        class Session:
            def authorize(self, cookie, owner, native, deadline):
                self.auth = cookie, owner, native

            def get(self, cookie, path, cap, deadline):
                self.query = parse_qs(urlsplit(path).query)
                return json.dumps({'2026-06-30': True, '2026-07-01': True}).encode()

        session = Session()
        result = session_recording_dates(session, 'session-cookie', 'viewer', 'cam', 'native', '2026-06', 'America/Toronto')
        self.assertEqual(session.auth, ('session-cookie', 'viewer', 'native'))
        self.assertEqual(session.query, {'cameras': ['native'], 'timezone': ['America/Toronto']})
        self.assertEqual(result['dates'], ['2026-06-30'])
        self.assertEqual(result['zone'], 'America/Toronto')

    def test_zone_validation_rejects_unsupported_input(self):
        for zone in ('Invalid/Zone', '../etc/passwd', 'localtime', 'posixrules', 'America/Toronto?token=x'):
            with self.subTest(zone=zone), self.assertRaises(ValueError):
                recording_zone(zone)


if __name__ == '__main__':
    unittest.main()
