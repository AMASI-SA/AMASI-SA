"""Hosting boundary contracts, without a database or provider connection."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
if Path('/opt/mezan/backend').is_dir():
    ROOT = Path('/opt/mezan')
spec = importlib.util.spec_from_file_location('independent_staging', ROOT / 'backend/independent_staging.py')
staging = importlib.util.module_from_spec(spec)
spec.loader.exec_module(staging)


class HostingBoundary(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'source.py').write_text('trusted source')
        self.addCleanup(patch.stopall)
        patch.object(staging, 'ROOT', self.root).start()
        staging.write_manifest('a' * 40)
        self.env = {'APP_ENV': 'staging', 'MEZAN_SYNTHETIC_HOSTING': '1',
            'DB_NAME': 'mezan_exit2d_staging', 'MONGO_URL': 'mongodb://u:p@staging-mongo:27017',
            'MEZAN_STAGING_MONGO_HOST': 'staging-mongo'}

    def test_source_identity_and_synthetic_database(self):
        self.assertEqual(staging.validate(self.env), 'staging:' + 'a'*40)

    def test_foreign_database_and_provider_credentials_rejected(self):
        for update in ({'DB_NAME': 'production'}, {'QOYOD_API_KEY': 'example'},
                       {'SALLA_TOKEN_ENC_KEY': 'example'}, {'APP_ENV': 'production'},
                       {'MONGO_URL': 'mongodb://u:p@other-host:27017'},
                       {'MONGO_URL': 'mongodb://u:p@staging-mongo:27017/?authSource=other'},
                       {'MONGO_URL': 'mongodb://staging-mongo:27017'}):
            with self.subTest(update=list(update)):
                with self.assertRaises(RuntimeError):
                    staging.validate({**self.env, **update})

    def test_changed_or_added_source_rejected(self):
        (self.root / 'source.py').write_text('changed')
        with self.assertRaises(RuntimeError): staging.validate(self.env)
        (self.root / 'source.py').write_text('trusted source')
        (self.root / 'extra.py').write_text('unexpected')
        with self.assertRaises(RuntimeError): staging.validate(self.env)

    def test_manifest_cannot_claim_production(self):
        manifest = self.root / 'independent-staging-manifest.json'
        content = json.loads(manifest.read_text())
        content['kind'] = 'production'
        manifest.write_text(json.dumps(content))
        with self.assertRaises(RuntimeError): staging.validate(self.env)

    def test_unknown_source_rejected(self):
        with self.assertRaises(ValueError): staging.write_manifest('unknown')


if __name__ == '__main__': unittest.main()
