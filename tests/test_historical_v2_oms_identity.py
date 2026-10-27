"""OMS SONAME identity before a product Worker launch. Fixtures carry their own RUNPATH."""
import hashlib
import os
import shutil
import stat
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from nanexus_trust.historical_v2.continuity_protocol import ProtocolError
from nanexus_trust.historical_v2.continuity_worker import (
    OMS_SHA256, OMS_SONAME, admit_oms_library, resolved_oms_library, run_context)
from nanexus_trust.historical_v2.rolling_product import WORKER_SHA256
import test_historical_v2_rolling_product as product

ROOT = Path(__file__).resolve().parents[1]
ACCEPTED = 'cfe8d86efebd749e82e308d86f88316795fcc6828bb205da3d8f2d151e8d4d22'
PRODUCT = 'd12125f5923f82e6b17d923791b0069ff30820c1381fd405b47f78c0f18e3f47'
ACCEPTED_WORKER = ROOT / '.local/runtime/hv2-cont33r2/snapshot/launcher/worker_child'
STAGED = ROOT / '.local/runtime/hv2-product-worker-isolated/worker_child'


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def write_elf(destination, runpath, *, needed=OMS_SONAME, run_tag=29):
    """Tiny ELF64 whose only useful dynamic facts are one NEEDED name and one RUNPATH."""
    soname = needed.encode('ascii') + b'\0'
    run = runpath.encode('ascii') + b'\0'
    strtab = soname + run
    phoff = 64
    dynoff = phoff + 2 * 56
    dynsz = 5 * 16
    stroff = dynoff + dynsz
    filesz = stroff + len(strtab)

    def phdr(ptype, offset, size):
        return struct.pack('<IIQQQQQQ', ptype, 4, offset, offset, offset, size, size, 8)

    def dyn(tag, value):
        return struct.pack('<QQ', tag, value)

    dynamic = b''.join((
        dyn(1, 0), dyn(5, stroff), dyn(10, len(strtab)), dyn(run_tag, len(soname)), dyn(0, 0)))
    header = struct.pack(
        '<16sHHIQQQIHHHHHH', b'\x7fELF\x02\x01\x01' + bytes(9),
        3, 62, 1, 0, phoff, 0, 0, 64, 56, 2, 0, 0, 0)
    blob = header + phdr(1, 0, filesz) + phdr(2, dynoff, dynsz) + dynamic + strtab
    if len(blob) != filesz or len(dynamic) != dynsz:
        raise RuntimeError('fixture-elf')
    destination.write_bytes(blob)


class OmsIdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pinned = resolved_oms_library(ACCEPTED_WORKER).read_bytes()
        if sha256(cls.pinned) != OMS_SHA256:
            raise AssertionError('pinned-oms-fixture')

    def test_constants_and_source_do_not_name_a_checkout(self):
        self.assertEqual(WORKER_SHA256, ACCEPTED)
        self.assertEqual(OMS_SHA256, 'e157fd03c14ba2ae63ce19800cb00d994bc4500a80d84f88492d5537cda76c98')
        self.assertEqual(OMS_SONAME, 'libmedia-signing-framework.so.25')
        text = (ROOT / 'src/nanexus_trust/historical_v2/continuity_worker.py').read_text()
        self.assertNotIn('/home/', text)
        self.assertNotIn('nanexus-video-trust-toolkit', text)
        self.assertNotIn(PRODUCT, text)
        product_source = (ROOT / 'src/nanexus_trust/historical_v2/rolling_product.py').read_text()
        self.assertIn(f"WORKER_SHA256 = '{ACCEPTED}'", product_source)

    def test_accepted_worker_resolves_its_pinned_library(self):
        final = resolved_oms_library(ACCEPTED_WORKER)
        link = final.parent / OMS_SONAME
        self.assertTrue(link.is_symlink())
        self.assertEqual(os.readlink(link), final.name)
        self.assertNotIn('/', os.readlink(link))
        self.assertTrue(stat.S_ISREG(final.lstat().st_mode))
        self.assertEqual(sha256(final.read_bytes()), OMS_SHA256)
        admit_oms_library(ACCEPTED_WORKER)
        admit_oms_library(STAGED)

    def test_missing_library_is_rejected_without_a_system_fallback(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            declared = root / 'declared'
            decoy = root / 'decoy'
            declared.mkdir()
            decoy.mkdir()
            (decoy / OMS_SONAME).write_bytes(self.pinned)
            elf = root / 'worker'
            write_elf(elf, str(declared))
            with self.assertRaises(ProtocolError) as caught:
                admit_oms_library(elf)
            self.assertEqual(str(caught.exception), 'oms-library-identity')
            self.assertEqual(sha256((decoy / OMS_SONAME).read_bytes()), OMS_SHA256)

    def test_wrong_library_bytes_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            libdir = root / 'lib'
            libdir.mkdir()
            (libdir / OMS_SONAME).write_bytes(self.pinned[:-1] + bytes([self.pinned[-1] ^ 0x01]))
            elf = root / 'worker'
            write_elf(elf, str(libdir))
            with self.assertRaises(ProtocolError) as caught:
                admit_oms_library(elf)
            self.assertEqual(str(caught.exception), 'oms-library-identity')

    def test_soname_symlink_must_be_one_same_directory_file(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cases = {}
            missing = root / 'missing'
            missing.mkdir()
            cases['missing'] = (missing, missing / 'worker')
            broken = root / 'broken'
            broken.mkdir()
            (broken / OMS_SONAME).symlink_to('absent.so')
            cases['broken'] = (broken, broken / 'worker')
            absolute = root / 'absolute'
            absolute.mkdir()
            outside = root / 'outside.so'
            outside.write_bytes(self.pinned)
            (absolute / OMS_SONAME).symlink_to(outside)
            cases['absolute'] = (absolute, absolute / 'worker')
            escaped = root / 'escaped'
            nested = escaped / 'nested'
            nested.mkdir(parents=True)
            (nested / 'pinned.so').write_bytes(self.pinned)
            (escaped / OMS_SONAME).symlink_to('nested/pinned.so')
            cases['escaped'] = (escaped, escaped / 'worker')
            chained = root / 'chained'
            chained.mkdir()
            (chained / 'pinned.so').write_bytes(self.pinned)
            (chained / 'mid.so').symlink_to('pinned.so')
            (chained / OMS_SONAME).symlink_to('mid.so')
            cases['chained'] = (chained, chained / 'worker')
            for name, (libdir, elf) in cases.items():
                with self.subTest(name=name):
                    write_elf(elf, str(libdir))
                    with self.assertRaises(ProtocolError) as caught:
                        admit_oms_library(elf)
                    self.assertEqual(str(caught.exception), 'oms-library-identity')

    def test_runpath_must_be_one_supported_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            good = root / 'good'
            other = root / 'other'
            good.mkdir()
            other.mkdir()
            (good / OMS_SONAME).write_bytes(self.pinned)
            (other / OMS_SONAME).write_bytes(self.pinned)
            elf = root / 'worker'
            samples = {
                'two-directories': f'{good}:{other}',
                'origin-and-lib-token': '$ORIGIN/$LIB',
                'relative': 'lib',
                'empty': '',
                'rpath-only': str(good),
                'wrong-needed': str(good),
            }
            for name, runpath in samples.items():
                with self.subTest(name=name):
                    kwargs = {}
                    if name == 'rpath-only':
                        kwargs['run_tag'] = 15
                    if name == 'wrong-needed':
                        kwargs['needed'] = 'libcrypto.so.3'
                    write_elf(elf, runpath, **kwargs)
                    with self.assertRaises(ProtocolError) as caught:
                        admit_oms_library(elf)
                    self.assertEqual(str(caught.exception), 'oms-library-identity')

    def test_origin_layout_accepts_symlink_or_regular_file(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            libdir = root / 'lib'
            bindir = root / 'bin'
            libdir.mkdir()
            bindir.mkdir()
            target = libdir / 'pinned.so'
            target.write_bytes(self.pinned)
            (libdir / OMS_SONAME).symlink_to(target.name)
            elf = bindir / 'worker'
            write_elf(elf, '$ORIGIN/../lib')
            admit_oms_library(elf)
            (libdir / OMS_SONAME).unlink()
            (libdir / OMS_SONAME).write_bytes(self.pinned)
            admit_oms_library(elf)
            braced = bindir / 'braced'
            write_elf(braced, '${ORIGIN}/../lib')
            admit_oms_library(braced)

    def test_glibc_hwcaps_override_is_rejected_before_exec(self):
        plan, request, *_ = product.fixture(1)
        trust = b'CA'
        def refuse(*args, **kwargs):
            raise AssertionError('load')
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            libdir = root / 'lib'
            override = libdir / 'glibc-hwcaps' / 'x86-64-v3'
            override.mkdir(parents=True)
            (libdir / OMS_SONAME).write_bytes(self.pinned)
            (override / OMS_SONAME).write_bytes(b'different-oms-object')
            elf = root / 'bin' / 'worker'
            elf.parent.mkdir()
            write_elf(elf, '$ORIGIN/../lib')
            with self.assertRaises(ProtocolError) as caught:
                admit_oms_library(elf)
            self.assertEqual(str(caught.exception), 'oms-library-identity')
            self.assertNotIn(str(root), str(caught.exception))
            digest = sha256(elf.read_bytes())
            started = []
            def explode(*args, **kwargs):
                started.append(args)
                raise AssertionError('popen')
            with patch('nanexus_trust.historical_v2.continuity_worker.subprocess.Popen', explode):
                outcome = run_context(elf, plan, request, 'owner', 'attempt', trust, refuse, refuse,
                                      lambda: None, timeout=5, _fault='check', worker_sha256=digest)
            self.assertEqual(started, [])
            self.assertIsNone(outcome.pid)
            self.assertEqual(outcome.state, 'FAILED')
            self.assertIn('oms-library-identity', outcome.error)
            self.assertNotIn(str(root), outcome.error)

    def test_hwcaps_symlink_file_and_loop_use_the_identity_error(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            layouts = ('linked', 'dangling', 'empty', 'file')
            for name in layouts:
                libdir = root / name
                libdir.mkdir()
                (libdir / OMS_SONAME).write_bytes(self.pinned)
                elf = root / f'{name}-worker'
                if name == 'linked':
                    tree = root / 'elsewhere'
                    (tree / 'x86-64-v3').mkdir(parents=True)
                    (tree / 'x86-64-v3' / OMS_SONAME).write_bytes(b'different-oms-object')
                    (libdir / 'glibc-hwcaps').symlink_to(tree, target_is_directory=True)
                elif name == 'dangling':
                    (libdir / 'glibc-hwcaps').symlink_to('missing-tree')
                elif name == 'empty':
                    (libdir / 'glibc-hwcaps').mkdir()
                else:
                    (libdir / 'glibc-hwcaps').write_bytes(b'not-a-directory')
                write_elf(elf, str(libdir))
                with self.subTest(name=name):
                    with self.assertRaises(ProtocolError) as caught:
                        admit_oms_library(elf)
                    self.assertEqual(str(caught.exception), 'oms-library-identity')
                    self.assertNotIn(str(root), str(caught.exception))
            left = root / 'loop-left'
            right = root / 'loop-right'
            left.symlink_to(right)
            right.symlink_to(left)
            loop = root / 'loop-worker'
            write_elf(loop, str(left))
            with self.assertRaises(ProtocolError) as caught:
                admit_oms_library(loop)
            self.assertEqual(str(caught.exception), 'oms-library-identity')

    def test_unrelated_subdirectory_does_not_replace_the_base_library(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            libdir = root / 'lib'
            (libdir / 'x86-64-v3').mkdir(parents=True)
            (libdir / 'pkgconfig').mkdir()
            (libdir / OMS_SONAME).write_bytes(self.pinned)
            (libdir / 'x86-64-v3' / OMS_SONAME).write_bytes(b'different-oms-object')
            (libdir / 'pkgconfig' / OMS_SONAME).write_bytes(b'different-oms-object')
            elf = root / 'bin' / 'worker'
            elf.parent.mkdir()
            write_elf(elf, '$ORIGIN/../lib')
            admit_oms_library(elf)

    def test_disposable_loader_prefers_hwcaps_and_ignores_a_sibling(self):
        gcc = shutil.which('gcc')
        self.assertIsNotNone(gcc)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            lib = root / 'lib'
            (lib / 'glibc-hwcaps' / 'x86-64-v3').mkdir(parents=True)
            (lib / 'x86-64-v3').mkdir()
            (root / 'base.c').write_text('int probe_marker(void){return 11;}\n')
            (root / 'hw.c').write_text('int probe_marker(void){return 33;}\n')
            (root / 'side.c').write_text('int probe_marker(void){return 44;}\n')
            (root / 'main.c').write_text(
                '#include <stdio.h>\nint probe_marker(void);\n'
                'int main(void){printf("%d\\n", probe_marker());return 0;}\n')
            shared = [gcc, '-shared', '-fPIC', '-Wl,-soname,libprobe.so']
            subprocess.check_call(shared + ['-o', str(lib / 'libprobe.so'), str(root / 'base.c')])
            subprocess.check_call(shared + ['-o', str(lib / 'glibc-hwcaps' / 'x86-64-v3' / 'libprobe.so'), str(root / 'hw.c')])
            subprocess.check_call(shared + ['-o', str(lib / 'x86-64-v3' / 'libprobe.so'), str(root / 'side.c')])
            subprocess.check_call([gcc, '-o', str(root / 'probe'), str(root / 'main.c'),
                                   '-L', str(lib), '-lprobe', '-Wl,-rpath,$ORIGIN/lib'])
            env = {'LANG': 'C', 'LC_ALL': 'C'}
            def marker():
                return subprocess.check_output([str(root / 'probe')], env=env, text=True).strip()
            self.assertEqual(marker(), '33')
            listed = subprocess.check_output(['ldd', str(root / 'probe')], env=env, text=True)
            probe_line = next(line for line in listed.splitlines() if 'libprobe.so' in line)
            self.assertIn('glibc-hwcaps', probe_line)
            shutil.rmtree(lib / 'glibc-hwcaps')
            self.assertEqual(marker(), '11')
            listed = subprocess.check_output(['ldd', str(root / 'probe')], env=env, text=True)
            probe_line = next(line for line in listed.splitlines() if 'libprobe.so' in line)
            self.assertNotIn('glibc-hwcaps', probe_line)
            self.assertNotIn('/x86-64-v3/', probe_line)

    def test_failed_identity_does_not_start_the_worker(self):
        plan, request, *_ = product.fixture(1)
        trust = b'CA'
        def refuse(*args, **kwargs):
            raise AssertionError('load')
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            libdir = root / 'lib'
            libdir.mkdir()
            elf = root / 'worker'
            write_elf(elf, str(libdir))
            digest = sha256(elf.read_bytes())
            started = []
            def explode(*args, **kwargs):
                started.append(args)
                raise AssertionError('popen')
            with patch('nanexus_trust.historical_v2.continuity_worker.subprocess.Popen', explode):
                outcome = run_context(elf, plan, request, 'owner', 'attempt', trust, refuse, refuse,
                                      lambda: None, timeout=5, _fault='check', worker_sha256=digest)
            self.assertEqual(started, [])
            self.assertIsNone(outcome.pid)
            self.assertEqual(outcome.state, 'FAILED')
            self.assertIn('oms-library-identity', outcome.error)
            self.assertFalse(outcome.published)

    def test_executable_hash_still_rejects_before_launch(self):
        plan, request, *_ = product.fixture(1)
        trust = b'CA'
        def refuse(*args, **kwargs):
            raise AssertionError('load')
        started = []
        def explode(*args, **kwargs):
            started.append(args)
            raise AssertionError('popen')
        with tempfile.TemporaryDirectory() as temp:
            wrong = Path(temp) / 'worker'
            wrong.write_bytes(b'not-the-accepted-worker')
            with patch('nanexus_trust.historical_v2.continuity_worker.subprocess.Popen', explode):
                outcome = run_context(wrong, plan, request, 'owner', 'attempt', trust, refuse, refuse,
                                      lambda: None, timeout=5, _fault='check')
            self.assertEqual(started, [])
            self.assertIsNone(outcome.pid)
            self.assertEqual(outcome.state, 'FAILED')
            self.assertIn('product-worker-identity', outcome.error)
            self.assertNotIn('oms-library-identity', outcome.error)
        with patch('nanexus_trust.historical_v2.continuity_worker.subprocess.Popen', explode):
            staged = run_context(STAGED, plan, request, 'owner', 'attempt', trust, refuse, refuse,
                                 lambda: None, timeout=5, _fault='check')
        self.assertEqual(started, [])
        self.assertEqual(staged.state, 'FAILED')
        self.assertIn('product-worker-identity', staged.error)

    def test_accepted_launch_still_reaches_the_isolation_check(self):
        plan, request, *_ = product.fixture(1)
        trust = b'CA'
        def refuse(*args, **kwargs):
            raise AssertionError('load')
        outcome = run_context(ACCEPTED_WORKER, plan, request, 'owner', 'attempt', trust, refuse, refuse,
                              lambda: None, timeout=5, _fault='check')
        self.assertEqual(outcome.state, 'ISOLATION_CHECKED')
        self.assertIsNone(outcome.error)
        self.assertEqual(outcome.exit_code, 0)
        self.assertIsNotNone(outcome.pid)
        self.assertEqual(sha256(ACCEPTED_WORKER.read_bytes()), ACCEPTED)
        self.assertEqual(WORKER_SHA256, ACCEPTED)


if __name__ == '__main__':
    unittest.main()
