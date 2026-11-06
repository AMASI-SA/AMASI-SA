"""Complete preparation-card images before producing a printable file.

Only the already validated immutable card snapshot supplies image references.
Each job owns its deduplicated in-flight work; no failure is frozen as success.
"""
from __future__ import annotations

import asyncio
import base64
import re
from typing import Any
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException
from starlette.concurrency import run_in_threadpool

from supplier_invoice_images import MAX_IMAGE_BYTES, decode_image, safe_remote_url, thumbnail

CONCURRENCY = 8
JOB_SECONDS = 30
LOCAL_ASSETS = {
    "/api/order-reviews-v1/mezan-images/": "order_review_mezan_images",
    "/api/order-reviews-v1/operational-images/": "order_review_operational_images",
}


def local_asset(value: str):
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if parsed.query or parsed.fragment or parsed.username or parsed.password:
        return None
    if parsed.netloc and (parsed.scheme != "https" or parsed.netloc != "mezansalla.com"):
        return None
    for prefix, collection in LOCAL_ASSETS.items():
        if parsed.path.startswith(prefix):
            identity = parsed.path[len(prefix):]
            if re.fullmatch(r"[A-Za-z0-9_-]{1,160}", identity):
                return collection, identity
    return None


async def _remote(client, url):
    if not safe_remote_url(url):
        return None
    for attempt in range(2):
        try:
            async with client.stream("GET", url) as response:
                if response.status_code in {408, 429, 500, 502, 503, 504} and attempt == 0:
                    continue
                if response.status_code != 200 or not response.headers.get("content-type", "").lower().startswith("image/"):
                    return None
                length = response.headers.get("content-length")
                if length and (not length.isdigit() or int(length) > MAX_IMAGE_BYTES):
                    return None
                raw = bytearray()
                async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
                    raw.extend(chunk)
                    if len(raw) > MAX_IMAGE_BYTES:
                        return None
                return await run_in_threadpool(thumbnail, bytes(raw))
        except (httpx.TimeoutException, httpx.NetworkError):
            if attempt:
                return None
    return None


async def hydrate_preparation_images(db: Any, user_id: str, rows: list[dict], *, client=None) -> list[dict]:
    """Return complete copies, or refuse printing; never changes card options.

Empty references retain their original explicit absence. A configured image
must resolve successfully. Uploaded images are read with the authenticated
tenant, never fetched through an unauthenticated public URL.
"""
    if client is None:
        async with httpx.AsyncClient(timeout=httpx.Timeout(5, connect=2), follow_redirects=False,
                trust_env=False, limits=httpx.Limits(max_connections=CONCURRENCY),
                headers={"User-Agent": "Mozilla/5.0 AMASI-Preparation/1.0", "Referer": "https://salla.sa/"}) as owned:
            return await hydrate_preparation_images(db, user_id, rows, client=owned)
    limit = asyncio.Semaphore(CONCURRENCY)
    jobs = {}

    async def resolve(url):
        async with limit:
            local = local_asset(url)
            if local:
                if db is None or not user_id:
                    return None
                collection, identity = local
                saved = await db[collection].find_one({"user_id": user_id, "id": identity,
                    "deleted_at": {"$exists": False}}, {"_id": 0, "data_base64": 1})
                return await run_in_threadpool(thumbnail, decode_image((saved or {}).get("data_base64")))
            if url.startswith("data:image/") and ";base64," in url:
                return await run_in_threadpool(thumbnail, decode_image(url.split(",", 1)[1]))
            return await _remote(client, url)

    async def hydrate(source):
        row = dict(source)
        frozen = await run_in_threadpool(thumbnail, decode_image(row.get("image_b64")))
        if frozen:
            row.update(image_b64=base64.b64encode(frozen).decode("ascii"), image_mime="image/jpeg", image_missing=False)
            return row
        chosen = str(row.get("selected_image_url") or row.get("resolved_image_url") or "").strip()
        candidates = [chosen] if chosen else list(dict.fromkeys(str(value).strip()
            for value in row.get("image_candidates", []) if str(value).strip()))
        for candidate in candidates:
            # Publish the in-flight task before awaiting it. Eight identical
            # cards share one fetch, not eight concurrent duplicate requests.
            if candidate not in jobs:
                jobs[candidate] = asyncio.create_task(resolve(candidate))
            image = await jobs[candidate]
            if image:
                row.update(image_b64=base64.b64encode(image).decode("ascii"), image_mime="image/jpeg",
                           resolved_image_url=candidate, image_missing=False)
                return row
        row.update(image_b64=None, image_mime=None, image_missing=bool(candidates))
        return row

    try:
        async with asyncio.timeout(JOB_SECONDS):
            hydrated = await asyncio.gather(*(hydrate(row) for row in rows))
    except TimeoutError as exc:
        raise HTTPException(503, detail={"code": "preparation_images_pending",
            "message": "لم يكتمل تحميل صور الملف. أعد فتح الملف نفسه للتحقق؛ لم تُطبع نسخة ناقصة."}) from exc
    finally:
        pending = [job for job in jobs.values() if not job.done()]
        for job in pending:
            job.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
    missing = sum(row.get("image_missing") is True for row in hydrated)
    if missing:
        raise HTTPException(409, detail={"code": "preparation_images_unavailable", "missing_image_count": missing,
            "message": "تعذّر تحميل بعض صور المنتجات؛ لم تُطبع نسخة ناقصة. أعد فتح الملف نفسه للمحاولة."})
    return hydrated
