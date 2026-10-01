"""A missing Track A integration cannot resolve a bank or touch storage."""
import unittest
import subprocess
import sys
from unittest.mock import patch

from fastapi import HTTPException

from accounting_shipping_bank_port import require_shipping_bank_identity


class ForbiddenDatabase:
    def __getattr__(self, name):
        raise AssertionError(f"Unexpected database access: {name}")

    def __getitem__(self, name):
        raise AssertionError(f"Unexpected collection access: {name}")


class ShippingBankPortTests(unittest.IsolatedAsyncioTestCase):
    def test_accounting_router_does_not_eagerly_load_order_engine(self):
        result = subprocess.run([sys.executable, "-c", """
import importlib.abc, sys
class BlockOrderEngine(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'order_engine' or fullname.startswith('order_engine.'):
            raise AssertionError('Accounting setup must not initialize Order Engine routes')
sys.meta_path.insert(0, BlockOrderEngine())
from financial_provider_apps import make_financial_provider_apps_router
async def actor(): return {'id': 'unused'}
router = make_financial_provider_apps_router(object(), actor)
assert any(route.path.endswith('/shipping-v2/couriers') for route in router.routes)
"""], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)

    async def test_all_identities_fail_closed_without_storage_access(self):
        for account_id in ("canonical-bank", "canonical-cash", "legacy-bank", "", None):
            with self.subTest(account_id=account_id):
                with self.assertRaises(HTTPException) as failure:
                    await require_shipping_bank_identity(ForbiddenDatabase(), "owner", account_id)
                self.assertEqual(failure.exception.status_code, 503)
                self.assertEqual(failure.exception.detail, {
                    "code": "mz2_shipping_bank_port_not_integrated",
                })

    async def test_environment_cannot_enable_the_unintegrated_port(self):
        with patch.dict("os.environ", {
            "MZ2_SHIPPING_BANK_PORT_ENABLED": "true",
            "P02_SHIPPING_COD_ENABLED": "true",
        }):
            with self.assertRaises(HTTPException) as failure:
                await require_shipping_bank_identity(ForbiddenDatabase(), "owner", "bank")
        self.assertEqual(failure.exception.status_code, 503)
