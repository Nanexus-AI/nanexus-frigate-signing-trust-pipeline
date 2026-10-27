"""Argument and pin checks for the Historical Worker build entry. Does not compile."""
import importlib.util
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / 'tools/build_historical_worker.py'
SIBLING = ROOT.parent / 'nanexus-video-trust-toolkit'


def load():
    spec = importlib.util.spec_from_file_location('build_historical_worker', TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BuildEntryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load()

    def test_source_file_has_no_developer_path(self):
        text = TOOL.read_text()
        self.assertNotIn('/home/', text)
        self.assertNotIn('.local/', text)
        self.assertNotIn('192.168.', text)

    def test_missing_toolkit_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / 'out'
            with self.assertRaises(self.mod.BuildFailure) as caught:
                self.mod.verify(ROOT, Path(temp) / 'absent-toolkit', output)
        self.assertEqual(caught.exception.code, 'missing')

    def test_toolkit_commit_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / 'toolkit'
            repo.mkdir()
            env = os.environ | {
                'GIT_AUTHOR_NAME': 'Build Test',
                'GIT_AUTHOR_EMAIL': 'build-test@example.com',
                'GIT_COMMITTER_NAME': 'Build Test',
                'GIT_COMMITTER_EMAIL': 'build-test@example.com',
            }
            subprocess.run(['git', 'init'], cwd=repo, check=True, capture_output=True, env=env)
            (repo / 'README').write_text('not the pinned toolkit\n')
            subprocess.run(['git', 'add', 'README'], cwd=repo, check=True, capture_output=True, env=env)
            subprocess.run(['git', 'commit', '-m', 'unrelated'], cwd=repo, check=True, capture_output=True, env=env)
            with self.assertRaises(self.mod.BuildFailure) as caught:
                self.mod.verify(ROOT, repo, Path(temp) / 'out')
        self.assertEqual(caught.exception.code, 'version')

    def test_compiler_mismatch_is_rejected(self):
        if not (SIBLING / '.git').is_dir():
            self.skipTest('sibling toolkit checkout is not present')
        original = self.mod.compiler_text
        self.mod.compiler_text = lambda: 'g++ (example) 99.0.0\n'
        try:
            with tempfile.TemporaryDirectory() as temp:
                with self.assertRaises(self.mod.BuildFailure) as caught:
                    self.mod.verify(ROOT, SIBLING, Path(temp) / 'out')
        finally:
            self.mod.compiler_text = original
        self.assertEqual(caught.exception.code, 'version')

    def test_source_hash_mismatch_is_rejected(self):
        if not (SIBLING / '.git').is_dir():
            self.skipTest('sibling toolkit checkout is not present')
        original = dict(self.mod.PIPELINE_SOURCES)
        self.mod.PIPELINE_SOURCES = dict(original)
        self.mod.PIPELINE_SOURCES[self.mod.ENTRY] = '0' * 64
        try:
            with tempfile.TemporaryDirectory() as temp:
                with self.assertRaises(self.mod.BuildFailure) as caught:
                    self.mod.verify(ROOT, SIBLING, Path(temp) / 'out')
        finally:
            self.mod.PIPELINE_SOURCES = original
        self.assertEqual(caught.exception.code, 'hash')

    def test_command_selects_pinned_sources_inside_output(self):
        if not (SIBLING / '.git').is_dir():
            self.skipTest('sibling toolkit checkout is not present')
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / 'out'
            plan = self.mod.verify(ROOT, SIBLING, output)
        command = plan['command']
        joined = '\n'.join(command)
        self.assertIn(str((ROOT / self.mod.ENTRY).resolve()), command)
        self.assertIn(str((SIBLING / self.mod.OBSERVATION).resolve()), command)
        self.assertNotIn('continuity_child.cpp', joined)
        self.assertNotIn('hv2-cont09', joined)
        self.assertEqual(command[:4], ['g++', '-std=c++20', '-O1', '-Wno-deprecated-declarations'])
        self.assertIn('-lcrypto', command)
        self.assertIn('-Wl,-z,now', command)
        binary = Path(command[command.index('-o') + 1])
        self.assertTrue(self.mod.inside(output, binary))
        self.assertEqual(binary.name, 'worker_child')
        self.assertTrue(self.mod.inside(plan['output'], plan['record']))

    def test_refuses_writes_outside_and_existing_destination(self):
        if not (SIBLING / '.git').is_dir():
            self.skipTest('sibling toolkit checkout is not present')
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            outside = root / 'outside'
            outside.mkdir()
            output = root / 'out'
            output.mkdir()
            escaped = outside / 'worker_child'
            link = output / 'worker_child'
            link.symlink_to(escaped)
            with self.assertRaises(self.mod.BuildFailure) as caught:
                self.mod.verify(ROOT, SIBLING, output)
            self.assertEqual(caught.exception.code, 'output')
            self.assertFalse(escaped.exists())
        with self.assertRaises(self.mod.BuildFailure) as caught:
            self.mod.verify(ROOT, SIBLING, ROOT / 'src' / 'worker-out')
        self.assertEqual(caught.exception.code, 'output')


if __name__ == '__main__':
    unittest.main()
