"""Admission covers the complete ASGI document lifetime, including slow send.

The same fail-fast slots cover issuance/download/parser/persistence. This bounds
live document operations per event loop; it is not a process RSS or proxy limit.
"""
import asyncio

from fastapi.routing import APIRoute
from starlette.responses import JSONResponse, Response

from shipping_pdf_sandbox import ParserError, document_slot

REQUEST_SECONDS = 45
SEND_CHUNK_BYTES = 64 * 1024
HEADERS = {"Cache-Control": "no-store, private", "Referrer-Policy": "no-referrer",
           "X-Content-Type-Options": "nosniff"}


class BoundedPDFResponse(Response):
    async def __call__(self, scope, receive, send):
        await send({"type": "http.response.start", "status": self.status_code,
                    "headers": self.raw_headers})
        # Avoid a single 2 MiB write into an ASGI server's socket buffer. Each
        # next send observes its backpressure; the final empty send does too.
        # Remaining transport/proxy buffers still require hosting connection caps.
        for offset in range(0, len(self.body), SEND_CHUNK_BYTES):
            await send({"type": "http.response.body",
                        "body": self.body[offset:offset + SEND_CHUNK_BYTES], "more_body": True})
        await send({"type": "http.response.body", "body": b"", "more_body": False})
        if self.background is not None:
            await self.background()


class DocumentCapacityRoute(APIRoute):
    async def handle(self, scope, receive, send):
        if not self.path.endswith("/carrier-label/document/{token}"):
            return await super().handle(scope, receive, send)
        started = False

        async def tracked_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            async with document_slot():
                # wait_for waits for cancellation cleanup. Capacity is never
                # recycled while the handler still owns bytes or a parser child.
                await asyncio.wait_for(super().handle(scope, receive, tracked_send), REQUEST_SECONDS)
        except (ParserError, TimeoutError) as exc:
            if started:
                # A partial PDF must remain a failed transport, not a second
                # successful response or an HTML error appended to PDF bytes.
                raise
            code = exc.code if isinstance(exc, ParserError) else "shipping_document_request_timeout"
            await JSONResponse({"detail": {"code": "shipping_snapshot_changed", "reason_code": code}},
                               status_code=503, headers=HEADERS)(scope, receive, send)
