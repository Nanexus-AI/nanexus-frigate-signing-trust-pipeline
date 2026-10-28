import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / 'tools'))
import build_historical_worker as entry

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / 'tools' / 'build_historical_worker.py'
ACCEPTED = 'cfe8d86efebd749e82e308d86f88316795fcc6828bb205da3d8f2d151e8d4d22'
SIBLING = ROOT.parent / 'nanexus-video-trust-toolkit'
HEADER_SOURCE = SIBLING / 'third_party' / 'media-signing-framework' / 'lib' / 'src' / 'includes'
LIBRARY_SOURCE = SIBLING / '.oms-prefix' / 'lib' / 'x86_64-linux-gnu' / 'libmedia-signing-framework.so'


class BuildEntryTests(unittest.TestCase):
    def test_source_has_no_developer_path_and_keeps_the_accepted_pin(self):
        text = SCRIPT.read_text()
        self.assertNotIn('/home/', text)
        self.assertNotIn('.local/', text)
        self.assertNotIn('192.168.', text)
        self.assertNotIn('--toolkit', text)
        self.assertIn(ACCEPTED, text)
        self.assertIn("'-Wl,-z,now'", text)
        self.assertNotIn('$ORIGIN', text)

    def test_missing_oms_inputs_are_rejected(self):
        with tempfile.TemporaryDirectory() as raw:
            output = Path(raw) / 'out'
            with self.assertRaises(entry.BuildFailure) as caught:
                entry.verify(ROOT, Path(raw) / 'absent-headers', Path(raw) / 'absent.so', output)
            self.assertEqual(caught.exception.code, 'missing')

    def test_oms_header_and_library_hashes_are_checked(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            headers = root / 'headers'
            headers.mkdir()
            for name in entry.OMS_HEADERS:
                (headers / name).write_bytes(b'not-the-pinned-header')
            library = root / 'library.so'
            library.write_bytes(b'not-the-pinned-library')
            with self.assertRaises(entry.BuildFailure) as header:
                entry.verify(ROOT, headers, library, root / 'out')
            self.assertEqual(header.exception.code, 'hash')
            if not self._inputs_present():
                self.skipTest('pinned OMS inputs are not available on this machine')
            for name in entry.OMS_HEADERS:
                shutil.copyfile(HEADER_SOURCE / name, headers / name)
            with self.assertRaises(entry.BuildFailure) as library_error:
                entry.verify(ROOT, headers, library, root / 'out')
            self.assertEqual(library_error.exception.code, 'hash')

    def test_compiler_identity_is_pinned(self):
        original = entry.compiler_text
        entry.compiler_text = lambda: 'g++ (example) 99.0.0\n'
        try:
            with tempfile.TemporaryDirectory() as raw:
                root = Path(raw)
                headers = root / 'headers'
                headers.mkdir()
                with self.assertRaises(entry.BuildFailure) as caught:
                    entry.verify(ROOT, headers, root / 'library.so', root / 'out')
        finally:
            entry.compiler_text = original
        self.assertEqual(caught.exception.code, 'version')

    def test_pipeline_source_hash_is_checked(self):
        original = entry.PIPELINE_SOURCES[entry.ENTRY]
        entry.PIPELINE_SOURCES[entry.ENTRY] = '0' * 64
        try:
            with tempfile.TemporaryDirectory() as raw:
                root = Path(raw)
                headers = root / 'headers'
                headers.mkdir()
                with self.assertRaises(entry.BuildFailure) as caught:
                    entry.verify(ROOT, headers, root / 'library.so', root / 'out')
        finally:
            entry.PIPELINE_SOURCES[entry.ENTRY] = original
        self.assertEqual(caught.exception.code, 'hash')

    def test_command_uses_pipeline_source_and_absolute_runpath(self):
        if not self._inputs_present():
            self.skipTest('pinned OMS inputs are not available on this machine')
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            headers, library = self._copy_inputs(root)
            output = root / 'out'
            plan = entry.verify(ROOT, headers, library, output)
            command = plan['command']
            self.assertIn(str((ROOT / entry.ENTRY).resolve()), command)
            self.assertIn(str((ROOT / entry.OBSERVATION).resolve()), command)
            self.assertIn(str((ROOT / entry.INCLUDE).resolve()), command)
            self.assertIn(str(headers.resolve()), command)
            self.assertIn(str(library.resolve()), command)
            self.assertIn('-lcrypto', command)
            self.assertIn('-Wl,-z,now', command)
            self.assertIn(f'-Wl,-rpath,{library.resolve().parent}', command)
            joined = '\n'.join(command)
            self.assertNotIn('nanexus-video-trust-toolkit', joined)
            self.assertNotIn('continuity_child.cpp', joined)
            self.assertNotIn('hv2-cont09', joined)
            self.assertEqual(command[0:4], ['g++', '-std=c++20', '-O1', '-Wno-deprecated-declarations'])
            self.assertEqual(command[-1], str(output.resolve() / 'worker_child'))

    def test_output_cannot_escape_or_overwrite(self):
        if not self._inputs_present():
            self.skipTest('pinned OMS inputs are not available on this machine')
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            headers, library = self._copy_inputs(root)
            outside = root / 'outside'
            outside.mkdir()
            output = root / 'out'
            output.mkdir()
            escaped = outside / 'worker_child'
            (output / 'worker_child').symlink_to(escaped)
            with self.assertRaises(entry.BuildFailure) as caught:
                entry.verify(ROOT, headers, library, output)
            self.assertEqual(caught.exception.code, 'output')
            self.assertFalse(escaped.exists())
            with self.assertRaises(entry.BuildFailure) as source_dir:
                entry.verify(ROOT, headers, library, ROOT / 'src' / 'worker-build')
            self.assertEqual(source_dir.exception.code, 'output')

    def _inputs_present(self):
        return HEADER_SOURCE.is_dir() and LIBRARY_SOURCE.is_file()

    def _copy_inputs(self, root):
        headers = root / 'headers'
        headers.mkdir()
        for name in entry.OMS_HEADERS:
            target = headers / name
            target.write_bytes((HEADER_SOURCE / name).read_bytes())
        library_dir = root / 'lib'
        library_dir.mkdir()
        real = LIBRARY_SOURCE.resolve()
        copied = library_dir / real.name
        copied.write_bytes(real.read_bytes())
        link = library_dir / 'libmedia-signing-framework.so'
        if link != copied:
            link.symlink_to(copied.name)
        return headers, link


if __name__ == '__main__':
    unittest.main()
