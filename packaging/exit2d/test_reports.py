"""HTTP contracts for the independent report process, with no production DB."""
import asyncio
from pathlib import Path
import sys
import unittest

import httpx

sys.path.insert(0, str(Path("/opt/mezan/backend") if Path("/opt/mezan/backend").is_dir()
    else Path(__file__).resolve().parents[2] / "backend"))
from independent_reports import ReportBoundary, is_report_request, validate_worker_url


async def endpoint(scope, receive, send):
    cookie = dict(scope.get("headers", [])).get(b"cookie", b"")
    await send({"type": "http.response.start", "status": 200,
        "headers": [(b"content-type", b"application/json")]})
    await send({"type": "http.response.body", "body": cookie or b"local"})


class ReportTests(unittest.IsolatedAsyncioTestCase):
    async def test_employees_salla_and_qoyod_stay_in_web(self):
        app = ReportBoundary(endpoint, role="web", worker_url="http://worker")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://web") as client:
            for path in ("/api/auth/me", "/api/employees-v2", "/api/salla/orders", "/api/integrations/qoyod"):
                self.assertEqual((await client.get(path)).content, b"local")
            self.assertEqual((await client.get("/api/dashboard-v2")).status_code, 503)

    async def test_worker_refuses_business_routes_and_writes(self):
        app = ReportBoundary(endpoint, role="worker-reports")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://worker") as client:
            for method, path in (("GET", "/api/auth/me"), ("POST", "/api/dashboard"),
                    ("POST", "/api/integrations/qoyod"), ("GET", "/api/salla/orders")):
                self.assertEqual((await client.request(method, path)).status_code, 404)
            self.assertEqual((await client.get("/api/dashboard-v2")).status_code, 200)

    async def test_auth_query_status_and_binary_response_preserved_without_cookie_borrowing(self):
        received = []
        class RawBytes(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b"\x00\xff"
        def upstream(request):
            received.append(request)
            return httpx.Response(403, stream=RawBytes(), headers={
                "Content-Type": "application/octet-stream", "Set-Cookie": "session=previous-user",
                "Connection": "keep-alive, X-Internal", "X-Internal": "must-not-forward"})
        async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as worker:
            worker.cookies.set("session", "another-user", domain="worker")
            app = ReportBoundary(endpoint, role="web", worker_url="http://worker", client=worker)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://web") as client:
                first = await client.get("/api/dashboard?from_date=2026-01-01", headers={"Cookie": "session=owner"})
                self.assertEqual(first.status_code, 403)
                self.assertEqual(first.content, b"\x00\xff")
                self.assertNotIn("x-internal", first.headers)
                client.cookies.clear()
                await client.get("/api/dashboard")
        self.assertEqual(received[0].headers["cookie"], "session=owner")
        self.assertEqual(received[0].url.query, b"from_date=2026-01-01")
        self.assertEqual(received[1].headers["cookie"], "")

    async def test_failure_does_not_retry_or_fall_back_to_local(self):
        calls = []
        def failed(request):
            calls.append(request)
            raise httpx.ConnectError("fixture", request=request)
        async with httpx.AsyncClient(transport=httpx.MockTransport(failed)) as worker:
            app = ReportBoundary(endpoint, role="web", worker_url="http://worker", client=worker)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://web") as client:
                response = await client.get("/api/dashboard")
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.json()["detail"]["code"], "report_worker_unavailable")
        self.assertEqual(len(calls), 1)

    async def test_concurrency_saturation_fails_boundedly(self):
        app = ReportBoundary(endpoint, role="web", worker_url="http://worker", client=object())
        for _ in range(4):
            await app.slots.acquire()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://web") as client:
            response = await asyncio.wait_for(client.get("/api/dashboard"), 1)
            self.assertEqual(response.status_code, 503)

    def test_explicit_path_and_origin_boundaries(self):
        for path in ("/api/dashboard-v2/abandoned-carts/recent", "/api/analyses/fixture/export/pdf",
                "/api/analyses", "/api/analyses/fixture", "/api/operational-reports"):
            self.assertTrue(is_report_request("GET", path))
        for value in ("file:///tmp/x", "http://user:password@host", "https://host/path", "https://host?query",
                "http://host:not-a-port"):
            with self.assertRaises(ValueError):
                validate_worker_url(value)
        self.assertFalse(is_report_request("GET", "/api/dashboard-employee"))

    async def test_upstream_close_failure_does_not_leak_concurrency_slot(self):
        class BrokenStream(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b"fixture"
            async def aclose(self):
                raise RuntimeError("synthetic close failure")
        async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda request: httpx.Response(200, stream=BrokenStream()))) as worker:
            app = ReportBoundary(endpoint, role="web", worker_url="http://worker", client=worker)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://web") as client:
                with self.assertRaises(RuntimeError):
                    await client.get("/api/dashboard")
            # All four request slots remain available after the failed stream.
            for _ in range(4):
                await asyncio.wait_for(app.slots.acquire(), 0.1)


if __name__ == "__main__":
    unittest.main()
