"""Shipping delegates to canonical Track A without granting write authority."""
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

    async def test_canonical_identity_and_transaction_scope_are_preserved(self):
        from accounting_atomic import SessionDatabase
        from test_financial_ledger_identity import DB, account
        raw = DB([account(), account("cash")])
        session = object()
        scoped = SessionDatabase(raw, session)
        scoped._owner = "owner"
        for kind in ("bank", "cash"):
            identity = await require_shipping_bank_identity(scoped, "owner", "canonical-" + kind)
            self.assertEqual((identity["entity_type"], identity["entity_id"], identity["sub_account"]),
                             ("bank", "canonical-" + kind, "main"))
            self.assertNotIn("can_write", identity)
        self.assertTrue(all(kwargs == {"session": session} for _, kwargs in raw.calls))

    async def test_invalid_identity_remains_closed_even_with_environment_flags(self):
        from test_financial_ledger_identity import DB, account
        for changes in ({"id": "other"}, {"status": "inactive"}, {"user_id": "other"},
                        {"currency": "USD"}, {"archived": True}, {"account_type": "ad_payable"}):
            with self.subTest(changes=changes), patch.dict("os.environ", {
                "MZ2_SHIPPING_BANK_PORT_ENABLED": "true", "P02_SHIPPING_COD_ENABLED": "true",
            }):
                with self.assertRaises(HTTPException) as failure:
                    await require_shipping_bank_identity(DB([account(**changes)]), "owner", "canonical-bank")
                self.assertEqual(failure.exception.status_code, 409)
                self.assertEqual(failure.exception.detail, {"code": "MZ2_LINK_REQUIRED"})
