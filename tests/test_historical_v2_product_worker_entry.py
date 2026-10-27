"""Source lock for the accepted product Worker entry. Does not compile or run it."""
import hashlib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT / 'src/nanexus_trust/historical_v2/product_worker_entry.cpp'
INCLUDED = ROOT / 'src/nanexus_trust/historical_v2/worker_child.cpp'
BODY_SHA256 = 'd438ac1d0a31230d4b695cbe26fab91ed3ff7a3f8925e736fdcbfcb25400e841'
INCLUDED_SHA256 = '760c93a44e49aa1dbcfd83d557a5a2013138fe1ee0524aa09a7a484dadf63e62'


def body(text):
    return ''.join(line for line in text.splitlines(True) if not line.startswith('#include '))


class ProductWorkerEntrySourceTests(unittest.TestCase):
    def test_entry_keeps_accepted_body_and_relative_include(self):
        text = ENTRY.read_text()
        includes = [line.strip() for line in text.splitlines() if line.startswith('#include')]
        self.assertEqual(includes, ['#include "worker_child.cpp"'])
        self.assertNotIn('/home/', text)
        self.assertNotIn('.local/', text)
        self.assertNotIn('192.168.', text)
        self.assertEqual(hashlib.sha256(body(text).encode()).hexdigest(), BODY_SHA256)
        for literal in (
            'd0d6181bf911e3c5552ee2c6f4bb91db6362b767ccbd7686c07fdf79a8d1f943',
            'hv2-cont03a/1',
            'B-h264-cont03a',
            'native-hv2-cont09-v1',
            'standalone-candidate-v1',
            '256*1024*1024',
            '32768',
            'count<=182',
            '{1048576,1024,2048,256,32768,262144}',
        ):
            self.assertIn(literal, text)
        self.assertEqual(hashlib.sha256(INCLUDED.read_bytes()).hexdigest(), INCLUDED_SHA256)
        copied = list((ROOT / 'src').rglob('historical_observation.cpp'))
        self.assertEqual(copied, [])


if __name__ == '__main__':
    unittest.main()
