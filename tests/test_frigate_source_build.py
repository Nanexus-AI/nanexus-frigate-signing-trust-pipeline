"""Offline handoff boundaries. No Docker/network/compiler/Host Entry execution."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import struct
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('source_build', ROOT / 'tools/frigate_source_build.py')
build = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build)


class SourceBuildTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.output = self.base / 'output'
        self.output.mkdir(mode=0o700)
        self.art = self.output / 'artifacts'
        self.art.mkdir()
        self.api = {name: name.encode() for name in build.api_overlay.OUTPUT_NAMES}
        self.addCleanup(patch.stopall)
        patch.object(build.api_overlay, 'ACCEPTED_SHA256', {
            n: build.hashlib.sha256(b).hexdigest() for n, b in self.api.items()}).start()
        patch.object(build.selector, 'read_dynamic', side_effect=self.dynamic).start()
        # Tripwire: unmocked subprocess anywhere is a test failure, never real work.
        self.process = patch('subprocess.run', side_effect=AssertionError('offline subprocess tripwire')).start()
        patch('subprocess.check_output', side_effect=AssertionError('offline subprocess tripwire')).start()
        for directory in build.TOP - {'boot.py', 'service-enable'}:
            (self.art / directory).mkdir()
        self.put('web/index.html', '<script src="/BASE_PATH/assets/main-local.js"></script>')
        self.put('web/login.html', '<link href="/BASE_PATH/assets/main-local.css">')
        for name in ['main-local.js', 'main-local.css', 'editor.worker.bundle.js', 'yaml.worker.bundle.js']:
            self.put('web/assets/' + name, 'generated fixture')
        for name, value in self.api.items():
            self.put('api/' + name, value)
        for name in build.NGINX_FILES:
            self.put('nginx/' + name, 'canonical fixture ' + name)
        for name in build.navigation_overlay.UI_SHA256:
            shutil.copyfile(ROOT / 'ui/nanexus' / name, self.art / 'ui' / name)
        self.put('src/nanexus_trust/frigate/host_entry.py', '# source fixture\n')
        self.put('boot.py', build.private.BOOT)
        self.put('service/run', build.RUN)
        self.put('service/type', 'longrun\n')
        self.put('service-enable', '')
        elf = b'\x7fELF\x02\x01' + bytes(12) + struct.pack('<H', 62) + bytes(44)
        self.put('pair/bin/worker_child', elf + b'worker fixture')
        self.put('pair/lib/' + build.pair.LIBRARY_NAME, elf + b'library fixture')
        (self.art / build.SONAME_PATH).symlink_to(build.pair.LIBRARY_NAME)
        self.put('pair/build-manifest.json', json.dumps({
            'schema': 'nanexus-hv2-runtime-pair/1', 'selected': False,
            'worker_sha256': build.digest(self.art / 'pair/bin/worker_child'),
            'oms_sha256': build.digest(self.art / 'pair/lib' / build.pair.LIBRARY_NAME)}))
        for name in ['pipeline-LICENSE', 'pipeline-NOTICE', 'frigate-LICENSE', 'frigate-TRADEMARK.md',
                     'frigate-branding-LICENSE', 'oms-LICENSE', 'oms-PROVENANCE']:
            self.put('licenses/' + name, 'notice fixture')
        build.normalize(self.art)
        self.seal()

    def put(self, rel, data):
        path = self.art / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data.encode() if isinstance(data, str) else data)

    @staticmethod
    def dynamic(path):
        if path.name == 'worker_child':
            return {'needed': [build.pair.SONAME, 'libcrypto.so.3', 'libc.so.6'],
                    'rpath': [], 'runpath': [build.pair.RUNPATH], 'soname': []}
        return {'needed': ['libcrypto.so.3', 'libc.so.6'], 'soname': [build.pair.SONAME],
                'rpath': [], 'runpath': []}

    @staticmethod
    def image(kind='classic'):
        # Classic Docker: configuration ID differs from registry/index digest.
        image = {'Id': 'sha256:' + '1' * 64, 'RepoDigests': [build.IMAGE],
                 'Architecture': 'amd64', 'Os': 'linux'}
        if kind == 'index':
            image['Id'] = build.DIGEST
            image['Descriptor'] = {'mediaType': 'application/vnd.oci.image.index.v1+json',
                                   'digest': build.DIGEST}
        elif kind == 'platform':
            image['Id'] = 'sha256:' + '2' * 64
            image['Descriptor'] = {'mediaType': 'application/vnd.oci.image.manifest.v1+json',
                                   'digest': image['Id'],
                                   'platform': {'architecture': 'amd64', 'os': 'linux'}}
        return image

    def seal(self):
        files, identities = build.validate_layout(self.art)
        runtime_source = {'src/nanexus_trust/frigate/host_entry.py':
                          build.digest(self.art / 'src/nanexus_trust/frigate/host_entry.py')}
        self.record = {
            'schema': 1, 'profile': build.PROFILE,
            'source': {'commit': 'a' * 40, 'baseline': build.BASELINE, 'files': runtime_source},
            'frigate': {'version': build.VERSION, 'commit': build.COMMIT, 'archive_sha256': build.ARCHIVE,
                        'image': build.IMAGE, **build.check_image([self.image()])},
            'tools': {'fixture': 'not a real build'}, 'commands': [{'fixture': True}],
            'frontend_lock_before': 'b' * 64, 'frontend_lock_after': 'b' * 64,
            'runtime_source': runtime_source, 'pair': identities, 'inventory': files}
        self.save()

    def save(self):
        (self.output / build.MANIFEST).write_text(json.dumps(self.record))

    def test_acceptance_requires_exact_digest_and_deliberate_acknowledgment(self):
        sha = build.verify(self.output)
        with self.assertRaises(ValueError):
            build.verify(self.output, accepted=True)
        for digest, ack in [(sha, False), ('0' * 64, True)]:
            with self.assertRaises(ValueError):
                build.accept(self.output, digest, ack)
        build.accept(self.output, sha, True)
        self.assertEqual(build.verify(self.output, accepted=True), sha)
        self.record['tools']['fixture'] = 'changed record'
        self.save()
        with self.assertRaisesRegex(ValueError, 'not accepted'):
            build.verify(self.output, accepted=True)

    def test_modified_frontend_after_acceptance_fails(self):
        build.accept(self.output, build.verify(self.output), True)
        self.put('web/assets/main-local.js', 'changed bytes')
        with self.assertRaisesRegex(ValueError, 'inventory mismatch'):
            build.verify(self.output, accepted=True)

    def test_required_assets_and_unknown_files_fail(self):
        for rel in ['web/assets/yaml.worker.bundle.js', 'boot.py', 'service/type', 'nginx/mime.types']:
            with self.subTest(rel=rel):
                path = self.art / rel
                data = path.read_bytes()
                mode = path.stat().st_mode & 0o777
                path.unlink()
                with self.assertRaises((ValueError, OSError)):
                    build.verify(self.output)
                path.write_bytes(data)
                path.chmod(mode)
        self.put('unexpected/private.key', 'not permitted')
        with self.assertRaises(ValueError):
            build.verify(self.output)

    def test_pair_replacement_and_alternate_resolution_fail(self):
        for rel in ['pair/bin/worker_child', 'pair/lib/' + build.pair.LIBRARY_NAME]:
            path = self.art / rel
            data = path.read_bytes()
            path.write_bytes(data + b'replacement')
            with self.assertRaises((ValueError, build.selector.SelectionFailure)):
                build.verify(self.output)
            path.write_bytes(data)
        link = self.art / build.SONAME_PATH
        link.unlink()
        link.symlink_to('/tmp/arbitrary-library')
        with self.assertRaisesRegex(ValueError, 'symlink'):
            build.verify(self.output)

    def test_modes_and_architecture_are_enforced(self):
        path = self.art / 'service/run'
        path.chmod(0o777)
        with self.assertRaisesRegex(ValueError, 'mode'):
            build.verify(self.output)
        path.chmod(0o755)
        path = self.art / 'pair/bin/worker_child'
        data = path.read_bytes()
        path.write_bytes(data[:18] + struct.pack('<H', 183) + data[20:])
        # Update only pair file identity to reach the architecture guard.
        record = build.load_json(self.art / 'pair/build-manifest.json')
        record['worker_sha256'] = build.digest(path)
        (self.art / 'pair/build-manifest.json').write_text(json.dumps(record))
        with self.assertRaisesRegex(ValueError, 'amd64'):
            build.verify(self.output)

    def test_symlink_directory_and_unsafe_output_roots_fail(self):
        (self.art / 'web/escape').symlink_to(self.base, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            build.verify(self.output)
        for value in ['relative/output', '/', str(ROOT / 'new-output'),
                      str(self.base / '.local/new-output'), str(self.base / '../escape')]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                build.safe_root(value, ROOT, fresh=True)
        link = self.base / 'alias'
        link.symlink_to(self.output, target_is_directory=True)
        with self.assertRaises(ValueError):
            build.safe_root(link, ROOT)
        with self.assertRaises(ValueError):
            build.safe_root(self.output, ROOT, fresh=True)

    def test_wrong_pinned_archive_image_and_manifest_versions_fail(self):
        archive = self.base / 'upstream.tar.gz'
        archive.write_bytes(b'wrong archive')
        with self.assertRaisesRegex(ValueError, 'archive'):
            build.check_archive(archive)
        for key, value in [('Id', 'sha256:wrong'), ('Architecture', 'arm64'), ('Os', 'windows')]:
            image = self.image()
            image[key] = value
            with self.assertRaises(ValueError):
                build.check_image([image])
        self.record['frigate']['version'] = 'another-version'
        self.save()
        with self.assertRaisesRegex(ValueError, 'Frigate'):
            build.verify(self.output)

    def test_reviewed_source_commit_mismatch_stops_before_build(self):
        runner = build.Runner()
        with patch.object(runner, 'run', return_value='b' * 40):
            with self.assertRaisesRegex(ValueError, 'commit mismatch'):
                build.reviewed_source(ROOT, 'a' * 40, runner)
        self.process.assert_not_called()

    def test_canonical_nginx_is_separate_from_initialized_copy(self):
        working = self.base / 'runtime-nginx'
        shutil.copytree(self.art / 'nginx', working)
        (working / 'nginx.conf').write_text('initialized worker_processes 4;')
        (working / 'listen.conf').write_text('generated TLS listener')
        self.assertEqual(build.verify(self.output), build.digest(self.output / build.MANIFEST))
        self.put('nginx/listen.conf', 'unexpected generated canonical file')
        (self.art / 'nginx/listen.conf').chmod(0o644)
        with self.assertRaisesRegex(ValueError, 'canonical nginx'):
            build.verify(self.output)

    def test_source_provenance_cannot_escape_or_omit_runtime_files(self):
        self.record['runtime_source'] = {'src/../../outside': '0' * 64}
        self.save()
        with self.assertRaisesRegex(ValueError, 'source'):
            build.verify(self.output)

    def test_no_precompiled_input_cli_exists(self):
        with self.assertRaises(SystemExit), patch('sys.stderr'):
            build.parser().parse_args(['build', '--source-commit', 'a' * 40,
                                      '--source-archive', '/upstream', '--output', '/output',
                                      '--frontend', '/private/frontend', '--pair', '/private/pair'])

    def test_monaco_move_and_unexpected_subtree_rejection(self):
        dist = self.base / 'dist'
        (dist / 'assets').mkdir(parents=True)
        source = dist / 'BASE_PATH/monacoeditorwork'
        source.mkdir(parents=True)
        for name in ['editor.worker.bundle.js', 'yaml.worker.bundle.js']:
            (source / name).write_text(name)
        build.relocate_monaco(dist)
        self.assertFalse((dist / 'BASE_PATH').exists())
        self.assertEqual((dist / 'assets/yaml.worker.bundle.js').read_text(), 'yaml.worker.bundle.js')
        source.mkdir(parents=True)
        (source / 'editor.worker.bundle.js').write_text('collision')
        with self.assertRaisesRegex(ValueError, 'collision'):
            build.relocate_monaco(dist)

    def test_failed_extraction_removes_only_created_temporary_container(self):
        runner = build.Runner()
        calls = []
        def run(*args, **kwargs):
            calls.append(args)
            if args[:2] == ('docker', 'create'):
                return 'c' * 64
            if args[:2] == ('docker', 'cp'):
                raise ValueError('copy failed')
            return ''
        with patch.object(runner, 'run', side_effect=run):
            with self.assertRaisesRegex(ValueError, 'copy failed'):
                build.extract_native(self.base, runner)
        name = calls[0][calls[0].index('--name') + 1]
        self.assertEqual(calls[-1], ('docker', 'rm', name))
        self.assertTrue(name.startswith('nanexus-source-extract-'))
        self.assertEqual(calls[0][-1], build.IMAGE)
        self.assertEqual(calls[0][2:4], ('--platform', 'linux/amd64'))
        self.assertFalse(any('start' in c or 'run' in c for c in calls))

    def test_malformed_manifest_and_insecure_root_fail_closed(self):
        (self.output / build.MANIFEST).write_text('[]')
        with self.assertRaisesRegex(ValueError, 'object'):
            build.verify(self.output)
        self.save()
        self.output.chmod(0o777)
        with self.assertRaisesRegex(ValueError, 'mode0700'):
            build.verify(self.output)
        self.output.chmod(0o700)
        (self.art / 'pair/lib/glibc-hwcaps').mkdir()
        (self.art / 'pair/lib/glibc-hwcaps').chmod(0o755)
        with self.assertRaises((ValueError, build.selector.SelectionFailure)):
            build.verify(self.output)

    def test_valid_exact_reference_supports_classic_index_and_platform_ids(self):
        for kind in ('classic', 'index', 'platform'):
            with self.subTest(kind=kind):
                image = self.image(kind)
                identity = build.check_image([image], build.IMAGE)
                self.assertEqual(identity['image_id'], image['Id'])
                self.assertEqual(identity['repo_digests'], [build.IMAGE])
        self.assertNotEqual(build.check_image([self.image()])['image_id'], build.DIGEST)
        # Multiple repository associations can legitimately name the same image.
        image = self.image()
        image['RepoDigests'].append('example.invalid/alias@sha256:' + '3' * 64)
        self.assertIn(build.IMAGE, build.check_image([image])['repo_digests'])

    def test_wrong_reference_or_missing_pinned_repo_digest_fails_even_if_id_matches(self):
        with self.assertRaisesRegex(ValueError, 'reference'):
            build.check_image([self.image()], 'ghcr.io/blakeblackshear/frigate:0.18.2')
        for values in ([], None, 'not-a-list', ['other.invalid/image@' + build.DIGEST],
                       ['ghcr.io/blakeblackshear/frigate@sha256:' + '3' * 64]):
            image = self.image('index')
            image['RepoDigests'] = values
            with self.subTest(values=values), self.assertRaises(ValueError):
                build.check_image([image])

    def test_invalid_or_ambiguous_image_metadata_fails_closed(self):
        for document in (None, {}, [], [None], [1], [self.image(), self.image()]):
            with self.subTest(document=document), self.assertRaises(ValueError):
                build.check_image(document)
        for key, value in [('Id', None), ('Id', 'short-id'), ('Id', 1),
                           ('Os', 'windows'), ('Architecture', 'arm64'), ('Variant', 'v3'),
                           ('RepoDigests', [build.IMAGE, build.IMAGE]), ('RepoDigests', [1])]:
            image = self.image()
            image[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                build.check_image([image])

    def test_inconsistent_descriptor_and_platform_evidence_is_rejected(self):
        for change in ('id', 'digest', 'media', 'platform', 'missing-platform', 'index'):
            image = self.image('platform')
            if change == 'id':
                image['Id'] = 'sha256:' + '4' * 64
            elif change == 'digest':
                image['Descriptor']['digest'] = 'sha256:' + '4' * 64
            elif change == 'media':
                image['Descriptor']['mediaType'] = 'unknown'
            elif change == 'platform':
                image['Descriptor']['platform']['architecture'] = 'arm64'
            elif change == 'missing-platform':
                del image['Descriptor']['platform']
            elif change == 'index':
                image['Descriptor']['mediaType'] = 'application/vnd.oci.image.index.v1+json'
            with self.subTest(change=change), self.assertRaises(ValueError):
                build.check_image([image])
        image = self.image()
        image['Descriptor'] = []
        with self.assertRaises(ValueError):
            build.check_image([image])

    def test_manifest_identity_evidence_cannot_be_missing_or_reassociated(self):
        del self.record['frigate']['repo_digests']
        self.save()
        with self.assertRaisesRegex(ValueError, 'RepoDigests'):
            build.verify(self.output)
        self.record['frigate']['repo_digests'] = ['other.invalid/image@' + build.DIGEST]
        self.save()
        with self.assertRaisesRegex(ValueError, 'RepoDigest'):
            build.verify(self.output)

    def test_non_node20_preflight_does_not_compile_or_create_output(self):
        archive = self.base / 'archive-fixture'
        archive.write_bytes(b'fixture archive')
        commands = []
        def run(runner, *args, **kwargs):
            commands.append(args)
            if args[:3] == ('docker', 'image', 'inspect'):
                return json.dumps([self.image()])
            if args[:2] == ('node', '--version'):
                return 'v22.0.0'
            return 'fixture'
        output = self.base / 'not-created'
        with patch.object(build.Runner, 'run', run), \
                patch.object(build, 'reviewed_source', return_value={'fixture':'hash'}), \
                patch.object(build, 'ARCHIVE', build.digest(archive)):
            with self.assertRaisesRegex(ValueError, 'Node 20'):
                build.build(argparse.Namespace(pipeline=str(ROOT), source_commit='a'*40,
                                                source_archive=str(archive), output=str(output)))
        self.assertFalse(output.exists())
        self.assertFalse(any(c[:2] in [('docker','create'),('npm','install')] for c in commands))

    def test_mocked_source_build_connects_outputs_without_prebuilt_inputs(self):
        import tarfile
        upstream = self.base / ('frigate-' + build.COMMIT)
        (upstream / 'web/src').mkdir(parents=True)
        (upstream / 'web/patches').mkdir()
        (upstream / 'web/package-lock.json').write_text('{"lockfileVersion":3}')
        (upstream / 'web/src/main.tsx').write_text('upstream entry')
        (upstream / 'web/images/branding').mkdir(parents=True)
        for rel in ['LICENSE', 'TRADEMARK.md', 'web/images/branding/LICENSE']:
            (upstream / rel).write_text('upstream notice fixture')
        archive = self.base / 'official-fixture.tar.gz'
        with tarfile.open(archive, 'w:gz') as handle:
            handle.add(upstream, arcname=upstream.name)
        output = self.base / 'built'
        calls = []
        source_files = {'src/nanexus_trust/frigate/host_entry.py':
                        build.digest(ROOT / 'src/nanexus_trust/frigate/host_entry.py')}
        def run(runner, *args, cwd=None):
            calls.append(tuple(str(a) for a in args))
            runner.commands.append({'argv': list(calls[-1]), 'exit_code': 0})
            if args[:3] == ('docker', 'image', 'inspect'):
                return json.dumps([self.image()])
            if args[:2] == ('docker', 'create'):
                return 'c' * 64
            if args[:2] == ('docker', 'cp'):
                src = self.art / ('nginx' if 'nginx' in args[2] else 'api')
                shutil.copytree(src, args[3])
            if args[:3] == ('npm', 'run', 'build'):
                dist = Path(cwd) / 'dist'
                shutil.copytree(self.art / 'web', dist)
                monaco = dist / 'BASE_PATH/monacoeditorwork'
                monaco.mkdir(parents=True)
                for name in ['editor.worker.bundle.js', 'yaml.worker.bundle.js']:
                    shutil.move(str(dist / 'assets' / name), monaco / name)
            if len(args) > 1 and str(args[1]).endswith('build_runtime_pair.py'):
                shutil.copytree(self.art / 'pair', args[-1], symlinks=True)
            if args[:2] == ('node', '--version'):
                return 'v20.19.0'
            if args[0] in ('gcc', 'g++'):
                return build.pair.COMPILER_RELEASE
            if args[0] == 'pkg-config':
                return '3.0.0'
            return 'fixture-version'
        with patch.object(build.Runner, 'run', run), \
                patch.object(build, 'reviewed_source', return_value=source_files), \
                patch.object(build, 'ARCHIVE', build.digest(archive)), \
                patch.object(build.api_overlay, 'render_api_overlay', return_value={n:b.decode() for n,b in self.api.items()}), \
                patch.object(build.navigation_overlay, 'render_navigation_overlay', side_effect=lambda text, version:text), \
                patch.object(build.live_frontend_overlay, 'render_live_frontend', return_value={'web/src/main.tsx':'rendered entry'}):
            sha = build.build(argparse.Namespace(pipeline=str(ROOT), source_commit='a'*40,
                                                source_archive=str(archive), output=str(output)))
            self.assertEqual(build.verify(output), sha)
            manifest = build.load_json(output / build.MANIFEST)
            self.assertEqual(manifest['frigate']['image_id'], self.image()['Id'])
            self.assertNotEqual(manifest['frigate']['image_id'], build.DIGEST)
            self.assertEqual(manifest['frigate']['repo_digests'], [build.IMAGE])
            self.assertFalse((output / build.ACCEPTANCE).exists())
            self.assertEqual((output / 'artifacts/boot.py').read_text(), build.private.BOOT)
        self.assertIn(('npm', 'install'), calls)
        self.assertIn(('npm', 'run', 'build'), calls)
        self.assertTrue(any(c[0] == 'docker' and c[1] == 'cp' for c in calls))
        self.assertTrue(any('build_runtime_pair.py' in c[1] for c in calls if len(c)>1))
        self.assertFalse(any(c[0] == 'docker' and c[1] in ('build','run','start','commit') for c in calls))
        self.process.assert_not_called()


if __name__ == '__main__':
    unittest.main()
