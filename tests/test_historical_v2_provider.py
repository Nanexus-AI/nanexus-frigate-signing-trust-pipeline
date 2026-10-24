"""In-memory protocol witness only; no host, credentials, staging or media IO."""
from dataclasses import replace
import unittest
from nanexus_trust.historical_v2 import contracts as c, provider as p, wire


class MockProvider:
    def capabilities(self, camera):
        return p.Capabilities(camera, 'synthetic-v1', True, True, True, False, False, None, None)

    def discover(self, request, cancel):
        if cancel.cancelled():
            raise RuntimeError('cancelled')
        return p.InventoryPage('inventory', 'cut', 'synthetic-v1', request.camera,
                               request.requested, c.Quality.UNKNOWN, c.Quality.UNKNOWN,
                               (), (), (), (), None, True)

    def acquire(self, item, bounds, cancel):
        raise RuntimeError('synthetic provider has no media')

    def read(self, handle, offset, max_bytes, cancel):
        raise RuntimeError('synthetic provider has no media')

    def release(self, handle):
        pass


class Cancel:
    def __init__(self, value=False): self.value = value
    def cancelled(self): return self.value


class ProviderTests(unittest.TestCase):
    def test_host_independent_unknown_inventory(self):
        camera = c.Camera('mock-instance', 'opaque-camera', 'generation-1', 'Example')
        bounds = p.Bounds(8, 1024, 2, 2048, 2, 100, c.Rational(50, 1), 'synthetic-only')
        request = p.DiscoveryRequest(camera, c.Interval('10', '20'), 'host-metadata-unix-seconds', bounds, None)
        provider: p.HistoricalProvider = MockProvider()
        self.assertFalse(provider.capabilities(camera).qualified_historical_anchor)
        result = provider.discover(request, Cancel())
        self.assertEqual(wire.loads(wire.dumps(result)), result)
        self.assertIs(result.completeness, c.Quality.UNKNOWN)
        self.assertEqual(result.items, ())  # Empty + unknown does not establish a gap.
        with self.assertRaises(RuntimeError):
            provider.discover(request, Cancel(True))
        with self.assertRaises(ValueError):
            replace(result, terminal=False)
        with self.assertRaises(ValueError):
            replace(bounds, max_work_units=0)

    def test_items_preserve_unknown_access_and_timing(self):
        camera = c.Camera('mock', 'camera', 'g1', 'Example')
        item = p.Item('item', 'g1', camera, None, None, None, False,
                      None, None, c.Quality.UNKNOWN, None, None)
        self.assertEqual(wire.loads(wire.dumps(item)), item)
        with self.assertRaises(ValueError):
            replace(item, size_exact=True)
        with self.assertRaises(ValueError):
            p.StableHandle(camera, 'item', 'g1', 'handle', 2, 'bad', c.Quality.UNKNOWN, 'owner')
