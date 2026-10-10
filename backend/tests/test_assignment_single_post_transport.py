"""Real loopback HTTP transport plus isolated Mongo; no Salla network access."""
import asyncio
import json
import os
import unittest
from unittest.mock import AsyncMock, patch

import httpx
import preparation_piece_operations as pieces
import salla_integration.service as salla
import test_assignment_salla_in_progress as assignment_fixture


class Provider:
    async def start(self):
        self.calls = []
        self.responses = [200]
        self.status = "pending_review"
        self.failure = None
        self.readback_failure = False
        self.handlers = set()
        self.server = await asyncio.start_server(self.handle, "127.0.0.1", 0)
        self.url = "http://127.0.0.1:%s" % self.server.sockets[0].getsockname()[1]
        self.token = AsyncMock(side_effect=lambda *a, **kw: "refreshed" if kw.get("force_refresh") else "initial")
        self.patches = [
            patch.object(salla, "SALLA_API_BASE", self.url),
            patch.object(salla, "ensure_fresh_access_token", self.token),
            patch.object(salla, "get_integration", AsyncMock(return_value={"synthetic": True})),
            patch.object(salla, "_decrypt_access", return_value="newest"),
            patch.dict(os.environ, {"NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1"}),
        ]
        for item in self.patches:
            item.start()

    async def close(self):
        self.server.close()
        await self.server.wait_closed()
        if self.handlers:
            await asyncio.gather(*self.handlers, return_exceptions=True)
        for item in reversed(self.patches):
            item.stop()

    @property
    def posts(self):
        return sum(method == "POST" for method, path in self.calls)

    async def handle(self, reader, writer):
        task = asyncio.current_task()
        self.handlers.add(task)
        try:
            header = await reader.readuntil(b"\r\n\r\n")
            lines = header.decode().split("\r\n")
            method, path, _ = lines[0].split()
            length = next((int(line.split(":", 1)[1]) for line in lines[1:]
                           if line.lower().startswith("content-length:")), 0)
            await reader.readexactly(length)
            self.calls.append((method, path))
            status = 200
            headers = ""
            if path == "/store/info":
                status, body = 401, {"message": "expired"}
            elif path == "/orders/statuses":
                body = {"data": [{"id": 7, "name": "قيد التنفيذ"}]}
            elif method == "POST" or path == "/transport-probe":
                # The entire POST body has reached the server before failure.
                if self.failure == "timeout":
                    await asyncio.sleep(.3)
                    return
                if self.failure == "disconnect":
                    return
                status = self.responses.pop(0) if self.responses else 200
                if 300 <= status < 400:
                    headers = "Location: /redirect-target\r\n"
                if status == 200:
                    self.status = "in_progress"
                body = {"message": "expired" if status == 401 else "ok"}
            else:
                status = 503 if self.readback_failure and self.posts else 200
                body = {"data": {"status": {"slug": self.status}}}
            payload = json.dumps(body).encode()
            writer.write((f"HTTP/1.1 {status} Synthetic\r\nContent-Type: application/json\r\n"
                          f"Content-Length: {len(payload)}\r\nConnection: close\r\n{headers}\r\n").encode() + payload)
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()
            self.handlers.discard(task)


class SinglePostTransportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.provider = Provider()
        await self.provider.start()

    async def asyncTearDown(self):
        await self.provider.close()

    async def test_401_sequences_never_replay_single_attempt(self):
        for sequence in ([401, 200], [401, 401, 200]):
            with self.subTest(sequence=sequence):
                self.provider.calls.clear()
                self.provider.token.reset_mock()
                self.provider.responses = list(sequence)
                with self.assertRaises(salla.SallaError) as failure:
                    await salla.call_salla(None, "synthetic", "POST", "/transport-probe",
                                          single_post_attempt=True)
                self.assertEqual(failure.exception.status_code, 401)
                self.assertEqual(self.provider.posts, 1)
                self.assertEqual(self.provider.responses, sequence[1:])
                self.provider.token.assert_awaited_once()

    async def test_default_callers_retain_both_auth_replay_paths(self):
        for sequence in ([401, 200], [401, 401, 200]):
            with self.subTest(sequence=sequence):
                self.provider.calls.clear()
                self.provider.responses = list(sequence)
                await salla.call_salla(None, "synthetic", "POST", "/transport-probe")
                self.assertEqual(self.provider.posts, len(sequence))

    async def test_get_retains_auth_refresh_even_with_option(self):
        self.provider.responses = [401, 200]
        await salla.call_salla(None, "synthetic", "GET", "/transport-probe", single_post_attempt=True)
        self.assertEqual(self.provider.calls.count(("GET", "/transport-probe")), 2)
        self.assertEqual(self.provider.posts, 0)

    async def test_redirects_never_follow_or_replay(self):
        for code in (301, 302, 303, 307, 308):
            with self.subTest(code=code):
                self.provider.calls.clear()
                self.provider.responses = [code, 200]
                with self.assertRaises(salla.SallaError):
                    await salla.call_salla(None, "synthetic", "POST", "/transport-probe", single_post_attempt=True)
                self.assertEqual(self.provider.calls, [("POST", "/transport-probe")])

    async def test_timeout_after_full_post_body_has_one_send(self):
        self.provider.failure = "timeout"
        timeout = httpx.Timeout(.05)
        with patch.object(salla.httpx, "Timeout", return_value=timeout):
            with self.assertRaises(httpx.ReadTimeout):
                await salla.call_salla(None, "synthetic", "POST", "/transport-probe", single_post_attempt=True)
        self.assertEqual(self.provider.posts, 1)

    async def test_disconnect_after_post_body_has_one_send(self):
        self.provider.failure = "disconnect"
        with self.assertRaises(httpx.RemoteProtocolError):
            await salla.call_salla(None, "synthetic", "POST", "/transport-probe", single_post_attempt=True)
        self.assertEqual(self.provider.posts, 1)


class MongoSinglePostTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fixture = assignment_fixture.AssignmentSyncTests()
        await self.fixture.asyncSetUp()
        await self.fixture.allocate()
        self.before = await self.fixture.db[pieces.PREPARATION_UNIT_ALLOCATIONS].find({}).to_list(20)
        self.provider = Provider()
        await self.provider.start()
        self.transport = patch.object(pieces, "call_salla", salla.call_salla)
        self.transport.start()

    async def asyncTearDown(self):
        self.transport.stop()
        await self.provider.close()
        await self.fixture.asyncTearDown()

    async def assert_preserved_and_confirm(self):
        t = self.fixture
        self.assertEqual(self.provider.posts, 1)
        self.assertEqual((await t.state())["salla_status_sync_state"], "dispatch_started")
        self.assertEqual(await t.db[pieces.PREPARATION_UNIT_ALLOCATIONS].find({}).to_list(20), self.before)
        with self.assertRaises(RuntimeError):
            await t.reconcile()
        self.assertEqual(self.provider.posts, 1)
        self.provider.status = "in_progress"
        self.provider.readback_failure = False
        self.assertEqual(await t.reconcile(), (True, 0))
        self.assertEqual(self.provider.posts, 1)
        self.assertEqual((await t.state())["salla_status_sync_state"], "sent")
        self.assertEqual(await t.db[pieces.PREPARATION_UNIT_ALLOCATIONS].find({}).to_list(20), self.before)

    async def test_401_claim_survives_and_reconciliation_only_reads(self):
        self.provider.responses = [401, 401, 200]
        with self.assertRaises(RuntimeError):
            await self.fixture.reconcile()
        await self.assert_preserved_and_confirm()
        self.assertEqual(self.provider.responses, [401, 200])

    async def test_timeout_claim_survives_and_reconciliation_only_reads(self):
        self.provider.failure = "timeout"
        timeout = httpx.Timeout(.05)
        with patch.object(salla.httpx, "Timeout", return_value=timeout):
            with self.assertRaises(httpx.ReadTimeout):
                await self.fixture.reconcile()
        await self.assert_preserved_and_confirm()

    async def test_readback_failure_never_reposts(self):
        self.provider.readback_failure = True
        with self.assertRaises(RuntimeError):
            await self.fixture.reconcile()
        await self.assert_preserved_and_confirm()

    async def test_disconnect_claim_survives_and_reconciliation_only_reads(self):
        self.provider.failure = "disconnect"
        with self.assertRaises(httpx.RemoteProtocolError):
            await self.fixture.reconcile()
        await self.assert_preserved_and_confirm()

    async def test_provider_backed_retains_auth_replay(self):
        await self.fixture.db[pieces.WORKFLOWS].update_one({}, {"$unset": {"completion_mode": ""}})
        self.provider.responses = [401, 200]
        self.assertEqual(await self.fixture.reconcile(), (True, 0))
        self.assertEqual(self.provider.posts, 2)

    async def test_concurrent_real_transport_has_one_post(self):
        outcomes = await asyncio.gather(*(self.fixture.reconcile() for _ in range(10)), return_exceptions=True)
        self.assertIn((True, 0), outcomes)
        self.assertEqual(self.provider.posts, 1)
        await self.fixture.reconcile()
        self.assertEqual(self.provider.posts, 1)
        self.assertEqual(await self.fixture.db[pieces.PREPARATION_UNIT_ALLOCATIONS].find({}).to_list(20), self.before)

    async def test_revision_conflict_after_readback_reconciles_without_post(self):
        original = pieces._sync_salla_in_progress
        async def interleave(*args, **kwargs):
            result = await original(*args, **kwargs)
            self.assertEqual(result, ("sent", None))
            await self.fixture.db[pieces.WORKFLOWS].update_one({}, {"$inc": {"revision": 1}})
            return result
        with patch.object(pieces, "_sync_salla_in_progress", interleave):
            with self.assertRaisesRegex(RuntimeError, "local_confirmation_conflict"):
                await self.fixture.reconcile()
        self.assertEqual((await self.fixture.state())["salla_status_sync_state"], "dispatch_started")
        self.assertEqual(await self.fixture.reconcile(), (True, 0))
        self.assertEqual(self.provider.posts, 1)
        self.assertEqual(await self.fixture.db[pieces.PREPARATION_UNIT_ALLOCATIONS].find({}).to_list(20), self.before)
        self.assertEqual(await self.fixture.db[pieces.EVENTS].count_documents({"event_type": "order_moved_to_in_progress"}), 1)
