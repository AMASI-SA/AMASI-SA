"""Fixed-origin creator API transport. Never fetches or stores media bytes."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from contextvars import ContextVar
import json
import logging

import httpx
from fastapi import HTTPException
from fastapi.routing import APIRoute
from resource_governor import governor

API_BASE = "https://business-api.tiktok.com/open_api/v1.3"
MAX_RESPONSE_BYTES = 131_072
MAX_REQUEST_BYTES = 32_768
_slot = asyncio.Semaphore(1)
_private_request = ContextVar("mezan_tiktok_creator_private_request", default=False)
_ENDPOINTS = {
    ("POST", "/tt_user/oauth2/token/"),
    ("POST", "/tt_user/oauth2/refresh_token/"),
    ("POST", "/tt_user/token_info/get/"),
    ("GET", "/business/video/settings/"),
    ("GET", "/business/property/list/"),
    ("POST", "/business/video/publish/"),
    ("POST", "/business/photo/publish/"),
    ("GET", "/business/publish/status/"),
}


class BoundedCreatorJSONRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def bounded(request):
            if request.method == "POST":
                length = request.headers.get("content-length")
                if length and (not length.isdigit() or int(length) > MAX_REQUEST_BYTES):
                    raise problem("tiktok_content_metadata_too_large", "أرسل نص المنشور وروابطه فقط؛ الحد 32 كيلوبايت.", 413)
                data = bytearray()
                async for chunk in request.stream():
                    if len(data) + len(chunk) > MAX_REQUEST_BYTES:
                        raise problem("tiktok_content_metadata_too_large", "أرسل نص المنشور وروابطه فقط؛ الحد 32 كيلوبايت.", 413)
                    data.extend(chunk)
                if data and "application/json" not in request.headers.get("content-type", "").lower():
                    raise problem("tiktok_content_json_only", "يقبل ميزان بيانات النص والروابط بصيغة JSON فقط.", 415)
                # Reuse Starlette's body cache so FastAPI parses only the
                # bounded bytes, including when Content-Length is absent.
                request._body = bytes(data)
            return await handler(request)

        return bounded


class _PrivateTransportLogFilter(logging.Filter):
    def filter(self, record):
        # property/list requires the app secret in the query. HTTPX and
        # HTTPCore must not log that URL, token headers, or OAuth bodies.
        # Context isolation preserves unrelated concurrent requests' logs.
        return not _private_request.get()


for _logger_name in ("httpx", "httpcore.connection", "httpcore.http11", "httpcore.http2", "httpcore.proxy", "httpcore.socks"):
    logging.getLogger(_logger_name).addFilter(_PrivateTransportLogFilter())


def problem(code, message, status=409):
    return HTTPException(status_code=status, detail={"code": code, "message": message})


async def mongo(awaitable):
    async with asyncio.timeout(3):
        return await awaitable


@asynccontextmanager
async def admission():
    if _slot.locked() or governor.peek()[0] in {"blocked", "cancel"}:
        raise problem("tiktok_content_busy", "نشر TikTok مشغول؛ حاول بعد قليل.", 503)
    await _slot.acquire()
    try:
        async with asyncio.timeout(50):
            yield
    except TimeoutError:
        raise problem("tiktok_content_timeout", "انتهت المهلة. راجع حالة العملية قبل أي محاولة نشر جديدة.", 503) from None
    finally:
        _slot.release()


class TikTokCreatorAPI:
    def __init__(self, *, transport=None):
        self.transport = transport

    async def __aenter__(self):
        self.client = httpx.AsyncClient(timeout=12.0, follow_redirects=False,
                                       limits=httpx.Limits(max_connections=1, max_keepalive_connections=1), transport=self.transport)
        return self

    async def __aexit__(self, *args):
        try:
            async with asyncio.timeout(2):
                await self.client.aclose()
        except TimeoutError:
            pass

    async def call(self, method, path, *, token=None, body=None, params=None):
        if (method, path) not in _ENDPOINTS:
            raise problem("tiktok_creator_endpoint_denied", "مسار TikTok غير مدعوم.", 500)
        if body is not None and len(json.dumps(body, ensure_ascii=False).encode("utf-8")) > MAX_REQUEST_BYTES:
            raise problem("tiktok_content_metadata_too_large", "بيانات المنشور أكبر من الحد المسموح.", 413)
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Access-Token"] = token
        private = _private_request.set(True)
        try:
            async with asyncio.timeout(14):
                async with self.client.stream(method, API_BASE + path, headers=headers, params=params, json=body) as response:
                    if response.status_code < 200 or response.status_code >= 300:
                        raise problem("tiktok_creator_provider_http_error", "رفض TikTok الطلب. راجع الربط والصلاحيات.", 502)
                    if "application/json" not in response.headers.get("content-type", "").lower():
                        raise problem("tiktok_creator_provider_not_json", "تعذر التحقق من استجابة TikTok.", 502)
                    length = response.headers.get("content-length")
                    if length and (not length.isdigit() or int(length) > MAX_RESPONSE_BYTES):
                        raise problem("tiktok_creator_response_too_large", "استجابة TikTok تجاوزت الحد المسموح.", 502)
                    data = bytearray()
                    async for chunk in response.aiter_bytes(chunk_size=8192):
                        if len(data) + len(chunk) > MAX_RESPONSE_BYTES:
                            raise problem("tiktok_creator_response_too_large", "استجابة TikTok تجاوزت الحد المسموح.", 502)
                        data.extend(chunk)
            try:
                payload = json.loads(data)
            except (ValueError, UnicodeError):
                raise problem("tiktok_creator_invalid_response", "تعذر التحقق من استجابة TikTok.", 502) from None
            if not isinstance(payload, dict) or type(payload.get("code")) is not int or payload["code"] != 0 or not isinstance(payload.get("data"), dict):
                # Never project a raw provider message or request URL.
                raise problem("tiktok_creator_provider_error", "رفض TikTok العملية. راجع حالة الربط والصلاحيات.", 502)
            return payload["data"]
        except httpx.HTTPError:
            raise problem("tiktok_creator_transport_failed", "تعذر الاتصال بـ TikTok. راجع حالة العملية قبل إعادة النشر.", 502) from None
        finally:
            _private_request.reset(private)
