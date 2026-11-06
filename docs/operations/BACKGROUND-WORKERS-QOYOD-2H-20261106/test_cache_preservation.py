"""Disposable filesystem/Git acceptance; no /app, root cache or network."""
import contextlib
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('cache_recovery', Path(__file__).with_name('RECOVER-YARN-CACHE.py'))
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


class PreservationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = tempfile.TemporaryDirectory(prefix='mezan-yarn-preservation-')
        self.addCleanup(self.fixture.cleanup)
        self.root = Path(self.fixture.name).resolve()
        self.source = self.root / 'cache'
        self.source.mkdir()
        (self.source / 'nested').mkdir()
        self.payload = b'preserve-all-bytes\x00\xff\x01' * 100
        (self.source / 'nested' / 'archive.bin').write_bytes(self.payload)
        self.backup = self.root / 'backup'
        self.backup.mkdir()
        self.unrelated = self.root / 'unrelated-source.txt'
        self.unrelated.write_text('keep user work')

    def run_recovery(self, **kwargs):
        return helper.preserve_cache(self.source, self.backup,
            allowed_source=self.source, before_remove=kwargs.pop('before_remove', lambda: None), **kwargs)

    def test_success_retains_exact_bytes_and_leaves_empty_reusable_cache(self):
        result = self.run_recovery()
        self.assertEqual((Path(result['backup']) / 'nested' / 'archive.bin').read_bytes(), self.payload)
        self.assertEqual(list(self.source.iterdir()), [])
        self.assertEqual(self.unrelated.read_text(), 'keep user work')
        self.assertTrue((self.backup / 'recovery.json').is_file())

    def test_failed_copy_never_removes_original(self):
        def fail(*args, **kwargs):
            raise OSError('synthetic copy failure')
        with self.assertRaisesRegex(OSError, 'copy failure'):
            self.run_recovery(copier=fail)
        self.assertEqual((self.source / 'nested' / 'archive.bin').read_bytes(), self.payload)

    def test_corrupt_backup_is_refused_and_original_is_retained(self):
        def corrupt(source, destination, **kwargs):
            shutil.copytree(source, destination, **kwargs)
            (destination / 'nested' / 'archive.bin').write_bytes(b'corrupt')
        with self.assertRaisesRegex(RuntimeError, 'original was retained'):
            self.run_recovery(copier=corrupt)
        self.assertEqual((self.source / 'nested' / 'archive.bin').read_bytes(), self.payload)

    def test_concurrent_source_change_is_not_evicted(self):
        def change(source, destination, **kwargs):
            shutil.copytree(source, destination, **kwargs)
            (source / 'new-write.bin').write_bytes(b'new cache writer')
        with self.assertRaisesRegex(RuntimeError, 'original was retained'):
            self.run_recovery(copier=change)
        self.assertEqual((self.source / 'new-write.bin').read_bytes(), b'new cache writer')

    def test_new_release_lease_refuses_eviction_after_copy(self):
        def active():
            raise RuntimeError('new release lease')
        with self.assertRaisesRegex(RuntimeError, 'release lease'):
            self.run_recovery(before_remove=active)
        self.assertEqual((self.source / 'nested' / 'archive.bin').read_bytes(), self.payload)
        self.assertEqual((self.backup / 'yarn' / 'nested' / 'archive.bin').read_bytes(), self.payload)

    def test_wrong_source_and_backup_inside_cache_are_refused(self):
        with self.assertRaisesRegex(RuntimeError, 'permitted directory'):
            helper.preserve_cache(self.source, self.backup, allowed_source=self.root / 'other', before_remove=lambda: None)
        inner = self.source / 'inner'
        inner.mkdir()
        with self.assertRaisesRegex(RuntimeError, 'outside the cache'):
            helper.preserve_cache(self.source, inner, allowed_source=self.source, before_remove=lambda: None)
        self.assertEqual((self.source / 'nested' / 'archive.bin').read_bytes(), self.payload)


class ReadOnlyWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.fixture = tempfile.TemporaryDirectory(prefix='mezan-index-preservation-')
        self.addCleanup(self.fixture.cleanup)
        self.root = Path(self.fixture.name).resolve()
        self.git('init', '-q')
        self.git('config', 'core.autocrlf', 'false')
        self.file = self.root / 'tracked.txt'
        self.file.write_bytes(b'reviewed source\n')
        self.git('add', 'tracked.txt')
        self.git('-c', 'user.name=Isolated fixture', '-c', 'user.email=test@example.invalid',
                 'commit', '-qm', 'fixture')
        self.head = self.git('rev-parse', 'HEAD').strip()
        scripts = self.root / 'scripts'
        scripts.mkdir()
        (scripts / 'production_release_guard.py').write_text(
            'print(\'{"active": false}\')\n', encoding='utf-8')

    def git(self, *args, **kwargs):
        return subprocess.check_output(['git', *args], cwd=self.root, text=True, **kwargs)

    def check_workspace(self):
        with contextlib.chdir(self.root), patch.object(helper, 'DEPLOYMENT', self.head):
            helper.require_workspace()

    def touch_source(self):
        info = self.file.stat()
        os.utime(self.file, ns=(info.st_atime_ns, info.st_mtime_ns + 10_000_000_000))

    def test_clean_stat_refresh_is_read_only_and_ignores_existing_index_lock(self):
        index = self.root / '.git' / 'index'
        self.touch_source()
        before = index.read_bytes()
        # The old command really refreshes the index after a stat-only change.
        with patch.dict(os.environ, {'GIT_OPTIONAL_LOCKS': '1'}):
            self.git('diff', '--quiet')
        self.assertNotEqual(index.read_bytes(), before)
        self.touch_source()
        before = index.read_bytes()
        lock = self.root / '.git' / 'index.lock'
        lock.write_bytes(b'leave another operation lock intact')
        self.check_workspace()
        self.assertEqual(index.read_bytes(), before)
        self.assertEqual(lock.read_bytes(), b'leave another operation lock intact')
        lock.unlink()  # Only this fixture's explicitly created lock.
        self.touch_source()
        before = index.read_bytes()
        self.check_workspace()
        self.assertEqual(index.read_bytes(), before)

    def test_read_only_check_still_refuses_unstaged_source_edits(self):
        self.file.write_bytes(b'changed source\n')
        before = (self.root / '.git' / 'index').read_bytes()
        with self.assertRaises(subprocess.CalledProcessError):
            self.check_workspace()
        self.assertEqual((self.root / '.git' / 'index').read_bytes(), before)

    def test_read_only_check_still_refuses_staged_source_edits(self):
        self.file.write_bytes(b'changed staged source\n')
        self.git('add', 'tracked.txt')
        before = (self.root / '.git' / 'index').read_bytes()
        with self.assertRaises(subprocess.CalledProcessError):
            self.check_workspace()
        self.assertEqual((self.root / '.git' / 'index').read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
