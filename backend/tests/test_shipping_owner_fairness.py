"""Owner fairness on isolated Mongo; inherited fixture uses GET-only MockTransport."""
import asyncio
from copy import deepcopy
import json
import os
from pathlib import Path
from time import perf_counter
from datetime import timedelta
from unittest.mock import patch
import unittest

from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import ExecutionTimeout

import assembly_completion_delivery as delivery
import test_shipping_reconciliation_load as load


class OwnerFairnessTests(unittest.IsolatedAsyncioTestCase):
    ORDERS, OWNERS = 2000, 100
    asyncSetUp = load.ReconciliationLoadTests.asyncSetUp
    asyncTearDown = load.ReconciliationLoadTests.asyncTearDown
    install = load.ReconciliationLoadTests.install
    provider = load.ReconciliationLoadTests.provider
    seed = load.ReconciliationLoadTests.seed
    business_hash = load.ReconciliationLoadTests.business_hash
    explain_candidates = load.ReconciliationLoadTests.explain_candidates

    def record_adversarial(self, scenario, result):
        path = os.environ.get("SHIPPING_OWNER_FAIRNESS_REPORT")
        if path:
            output = Path(path)
            output.parent.mkdir(parents=True, exist_ok=True)
            report = json.loads(output.read_text()) if output.exists() else {}
            report[scenario] = result
            output.write_text(json.dumps(report, indent=2) + "\n")

    async def test_ten_thousand_ineligible_rows_do_not_hide_due_owners(self):
        await self.seed(clustered=True)
        template = await self.db[delivery.WORKFLOWS].find_one({"order_number": "load-0000"})
        template.pop("_id")
        rows = []
        future = (self.clock + timedelta(days=1)).isoformat()
        for index in range(10000):
            row = deepcopy(template)
            row.update(user_id=f"ahead-ineligible-{index // 100:03}", order_number=f"ineligible-{index:05}")
            operation = row[delivery.FIELD]
            kind = index % 4
            if kind == 0:
                operation["state"] = "confirmed"
            elif kind == 1:
                operation["due_at"] = future
            elif kind == 2:
                operation["read_attempts"] = delivery.MAX_READ_ATTEMPTS
            else:
                operation["lease_until"] = future
            rows.append(row)
        await self.db[delivery.WORKFLOWS].insert_many(rows)
        start = perf_counter()
        self.assertEqual(await delivery.run_once(self.db), 1)
        elapsed_ms = (perf_counter() - start) * 1000
        self.assertEqual(self.calls[0]["owner"], "owner-000")
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(await self.db[delivery.WORKFLOWS].count_documents({
            "order_number": {"$regex": "^ineligible-"},
            f"{delivery.FIELD}.claim": {"$ne": None}}), 0)
        explain = await self.explain_candidates()
        self.assertEqual(explain["nReturned"], 1)
        self.assertNotIn("COLLSCAN", json.dumps(explain["winning_plan"]))
        self.assertEqual(await self.business_hash(), self.business_before)
        self.record_adversarial("ineligible_heavy", {
            "eligible_orders": 2000, "eligible_owners": 100, "ineligible_orders": 10000,
            "ineligible_mix": {"confirmed": 2500, "future_due": 2500,
                "exhausted": 2500, "unexpired_lease": 2500},
            "useful_jobs": 1, "provider_gets": 2, "provider_posts": 0,
            "elapsed_ms": round(elapsed_ms, 3), "query_execution": explain,
            "protected_business_unchanged": True,
            "limitation": "One first eligible job measured; this does not prove all 2000 orders complete or a production DB latency bound."})

    async def test_candidate_query_timeout_preserves_cursor_then_recovers(self):
        await self.seed(clustered=True)
        await self.db[delivery.LIMITS].insert_one({"_id": "global", "available_at": "",
            "claim": None, "owner_cursor": "owner-020", "round_due_before": self.clock.isoformat()})
        collection_type = type(self.db[delivery.WORKFLOWS])
        original = collection_type.find_one
        observed_limits = []
        async def timed_out(collection, *args, **kwargs):
            if collection.name == delivery.WORKFLOWS and kwargs.get("sort"):
                observed_limits.append(kwargs.get("max_time_ms"))
                raise ExecutionTimeout("Synthetic bounded queue timeout", code=50)
            return await original(collection, *args, **kwargs)
        with patch.object(collection_type, "find_one", new=timed_out):
            with self.assertRaises(ExecutionTimeout):
                await delivery.run_once(self.db)
        self.assertEqual(observed_limits, [delivery.QUEUE_QUERY_TIMEOUT_MS])
        self.assertEqual(self.calls, [])
        scheduler = await self.db[delivery.LIMITS].find_one({"_id": "global"})
        self.assertEqual(scheduler["owner_cursor"], "owner-020")
        self.assertEqual(scheduler["round_due_before"], self.clock.isoformat())
        self.assertIsNone(scheduler["claim"])
        self.assertEqual(await delivery.run_once(self.db), 0)
        self.clock += timedelta(seconds=delivery.GLOBAL_READ_INTERVAL)
        self.assertEqual(await delivery.run_once(self.db), 1)
        self.assertEqual(self.calls[0]["owner"], "owner-021")
        self.assertEqual(await self.business_hash(), self.business_before)
        self.record_adversarial("candidate_timeout", {
            "injection": "Motor candidate find_one raises server ExecutionTimeout code 50; not a server CPU saturation measurement",
            "configured_server_max_time_ms": observed_limits[0],
            "timeout_provider_gets": 0, "timeout_provider_posts": 0,
            "cursor_preserved": "owner-020", "cooldown_blocks_immediate_retry": True,
            "recovery_owner": "owner-021", "protected_business_unchanged": True,
            "limitation": "Repeated infrastructure timeouts safely defer work; recovery requires DB availability, not guaranteed throughput."})

    async def test_bounded_cooling_owner_scan_persists_progress(self):
        await self.seed(clustered=True)
        future = (self.clock + timedelta(hours=1)).isoformat()
        await self.db[delivery.LIMITS].insert_many([
            {"_id": f"owner:owner-{index:03}", "available_at": future, "claim": None}
            for index in range(20)])
        self.assertEqual(await delivery.run_once(self.db), 0)
        scheduler = await self.db[delivery.LIMITS].find_one({"_id": "global"})
        self.assertEqual(scheduler["owner_cursor"], "owner-015")
        self.assertEqual(len(self.monitor.candidates), 16)
        self.assertEqual(self.calls, [])
        self.clock += timedelta(seconds=delivery.GLOBAL_READ_INTERVAL)
        self.assertEqual(await delivery.run_once(self.db), 1)
        self.assertEqual(self.calls[0]["owner"], "owner-020")
        self.assertEqual(len(self.monitor.candidates), 21)
        self.assertEqual(await self.business_hash(), self.business_before)

    async def test_new_arrival_waits_for_next_round(self):
        await self.seed(clustered=True)
        self.assertEqual(await delivery.run_once(self.db), 1)
        self.clock += timedelta(seconds=delivery.GLOBAL_READ_INTERVAL)
        # New work lexically before the next old owner cannot jump this round.
        row = await self.db[delivery.WORKFLOWS].find_one({"order_number": "load-1999"})
        row.pop("_id")
        row.update(user_id="owner-000-new", order_number="new-arrival")
        row[delivery.FIELD]["due_at"] = self.clock.isoformat()
        await self.db[delivery.WORKFLOWS].insert_one(row)
        source = await self.db.unified_orders.find_one({"order_number": "load-1999"})
        source.pop("_id")
        source.update(user_id="owner-000-new", order_number="new-arrival")
        await self.db.unified_orders.insert_one(source)
        self.assertEqual(await delivery.run_once(self.db), 1)
        self.assertEqual(self.calls[-1]["owner"], "owner-001")
        self.assertEqual((await self.db[delivery.WORKFLOWS].find_one({
            "order_number": "new-arrival"}))[delivery.FIELD]["read_attempts"], 0)

    async def test_restart_retains_owner_rotation(self):
        await self.seed(clustered=True)
        self.assertEqual(await delivery.run_once(self.db), 1)
        self.clock += timedelta(seconds=delivery.GLOBAL_READ_INTERVAL)
        import os
        restarted = AsyncIOMotorClient(os.environ["MZ2_TEST_MONGO_URI"], serverSelectionTimeoutMS=3000)
        try:
            self.assertEqual(await delivery.run_once(restarted[self.db.name]), 1)
        finally:
            restarted.close()
        self.assertEqual(self.calls[-1]["owner"], "owner-001")
        self.assertEqual(await self.business_hash(), self.business_before)

    async def test_lost_global_lease_cannot_advance_cursor_or_launch(self):
        await self.seed(clustered=True)
        original = delivery._advance_owner_cursor
        async def superseded(db, token, owner, cutoff):
            await db[delivery.LIMITS].update_one({"_id": "global"}, {"$set": {
                "claim": "successor", "owner_cursor": "owner-050",
                "available_at": (self.clock + timedelta(seconds=120)).isoformat()}})
            return await original(db, token, owner, cutoff)
        with patch.object(delivery, "_advance_owner_cursor", side_effect=superseded):
            self.assertEqual(await delivery.run_once(self.db), 0)
        scheduler = await self.db[delivery.LIMITS].find_one({"_id": "global"})
        self.assertEqual(scheduler["claim"], "successor")
        self.assertEqual(scheduler["owner_cursor"], "owner-050")
        self.assertEqual(self.calls, [])

    async def test_global_expiry_while_acquiring_owner_prevents_launch(self):
        await self.seed(clustered=True)
        original = delivery._take_read_slot
        async def expire_after_owner(db, key):
            token = await original(db, key)
            if key.startswith("owner:"):
                await db[delivery.LIMITS].update_one({"_id": "global"}, {"$set": {
                    "available_at": (self.clock - timedelta(seconds=1)).isoformat()}})
            return token
        with patch.object(delivery, "_take_read_slot", side_effect=expire_after_owner):
            self.assertEqual(await delivery.run_once(self.db), 0)
        self.assertEqual(self.calls, [])
        self.assertEqual(await self.db[delivery.WORKFLOWS].count_documents({
            f"{delivery.FIELD}.read_attempts": {"$gt": 0}}), 0)

    async def test_cancelled_scheduler_retains_progress_without_provider_io(self):
        await self.seed(clustered=True)
        original = delivery._advance_owner_cursor
        async def cancelled(db, token, owner, cutoff):
            result = await original(db, token, owner, cutoff)
            if result:
                raise asyncio.CancelledError()
            return result
        with patch.object(delivery, "_advance_owner_cursor", side_effect=cancelled):
            with self.assertRaises(asyncio.CancelledError):
                await delivery.run_once(self.db)
        self.assertEqual(self.calls, [])
        self.clock += timedelta(seconds=delivery.GLOBAL_READ_INTERVAL)
        self.assertEqual(await delivery.run_once(self.db), 1)
        self.assertEqual(self.calls[-1]["owner"], "owner-001")

    async def test_exhausted_oldest_order_does_not_hide_same_owner_work(self):
        await self.seed(clustered=True)
        await self.db[delivery.WORKFLOWS].update_one({"order_number": "load-0000"}, {"$set": {
            f"{delivery.FIELD}.state": "pending", f"{delivery.FIELD}.read_attempts": delivery.MAX_READ_ATTEMPTS}})
        self.assertEqual(await delivery.run_once(self.db), 1)
        exhausted = await self.db[delivery.WORKFLOWS].find_one({"order_number": "load-0000"})
        next_order = await self.db[delivery.WORKFLOWS].find_one({"order_number": "load-0001"})
        self.assertEqual(exhausted[delivery.FIELD]["state"], "requires_attention")
        self.assertEqual(exhausted[delivery.FIELD]["error_code"], "completion_read_budget_exhausted")
        self.assertEqual(exhausted[delivery.FIELD]["read_attempts"], delivery.MAX_READ_ATTEMPTS)
        self.assertEqual(next_order[delivery.FIELD]["read_attempts"], 1)
        self.assertEqual(await self.business_hash(), self.business_before)
