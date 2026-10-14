"""Synthetic PDFs and HTTP transport only; never contact DNS or a provider."""
from datetime import datetime, timedelta, timezone
import hashlib
import socket
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from mongomock_motor import AsyncMongoMockClient
import pymupdf

import shipping_print_document as documents


def pdf_bytes(text="AWB-123456", pages=1, encrypted=False):
    with pymupdf.open() as pdf:
        for _ in range(pages):
            page = pdf.new_page()
            page.insert_text((40, 50), text)
        return pdf.tobytes(**({"encryption": pymupdf.PDF_ENCRYPT_AES_256,
                              "owner_pw": "owner", "user_pw": "secret"} if encrypted else {}))


class DocumentTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.db = AsyncMongoMockClient()["document_fixture"]
        self.snapshot = {"ready": True, "label_url": "https://labels.example.test/a.pdf?signature=synthetic",
                         "shipment_id": "shipment-a", "tracking_number": "AWB-123456",
                         "courier_name": "Synthetic courier", "status": "created"}

    async def store(self, data=None, *, status=200, headers=None):
        requests = []
        real_client = httpx.AsyncClient
        body = pdf_bytes() if data is None else data
        def handler(request):
            requests.append(request)
            return httpx.Response(status, headers=headers or {"Content-Type": "application/pdf"},
                                  stream=httpx.ByteStream(body))
        def client(**kwargs):
            self.assertFalse(kwargs["trust_env"])
            self.assertFalse(kwargs["follow_redirects"])
            return real_client(transport=httpx.MockTransport(handler), **kwargs)
        public = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
        with patch.object(documents.asyncio.get_running_loop(), "getaddrinfo", AsyncMock(return_value=public)), \
             patch.object(documents.httpx, "AsyncClient", client):
            result = await documents.verify_and_store(self.db, "owner", "42", self.snapshot)
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].method, "GET")
        self.assertEqual(requests[0].url.host, "93.184.216.34")
        self.assertEqual(requests[0].headers["host"], "labels.example.test")
        self.assertEqual(requests[0].extensions["sni_hostname"], "labels.example.test")
        self.assertNotIn("authorization", requests[0].headers)
        return result

    async def test_exact_verified_bytes_and_identity_are_immutable(self):
        body = pdf_bytes()
        result = await self.store(body)
        self.assertTrue(result["label_url"].startswith(
            "https://mezansalla.com/api/fulfillment-v2/completed/42/carrier-label/document/"))
        token = result["label_url"].rsplit("/", 1)[1]
        row = await documents.load_document(self.db, "42", token)
        self.assertEqual(row["bytes"], body)
        self.assertEqual(row["document_sha256"], hashlib.sha256(body).hexdigest())
        self.assertEqual(result["document_sha256"], row["document_sha256"])
        self.assertEqual(row["user_id"], "owner")
        self.assertEqual(row["source_url"], self.snapshot["label_url"])
        self.assertEqual(row["shipment_id"], "shipment-a")
        self.assertEqual(row["tracking_number"], "AWB-123456")
        self.assertNotEqual(row["_id"], token)
        with self.assertRaises(documents.DocumentError):
            await documents.load_document(self.db, "other-order", token)
        await self.db[documents.COLLECTION].update_one({"_id": row["_id"]}, {"$set": {"bytes": b"changed"}})
        with self.assertRaises(documents.DocumentError) as error:
            await documents.load_document(self.db, "42", token)
        self.assertEqual(error.exception.code, "shipping_document_integrity_failed")

    async def test_expiry_is_enforced_before_mongo_ttl_cleanup(self):
        result = await self.store()
        token = result["label_url"].rsplit("/", 1)[1]
        # TTL deletion is asynchronous in Mongo; remove the mock's eager TTL index.
        await self.db[documents.COLLECTION].drop_index("expires_at_1")
        await self.db[documents.COLLECTION].update_many({}, {"$set": {
            "expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)}})
        with self.assertRaises(documents.DocumentError) as error:
            await documents.load_document(self.db, "42", token)
        self.assertEqual(error.exception.status_code, 410)

    async def test_rejects_redirect_errors_wrong_mime_and_size(self):
        cases = [(b"", 302, {"Location": "https://other.test"}),
                 (b"error", 500, {"Content-Type": "application/pdf"}),
                 (b"<html>AWB-123456</html>", 200, {"Content-Type": "text/html"}),
                 (b"", 200, {"Content-Type": "application/pdf", "Content-Length": str(documents.MAX_BYTES + 1)}),
                 (b"x" * (documents.MAX_BYTES + 1), 200, {"Content-Type": "application/pdf"})]
        for data, status, headers in cases:
            with self.subTest(status=status, headers=headers), self.assertRaises(documents.DocumentError):
                await self.store(data, status=status, headers=headers)
        self.assertEqual(await self.db[documents.COLLECTION].count_documents({}), 0)

    async def test_pdf_validation_requires_exact_awb_and_supported_document(self):
        for data in (pdf_bytes("XAWB-123456"), pdf_bytes("AWB-1234567"), pdf_bytes(""),
                     pdf_bytes(pages=9), pdf_bytes(encrypted=True), b"%PDF-1.7 broken %%EOF",
                     b"<html>AWB-123456</html>"):
            with self.subTest(size=len(data)), self.assertRaises(documents.DocumentError):
                await self.store(data)
        self.assertEqual(await self.db[documents.COLLECTION].count_documents({}), 0)

    async def test_unsafe_urls_and_dns_never_reach_http(self):
        for url in ("http://public.test/a", "https://user:password@public.test/a",
                    "https://public.test:8443/a", "https://public.test/a#fragment"):
            with self.subTest(url=url), patch.object(documents.httpx, "AsyncClient") as client:
                with self.assertRaises(documents.DocumentError):
                    await documents._download(url)
                client.assert_not_called()
        for address in ("127.0.0.1", "10.0.0.1", "169.254.169.254", "::1", "224.0.0.1"):
            rows = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))]
            with patch.object(documents.asyncio.get_running_loop(), "getaddrinfo", AsyncMock(return_value=rows)), \
                 patch.object(documents.httpx, "AsyncClient") as client:
                with self.assertRaises(documents.DocumentError):
                    await documents._download("https://public.test/a")
                client.assert_not_called()

    async def test_timeout_and_missing_identity_do_not_create_capability(self):
        with patch.object(documents, "_public_target", AsyncMock(side_effect=TimeoutError)):
            with self.assertRaises(documents.DocumentError) as error:
                await documents._download(self.snapshot["label_url"])
        self.assertEqual(error.exception.code, "shipping_document_fetch_failed")
        for field in ("tracking_number", "shipment_id", "label_url"):
            snapshot = {**self.snapshot, field: None}
            with patch.object(documents, "_download", AsyncMock()) as download:
                with self.assertRaises(documents.DocumentError):
                    await documents.verify_and_store(self.db, "owner", "42", snapshot)
                download.assert_not_awaited()
        self.assertEqual(await self.db[documents.COLLECTION].count_documents({}), 0)


if __name__ == "__main__":
    unittest.main()
