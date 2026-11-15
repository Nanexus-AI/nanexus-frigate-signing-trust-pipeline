"""Portable recovery regressions. Structural fixtures do not validate crypto.

Run explicitly: PYTHONPATH=src:tests python3 -m unittest test_live_reader_recovery
Optional real RTSP check: NANEXUS_TEST_FFMPEG=/path/to/pinned/ffmpeg ...
"""
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import patch

from nanexus_trust.frigate.host_entry import Feed, reader_command
from nanexus_trust.companion.live import LiveScheduler, PlaybackMapping
from nanexus_trust.live import Binding
from nanexus_trust.contracts import CameraId, RenditionId
from test_live import POLICY, PREFIX, MEDIA, SIGNING

BINDING = Binding(CameraId('cam'), RenditionId('main'), 'source', 'revision', POLICY)


class Child:
    pid = 123456789

    def __init__(self):
        self.returncode = None
        self.waited = False

    def poll(self):
        return self.returncode

    def send_signal(self, sig):
        self.returncode = -sig

    def wait(self, timeout=None):
        self.waited = True
        return self.returncode


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.now = 0.0
        self.children = []
        self.feed = Feed('cam', ('synthetic',), .05, .1, .8,
                         startup_timeout=2, silence_timeout=2, clock=lambda: self.now)
        self.scheduler = LiveScheduler(
            (PlaybackMapping('cam', 'stream', 'grid', BINDING),), self.feed.factory,
            clock=lambda: (format(self.now, '.9f'), '2026-11-15T00:00:00Z'))
        self.addCleanup(self.feed.stop)
        self.addCleanup(self.scheduler.close)
        self.popen = patch('nanexus_trust.frigate.host_entry.subprocess.Popen', self.spawn)
        self.popen.start()
        self.addCleanup(self.popen.stop)

    def spawn(self, *args, **kwargs):
        self.assertFalse(any(c.poll() is None for c in self.children), 'duplicate child')
        os.fstat(kwargs['stdout'])
        c = Child()
        self.children.append(c)
        return c

    def tick(self):
        self.scheduler.tick()
        return self.scheduler.query('cam', 'stream', 'grid')

    def begin(self):
        self.tick()
        self.feed._once()
        self.tick()
        os.write(self.feed.write, PREFIX)
        self.tick()

    def deliver(self, data=MEDIA + SIGNING):
        if self.feed.last_byte is None:
            data = PREFIX + data
        os.write(self.feed.write, data)
        return self.tick()

    def retry(self):
        self.tick()  # EOF -> reset, new pipe on query
        self.now = self.feed.next_start
        self.feed._once()
        self.tick()

    def test_A_active_silence_generation_recovery(self):
        self.begin()
        initial = self.deliver()
        self.assertEqual(initial['status'], 'ACTIVE')
        child = self.feed.proc
        old_gen = self.feed.generation
        self.now = 1.1
        self.tick()
        self.assertGreater(self.feed.generation, old_gen)
        current_fd = self.feed.write
        self.feed._once()
        self.assertTrue(child.waited)
        self.assertIn('stale-generation', self.feed.events)
        os.fstat(current_fd)
        self.now = self.feed.next_start
        self.feed._once()
        self.assertEqual(self.feed.proc_gen, self.feed.generation)
        fresh = self.tick()
        self.assertIsNone(fresh['last_evidence_at'])
        self.assertNotEqual(initial['session_id'], fresh['session_id'])
        self.assertEqual(self.deliver()['status'], 'ACTIVE')

    def test_B_initial_no_bytes_backoff(self):
        self.begin()
        delays = []
        for _ in range(5):
            child = self.feed.proc
            self.now += 2.01
            self.feed._once()
            self.assertTrue(child.waited)
            delays.append(self.feed.backoff)
            count = len(self.children)
            self.feed._once()
            self.assertEqual(len(self.children), count)
            self.retry()
        self.assertEqual(delays, [.1, .2, .4, .8, .8])
        self.assertIn('startup-timeout', self.feed.events)

    def test_D_normal_eof(self):
        self.begin()
        child = self.feed.proc
        child.returncode = 0
        self.feed._once()
        self.assertTrue(child.waited)
        self.retry()
        self.assertEqual(self.deliver()['status'], 'ACTIVE')
        self.assertIn('child-exit', self.feed.events)

    def test_E_unavailable_then_delayed_recovery(self):
        self.begin()
        for _ in range(3):
            self.feed.proc.returncode = 1
            self.feed._once()
            self.retry()
        self.assertEqual(self.deliver()['status'], 'ACTIVE')
        self.assertEqual(len(self.children), 4)

    def test_F_repeated_generations_no_fd_leak(self):
        self.begin()
        before = len(list(Path('/proc/self/fd').iterdir()))
        for _ in range(50):
            self.scheduler.reconnect('cam', 'main')
            self.tick()
            self.feed._once()
            self.now = self.feed.next_start
            self.feed._once()
            self.assertEqual(self.feed.proc_gen, self.feed.generation)
        self.assertEqual(len(list(Path('/proc/self/fd').iterdir())), before)
        self.assertTrue(all(c.waited for c in self.children[:-1]))

    def test_G_continuing_unsigned_and_stale_evidence(self):
        self.begin()
        for n in range(8):
            self.now = n * .5
            result = self.deliver(MEDIA)
            self.feed._once()
            self.assertEqual(result['status'], 'UNKNOWN')
        self.assertEqual(len(self.children), 1)
        self.assertEqual(self.deliver()['status'], 'ACTIVE')
        for n in range(1, 12):
            self.now = 3.5 + n * .5
            result = self.deliver(MEDIA)
            self.feed._once()
        self.assertEqual(result['reason'], 'evidence_stale')
        self.assertEqual(len(self.children), 1)

    def test_no_byte_watchdog_independent_of_scheduler_reset(self):
        self.begin()
        self.deliver()
        self.now = 2.01
        self.feed._once()
        self.assertIn('no-byte-timeout', self.feed.events)
        self.assertIsNone(self.feed.proc)

    def test_obsolete_progress_cannot_refresh_new_generation(self):
        old = self.feed.factory(BINDING)
        new = self.feed.factory(BINDING)
        try:
            old.progress()
            self.assertIsNone(self.feed.last_byte)
        finally:
            old.close()
            new.close()

    def test_factory_during_spawn_and_shutdown(self):
        sources = []
        real_spawn = self.spawn

        def spawn(*args, **kwargs):
            sources.append(self.feed.factory(BINDING))
            self.feed.stop_event.set()
            return real_spawn(*args, **kwargs)

        try:
            self.tick()
            with patch('nanexus_trust.frigate.host_entry.subprocess.Popen', spawn):
                self.feed._once()
            self.assertIsNone(self.feed.proc)
            self.assertTrue(self.children[0].waited)
            os.fstat(self.feed.write)
        finally:
            for source in sources:
                source.close()

    def test_concurrent_once_serializes_spawn(self):
        self.tick()
        entered, release = threading.Event(), threading.Event()
        spawn = self.spawn
        errors = []

        def slow_spawn(*args, **kwargs):
            entered.set()
            if not release.wait(2):
                raise AssertionError('spawn barrier timeout')
            return spawn(*args, **kwargs)

        def once():
            try:
                self.feed._once()
            except Exception as exc:
                errors.append(exc)

        with patch('nanexus_trust.frigate.host_entry.subprocess.Popen', slow_spawn):
            first = threading.Thread(target=once)
            second = threading.Thread(target=once)
            first.start()
            self.assertTrue(entered.wait(1))
            second.start()
            replacement = self.feed.factory(BINDING)
            release.set()
            first.join(2)
            second.join(2)
            try:
                self.assertFalse(first.is_alive() or second.is_alive())
                self.assertFalse(errors)
                self.assertEqual(len(self.children), 1)
                self.assertTrue(self.children[0].waited)
                os.fstat(self.feed.write)
            finally:
                replacement.close()


class RealProcessTests(unittest.TestCase):
    def test_forced_termination_and_concurrent_factory_shutdown(self):
        feed = Feed('cam', (sys.executable, '-c',
                    'import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); '
                    'print("ready",flush=True); time.sleep(60)'), .05, .1, .5)
        sources = [feed.factory(BINDING)]
        self.addCleanup(feed.stop)
        try:
            feed._once()
            proc = feed.proc
            deadline = time.monotonic() + 2
            sources[0].next_event('0')
            while time.monotonic() < deadline:
                event = sources[0].next_event('0')
                if event and event.kind == 'bytes':
                    break
                time.sleep(.01)
            else:
                self.fail('child never became ready')
            errors = []

            def replace():
                try:
                    for _ in range(30):
                        s = feed.factory(BINDING)
                        sources.append(s)
                except OSError:
                    pass  # Shutdown rejects new factories.
                except Exception as exc:
                    errors.append(exc)

            thread = threading.Thread(target=replace)
            thread.start()
            started = time.monotonic()
            feed.stop()
            thread.join(1)
            self.assertFalse(thread.is_alive())
            self.assertFalse(errors)
            self.assertLess(time.monotonic() - started, 3.5)
            self.assertIsNotNone(proc.poll())
            self.assertIn('forced-termination', feed.events)
            self.assertIsNone(feed.write)
        finally:
            for source in sources:
                source.close()

    @unittest.skipUnless(os.environ.get('NANEXUS_TEST_FFMPEG'), 'pinned ffmpeg path required')
    def test_C_loopback_rtsp_stall(self):
        """Real socket/ffmpeg check; also prove watchdog precedes socket timeout."""
        ffmpeg = os.environ['NANEXUS_TEST_FFMPEG']
        help_result = subprocess.run([ffmpeg, '-hide_banner', '-h', 'demuxer=rtsp'],
                                     capture_output=True, timeout=5)
        self.assertIn(b'-timeout ', help_result.stdout)
        for watchdog in (False, True):
            with self.subTest(watchdog=watchdog), socket.socket() as server:
                server.bind(('127.0.0.1', 0))
                server.listen()
                server.settimeout(3)
                feed = Feed('cam', reader_command(ffmpeg, 'rtsp://127.0.0.1:%d/stall' %
                            server.getsockname()[1]), .05, .1, .5,
                            startup_timeout=.3 if watchdog else 15)
                source = feed.factory(BINDING)
                try:
                    feed._once()
                    proc = feed.proc
                    connection, _ = server.accept()
                    with connection:
                        connection.settimeout(2)
                        self.assertTrue(connection.recv(4096))
                        if watchdog:
                            time.sleep(.35)
                            self.assertIsNone(proc.poll())
                            feed._once()
                            self.assertIn('startup-timeout', feed.events)
                        else:
                            proc.wait(timeout=8)
                            self.assertNotEqual(proc.returncode, 0)
                            feed._once()
                            self.assertIn('child-exit', feed.events)
                        self.assertIsNotNone(proc.poll())
                finally:
                    feed.stop()
                    source.close()


if __name__ == '__main__':
    unittest.main()
