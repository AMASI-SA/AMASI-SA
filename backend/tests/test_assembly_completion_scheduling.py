"""Slow provider IO must not hold newly completed orders behind a batch."""
import asyncio
import os
import unittest
from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

from motor.motor_asyncio import AsyncIOMotorClient

import assembly_completion_delivery as delivery


class CompletionSchedulingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        uri = os.environ.get("MZ2_TEST_MONGO_URI", "")
        if not uri:
            self.skipTest("MZ2_TEST_MONGO_URI required; no production fallback")
        self.assertTrue(uri.startswith("mongodb://127.0.0.1:"))
        self.mongo = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000)
        self.db = self.mongo["assembly_scheduling_" + uuid4().hex]
        self.addCleanup(self.mongo.close)
        self.addAsyncCleanup(self.mongo.drop_database, self.db.name)
        self.real_sleep = asyncio.sleep

    async def seed(self, number, *, attempts=0):
        await self.db[delivery.WORKFLOWS].insert_one({
            "user_id": "synthetic-owner", "order_number": number,
            "assembly_status": "completed", delivery.FIELD: {
                "version": 2, "state": "pending", "attempts": attempts,
                "status_attempted": attempts > 0,
                "due_at": (delivery.now() - timedelta(seconds=10)).isoformat(),
                "lease_until": "",
            },
        })

    async def poll_sleep(self, delay):
        await self.real_sleep(min(delay, 0.01))

    async def stop(self, worker):
        worker.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await worker

    async def test_new_completion_starts_while_older_provider_read_is_blocked(self):
        await self.seed("older")
        older_started, new_started, release = (asyncio.Event() for _ in range(3))

        async def resume(db, *, user_id, order_number):
            if order_number == "older":
                older_started.set()
                await release.wait()
            else:
                new_started.set()
            await db[delivery.WORKFLOWS].update_one(
                {"order_number": order_number}, {"$set": {f"{delivery.FIELD}.state": "confirmed"}})

        with patch.object(delivery, "resume", resume), patch.object(delivery.asyncio, "sleep", self.poll_sleep):
            worker = asyncio.create_task(delivery.loop(self.db))
            try:
                await asyncio.wait_for(older_started.wait(), 2)
                await self.seed("newly-ready")
                await asyncio.wait_for(new_started.wait(), 2)
                self.assertFalse(release.is_set(), "New completion must not await the older provider")
            finally:
                await self.stop(worker)

    async def test_concurrency_is_bounded_and_shutdown_joins_provider_tasks(self):
        for index in range(8):
            await self.seed(f"synthetic-{index}")
        four_started, release = asyncio.Event(), asyncio.Event()
        active, peak, cancelled = set(), 0, set()

        async def resume(db, *, user_id, order_number):
            nonlocal peak
            active.add(order_number)
            peak = max(peak, len(active))
            if len(active) == 4:
                four_started.set()
            try:
                await release.wait()
            finally:
                active.remove(order_number)
                cancelled.add(order_number)

        with patch.object(delivery, "resume", resume), patch.object(delivery.asyncio, "sleep", self.poll_sleep):
            worker = asyncio.create_task(delivery.loop(self.db))
            try:
                await asyncio.wait_for(four_started.wait(), 2)
                await self.real_sleep(0.05)
                self.assertEqual(peak, 4)
                self.assertEqual(len(active), 4)
            finally:
                await self.stop(worker)
            self.assertFalse(active)
            self.assertEqual(len(cancelled), 4)

    async def test_first_attempt_precedes_older_label_retries(self):
        for index in range(4):
            await self.seed(f"retry-{index}", attempts=3)
        await self.seed("newly-ready")
        calls = []

        async def resume(db, *, user_id, order_number):
            calls.append(order_number)

        with patch.object(delivery, "resume", resume):
            self.assertEqual(await delivery.run_once(self.db), 4)
        self.assertIn("newly-ready", calls)

    async def test_last_piece_wakeup_dispatches_without_waiting_for_poll_interval(self):
        first_poll, dispatched, wake = (asyncio.Event() for _ in range(3))
        original = delivery.run_once

        async def observed_poll(*args, **kwargs):
            result = await original(*args, **kwargs)
            first_poll.set()
            return result

        async def resume(db, *, user_id, order_number):
            dispatched.set()

        with patch.object(delivery, "run_once", observed_poll), patch.object(delivery, "resume", resume):
            worker = asyncio.create_task(delivery.loop(self.db, wake_event=wake))
            try:
                await asyncio.wait_for(first_poll.wait(), 2)
                await self.seed("newly-ready")
                wake.set()
                await asyncio.wait_for(dispatched.wait(), 1)
            finally:
                if worker.done() and worker.exception() is not None:
                    await worker
                else:
                    await self.stop(worker)

    async def test_slow_label_retries_leave_capacity_for_a_new_completion(self):
        for index in range(4):
            await self.seed(f"retry-{index}", attempts=3)
        retries_started, dispatched, release = (asyncio.Event() for _ in range(3))
        started = set()

        async def resume(db, *, user_id, order_number):
            if order_number == "newly-ready":
                dispatched.set()
                return
            started.add(order_number)
            if len(started) == 3:
                retries_started.set()
            await release.wait()

        with patch.object(delivery, "resume", resume), patch.object(delivery.asyncio, "sleep", self.poll_sleep):
            worker = asyncio.create_task(delivery.loop(self.db))
            try:
                await asyncio.wait_for(retries_started.wait(), 2)
                await self.seed("newly-ready")
                await asyncio.wait_for(dispatched.wait(), 1)
                self.assertEqual(len(started), 3)
            finally:
                await self.stop(worker)


if __name__ == "__main__":
    unittest.main()
