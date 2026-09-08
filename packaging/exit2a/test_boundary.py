"""No application startup, DB connection or provider call in these unit tests."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from rehearsal import SYNTHETIC_ENV, validate_environment


class BoundaryTests(unittest.TestCase):
    def test_only_exact_synthetic_configuration_is_accepted(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            validate_environment(dict(SYNTHETIC_ENV), root)
            for key, value in [('MONGO_URL', 'mongodb://localhost:27017'), ('DB_NAME', 'production'), ('JWT_SECRET', 'other'), ('SALLA_CLIENT_SECRET', 'synthetic-injected')]:
                with self.subTest(key=key), self.assertRaises(RuntimeError):
                    validate_environment({**SYNTHETIC_ENV, key: value}, root)
            (root / '.env').write_text('# test only')
            with self.assertRaises(RuntimeError):
                validate_environment(dict(SYNTHETIC_ENV), root)

    def test_import_index_is_skipped_only_for_explicit_rehearsal(self):
        from bank_transfer_review_routes import make_bank_transfer_review_router
        class IndexRecorder:
            def __init__(self): self.calls = []
            def create_index(self, *args, **kwargs): self.calls.append((args, kwargs))
        for flag in (None, '0', 'true', '1'):
            c = IndexRecorder()
            env = {} if flag is None else {'MEZAN_EXIT2A_REHEARSAL': flag}
            with self.subTest(flag=flag), patch.dict(os.environ, env, clear=True):
                router = make_bank_transfer_review_router(SimpleNamespace(bank_transfer_reviews=c), lambda: None)
                self.assertTrue(router.routes)
                self.assertEqual(len(c.calls), 0 if flag == '1' else 1)
                if c.calls:
                    self.assertEqual(c.calls[0][1]['name'], 'uniq_review_source_target')
                    self.assertTrue(c.calls[0][1]['unique'])


if __name__ == '__main__':
    sys.path.insert(0, '/opt/mezan/backend')
    unittest.main()
