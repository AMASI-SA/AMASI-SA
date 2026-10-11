from datetime import datetime, timedelta, timezone
import io
import logging
import unittest

from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient, URL as HttpxURL

import shipping_capability_security as security
from shipping_print_document import DocumentError

TOKEN = "A" * 43
URL = "https://mezansalla.com/api/fulfillment-v2/completed/42/carrier-label/document/" + TOKEN


class CapabilitySecurityTests(unittest.TestCase):
    def test_httpx_url_object_is_redacted_before_lazy_log_formatting(self):
        for placeholder in ("%s", "%r"):
            record = logging.LogRecord("httpx", logging.INFO, __file__, 1,
                "HTTP Request: %s " + placeholder + ' "%s %d %s"',
                ("GET", HttpxURL(URL), "HTTP/1.1", 200, "OK"), None)
            record.context = {"url": HttpxURL(URL)}
            security.CapabilityLogFilter().filter(record)
            self.assertNotIn(TOKEN, logging.Formatter().format(record))
            self.assertNotIn(TOKEN, repr(record.context))
            self.assertEqual(record.args[3], 200)

    def test_final_expiry_boundary_and_naive_mongo_utc(self):
        instant = datetime.now(timezone.utc)
        security.assert_document_unexpired({"expires_at": instant + timedelta(seconds=1)}, now=instant)
        security.assert_document_unexpired({"expires_at": (instant + timedelta(seconds=1)).replace(tzinfo=None)}, now=instant)
        for expires in (None, instant, instant - timedelta(seconds=1)):
            with self.assertRaises(DocumentError) as error:
                security.assert_document_unexpired({"expires_at": expires}, now=instant)
            self.assertEqual(error.exception.status_code, 410)

    def test_structured_monitoring_sanitizes_nested_urls_without_mutating_input(self):
        data = {"request": {"url": URL}, "breadcrumbs": [(URL, {"document_token": TOKEN})],
                "binary": URL.encode(), "safe": 200}
        sanitized = security.redact_capability_data(data)
        self.assertNotIn(TOKEN, repr(sanitized))
        self.assertEqual(data["request"]["url"], URL)
        self.assertEqual(sanitized["safe"], 200)
        self.assertNotIn(TOKEN, security.redact_capability_data(URL.replace("/", "%2F")))

    def test_uvicorn_access_argument_contract_and_exception_redaction(self):
        record = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1,
            '%s - "%s %s HTTP/%s" %d', ("127.0.0.1", "GET", URL, "1.1", 410), None)
        security.CapabilityLogFilter().filter(record)
        # Preserve Uvicorn AccessFormatter's five-value unpacking contract.
        client, method, path, version, status = record.args
        self.assertEqual((method, version, status), ("GET", "1.1", 410))
        rendered = logging.Formatter().format(record)
        self.assertNotIn(TOKEN, rendered)
        self.assertIn("[REDACTED]", rendered)
        try:
            raise RuntimeError("Failed download " + URL)
        except RuntimeError:
            import sys
            record = logging.LogRecord("httpx", logging.ERROR, __file__, 1, "failure %s", (URL,), sys.exc_info())
        security.CapabilityLogFilter().filter(record)
        self.assertIsNone(record.exc_info)
        self.assertNotIn(TOKEN, logging.Formatter().format(record))

    def test_install_handles_future_handlers_and_is_idempotent(self):
        original_factory = logging.getLogRecordFactory()
        original_make_record = logging.Logger.makeRecord
        names = (None, "uvicorn", "uvicorn.access", "uvicorn.error", "httpx", "httpcore")
        previous = [(logging.getLogger(name), list(logging.getLogger(name).filters)) for name in names]
        handlers = [(handler, list(handler.filters)) for logger, _ in previous for handler in logger.handlers]
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(logging.Formatter("%(message)s %(document_token)s %(context)s %(factory_marker)s"))
        logger = logging.getLogger("shipping.security.synthetic")
        old_level, old_propagate = logger.level, logger.propagate
        try:
            def existing_factory(*args, **kwargs):
                record = original_factory(*args, **kwargs)
                record.factory_marker = "preserved"
                return record
            logging.setLogRecordFactory(existing_factory)
            security.install_capability_log_redaction()
            factory = logging.getLogRecordFactory()
            make_record = logging.Logger.makeRecord
            security.install_capability_log_redaction()
            self.assertIs(logging.getLogRecordFactory(), factory)
            self.assertIs(logging.Logger.makeRecord, make_record)
            logger.addHandler(handler)
            logger.setLevel(logging.INFO)
            logger.propagate = False
            extra = {"document_token": TOKEN, "context": {"url": URL, "nested": [{"capability_token": TOKEN}]}}
            try:
                raise RuntimeError(URL)
            except RuntimeError:
                logger.exception("HTTP GET %s", URL, extra=extra)
            self.assertNotIn(TOKEN, stream.getvalue())
            self.assertIn("[REDACTED]", stream.getvalue())
            self.assertIn("preserved", stream.getvalue())
            self.assertEqual(extra["document_token"], TOKEN)
            with self.assertRaises(KeyError):
                logger.info("collision", extra={"msg": "must remain forbidden"})
        finally:
            logging.setLogRecordFactory(original_factory)
            logging.Logger.makeRecord = original_make_record
            logger.removeHandler(handler)
            logger.setLevel(old_level)
            logger.propagate = old_propagate
            for target, filters in previous + handlers:
                target.filters[:] = filters


class HeaderTests(unittest.IsolatedAsyncioTestCase):
    async def test_crashing_app_has_generic_protected_error_and_no_token(self):
        async def crashing(scope, receive, send):
            raise RuntimeError(URL)
        app = security.CapabilityResponseHeadersMiddleware(crashing)
        with self.assertLogs("shipping_capability_security", level="ERROR") as logs:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="https://example.test") as client:
                response = await client.get(URL)
        self.assertEqual(response.status_code, 500)
        self.assertNotIn(TOKEN, response.text + repr(logs.output))
        self.assertEqual(response.headers["cache-control"], "no-store, private")
        self.assertEqual(response.headers["referrer-policy"], "no-referrer")
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")

    async def test_started_response_and_cancellation_are_not_swallowed(self):
        import asyncio
        async def started(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            raise RuntimeError("already started")
        async def cancelled(scope, receive, send):
            raise asyncio.CancelledError()
        async def noop(*args):
            return None
        scope = {"type": "http", "path": "/carrier-label/document/token"}
        with self.assertRaises(RuntimeError):
            await security.CapabilityResponseHeadersMiddleware(started)(scope, noop, noop)
        with self.assertRaises(asyncio.CancelledError):
            await security.CapabilityResponseHeadersMiddleware(cancelled)(scope, noop, noop)

    async def test_capability_success_and_errors_are_not_cacheable(self):
        app = FastAPI()
        @app.get("/api/fulfillment-v2/completed/42/carrier-label/document/{token}")
        async def document(token: str):
            if token == "expired":
                raise HTTPException(410, "expired")
            return {"ok": True}
        @app.get("/unrelated")
        async def unrelated():
            return {"ok": True}
        guarded = security.CapabilityResponseHeadersMiddleware(app)
        async with AsyncClient(transport=ASGITransport(app=guarded), base_url="https://example.test") as client:
            for token in (TOKEN, "expired"):
                response = await client.get("/api/fulfillment-v2/completed/42/carrier-label/document/" + token)
                self.assertEqual(response.headers["cache-control"], "no-store, private")
                self.assertEqual(response.headers["referrer-policy"], "no-referrer")
                self.assertEqual(response.headers["x-content-type-options"], "nosniff")
            self.assertNotIn("cache-control", (await client.get("/unrelated")).headers)
