"""Synthetic filesystem acceptance only; no /app, root cache, Git or network."""
import importlib.util
from pathlib import Path
import shutil
import tempfile
import unittest

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


if __name__ == '__main__':
    unittest.main()
