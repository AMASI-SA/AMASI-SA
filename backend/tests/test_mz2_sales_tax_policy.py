import copy
import unittest
from types import SimpleNamespace
from accounting_sales_tax import TaxError
from accounting_sales_tax_service import read_policy, save_policy, sale_snapshot


class Collection:
    def __init__(self):
        self.rows = {}
        self.writes = 0

    async def find_one(self, query):
        return copy.deepcopy(self.rows.get(query["_id"]))

    async def insert_one(self, record):
        self.rows[record["_id"]] = copy.deepcopy(record)
        self.writes += 1

    async def replace_one(self, query, record):
        matched = self.rows[query["_id"]]["revision"] == query["revision"]
        if matched:
            self.rows[query["_id"]] = copy.deepcopy(record)
            self.writes += 1
        return SimpleNamespace(matched_count=int(matched))


class PolicyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = SimpleNamespace(mz2_sales_tax_policies=Collection())

    async def save(self, **overrides):
        return await save_policy(self.db, **dict(
            owner="owner", actor_id="accountant", rate="15",
            effective_at="2026-09-19T00:00:00Z", revision=0, reason="Test decision",
        ) | overrides)

    async def test_missing_read_does_not_write(self):
        policy = await read_policy(self.db, "owner")
        self.assertEqual(policy["versions"], [])
        self.assertEqual(self.db.mz2_sales_tax_policies.writes, 0)

    async def test_audit_and_versions_written_together(self):
        first = await self.save()
        second = await self.save(rate="0", revision=1)
        self.assertEqual(len(second["audit"]), 2)
        self.assertEqual(second["versions"][0], first["versions"][0])
        self.assertEqual(second["versions"][1]["rate"], "0")
        self.assertEqual(second["audit"][1]["actor_id"], "accountant")
        self.assertEqual(self.db.mz2_sales_tax_policies.writes, 2)

    async def test_invalid_or_stale_update_no_write(self):
        await self.save()
        for change in ({"revision": 0}, {"revision": 1, "rate": None},
                       {"revision": 1, "effective_at": "2026-09-19"},
                       {"revision": 1, "reason": ""}):
            with self.subTest(change=change), self.assertRaises(TaxError):
                await self.save(**change)
        self.assertEqual(self.db.mz2_sales_tax_policies.writes, 1)

    async def test_owner_isolation(self):
        await self.save()
        self.assertEqual((await read_policy(self.db, "other"))["versions"], [])

    async def test_source_eight_percent_is_retained_not_applied(self):
        policy = await self.save()
        snapshot = sale_snapshot(policy,
            {"amount": "115", "recognized_at": "2026-09-19T12:00:00Z"},
            {"tax_percent": "8", "tax_amount": "8.52"})
        self.assertEqual(snapshot["net"], "100.00")
        self.assertEqual(snapshot["tax"], "15.00")
        self.assertEqual(snapshot["source_tax_for_review"]["tax_percent"], "8")
        await self.save(rate="20", revision=1)
        self.assertEqual(snapshot["rate"], "15")
        self.assertEqual(snapshot["tax"], "15.00")


if __name__ == "__main__":
    unittest.main()
