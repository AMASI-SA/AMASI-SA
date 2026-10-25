"""Content Posting metadata only. This server has no media upload/relay API."""
import asyncio
import json
from urllib.parse import urlsplit

import httpx

from .tiktok_creator_api import (MAX_REQUEST_BYTES, MAX_RESPONSE_BYTES, TikTokCreatorAPI,
                                 _private_request, problem)

BASE = "https://open.tiktokapis.com"
ENDPOINTS = {
    ("GET", "/v2/user/info/"),
    ("POST", "/v2/post/publish/creator_info/query/"),
    ("POST", "/v2/post/publish/video/init/"),
    ("POST", "/v2/post/publish/inbox/video/init/"),
    ("POST", "/v2/post/publish/status/fetch/"),
}
REJECTIONS = {"access_token_invalid", "scope_not_authorized", "invalid_param", "invalid_params",
    "unaudited_client_can_only_post_to_private_accounts", "spam_risk_user_banned_from_posting",
    "spam_risk_too_many_posts", "reached_active_user_cap", "rate_limit_exceeded"}
UPLOAD_HOSTS = {"open-upload.tiktokapis.com", "upload.us.tiktokapis.com", "upload.eu.tiktokapis.com"}
CHUNK_BYTES = 10 * 1024 * 1024
MAX_VIDEO_BYTES = 1024 * 1024 * 1024


def source_info(size):
    if type(size) is not int or not 1 <= size <= MAX_VIDEO_BYTES:
        raise problem("tiktok_file_size_invalid", "حجم الفيديو خارج الحد المسموح.", 422)
    chunk = min(CHUNK_BYTES, size)
    return {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": chunk,
            "total_chunk_count": max(1, size // chunk)}


def upload_url(value):
    try:
        p = urlsplit(value)
        valid = (isinstance(value, str) and len(value) <= 8192 and not any(c.isspace() or c == "\\" for c in value)
            and p.scheme == "https" and p.hostname in UPLOAD_HOSTS and p.netloc.lower() == p.hostname
            and not p.username and not p.password and not p.fragment and p.path in {"/video/", "/upload/"}
            and bool(p.query))
    except (ValueError, TypeError, AttributeError):
        valid = False
    if not valid:
        raise problem("tiktok_file_upload_destination_unproven", "تعذر إثبات وجهة رفع الملف؛ راجع حالة العملية.", 502)
    return value


class TikTokFileAPI(TikTokCreatorAPI):
    async def call(self, method, path, *, token, body=None, params=None):
        if (method, path) not in ENDPOINTS or not isinstance(token, str) or not token:
            raise problem("tiktok_file_endpoint_denied", "مسار رفع الملفات غير مدعوم.", 500)
        if body is not None and (not isinstance(body, dict) or len(json.dumps(body).encode()) > MAX_REQUEST_BYTES):
            raise problem("tiktok_file_metadata_invalid", "أرسل بيانات الفيديو فقط.", 422)
        private = _private_request.set(True)
        try:
            async with asyncio.timeout(14):
                async with self.client.stream(method, BASE + path, headers={"Authorization": "Bearer " + token,
                        "Accept-Encoding": "identity", "Content-Type": "application/json"}, json=body, params=params) as response:
                    if response.status_code >= 500 or response.status_code < 200 or 300 <= response.status_code < 400:
                        raise problem("tiktok_file_provider_http_error", "تعذر تأكيد طلب TikTok؛ راجع حالة العملية.", 502)
                    if "application/json" not in response.headers.get("content-type", "").lower() or response.headers.get("content-encoding", "").lower() not in {"", "identity"}:
                        raise problem("tiktok_file_response_unproven", "تعذر التحقق من استجابة TikTok.", 502)
                    length = response.headers.get("content-length")
                    if length and (not length.isdigit() or int(length) > MAX_RESPONSE_BYTES):
                        raise problem("tiktok_file_response_unproven", "استجابة TikTok أكبر من الحد المسموح.", 502)
                    data = bytearray()
                    async for chunk in response.aiter_bytes(chunk_size=8192):
                        if len(data) + len(chunk) > MAX_RESPONSE_BYTES:
                            raise problem("tiktok_file_response_unproven", "استجابة TikTok أكبر من الحد المسموح.", 502)
                        data.extend(chunk)
            try:
                payload = json.loads(data)
            except (ValueError, UnicodeError):
                raise problem("tiktok_file_response_unproven", "تعذر التحقق من استجابة TikTok.", 502) from None
            error = payload.get("error") if isinstance(payload, dict) else None
            if not isinstance(error, dict) or error.get("code") != "ok" or not isinstance(payload.get("data"), dict) or response.status_code >= 400:
                failure = problem("tiktok_file_provider_rejected", "لم يقبل TikTok مسار رفع الملفات لهذا الحساب. راجع صلاحيات النشر وربط التطبيق.", 502)
                code = error.get("code") if isinstance(error, dict) else None
                if isinstance(code, str) and code in REJECTIONS:
                    failure.detail.update({"native_rejected": True, "provider_code": code})
                raise failure
            return payload["data"]
        except httpx.HTTPError:
            raise problem("tiktok_file_transport_failed", "تعذر الاتصال بتيك توك؛ راجع حالة العملية قبل الإرسال مجددًا.", 502) from None
        finally:
            _private_request.reset(private)
