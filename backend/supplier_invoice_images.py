"""Optional supplier invoice thumbnails. Never writes financial documents.

Only the authenticated, canonical invoice display supplies image references.
Successful thumbnails survive process restarts in a tenant-scoped cache; failed
images remain optional and retry on a later print. No Salla API is invoked.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import logging
from time import perf_counter
from functools import wraps
from urllib.parse import urlsplit

import httpx
from PIL import Image
from starlette.concurrency import run_in_threadpool

CACHE = "mezan_supplier_invoice_thumbnails_v1"
MEZAN_PREFIX = "/api/order-reviews-v1/mezan-images/"
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_IMAGE_PIXELS = 16_000_000
MAX_THUMBNAIL_BYTES = 256 * 1024
IMAGE_DEADLINE_SECONDS = 3.0
IMAGE_CONCURRENCY = 6
logger = logging.getLogger(__name__)


async def measured(stage, awaitable):
    """Fixed stage names only: no identifiers, URLs, arguments or exception text."""
    started = perf_counter()
    try:
        return await awaitable
    finally:
        logger.info("supplier_invoice_stage stage=%s elapsed_ms=%.3f", stage,
                    (perf_counter() - started) * 1000)


def profiled(stage):
    def decorate(function):
        @wraps(function)
        async def wrapped(*args, **kwargs):
            return await measured(stage, function(*args, **kwargs))
        return wrapped
    return decorate


def safe_remote_url(value):
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").lower()
        trusted = host in {"salla.sa", "salla.network"} or host.endswith((".salla.sa", ".salla.network"))
        return bool(parsed.scheme == "https" and trusted and not parsed.username
                    and not parsed.password and parsed.port in (None, 443))
    except (TypeError, ValueError):
        return False


def thumbnail(raw):
    if not raw or len(raw) > MAX_IMAGE_BYTES:
        return None
    try:
        with Image.open(io.BytesIO(raw)) as source:
            if source.width * source.height > MAX_IMAGE_PIXELS:
                return None
            source.thumbnail((420, 420))
            source = source.convert("RGB")
            result = io.BytesIO()
            source.save(result, format="JPEG", quality=78, optimize=True)
            value = result.getvalue()
            return value if len(value) <= MAX_THUMBNAIL_BYTES else None
    except Exception:
        return None


def decode_image(value):
    if not isinstance(value, str) or len(value) > (MAX_IMAGE_BYTES * 4 // 3 + 4):
        return None
    try:
        return base64.b64decode(value, validate=True)
    except (ValueError, TypeError):
        return None


async def _download(client, url):
    if not safe_remote_url(url):
        return None
    # Do not follow redirects: a trusted CDN must not redirect to a private host.
    async with client.stream("GET", url) as response:
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


async def prepare_invoice_images(db, merchant_id, invoice):
    """Return URL -> JPEG bytes, deduplicated and bounded across the whole print.

    Existing canonical snapshots win over current catalog data. Mezan uploads
    and the invoice pieces' own preparation batch thumbnails are reused before
    downloading. Cache identity includes tenant and the exact snapshot URL.
    Missing photos never alter the immutable invoice or its totals/services.
    """
    cards = (invoice.get("display") or {}).get("cards", [])
    urls = {str(row.get("selected_image_url") or "").strip() for row in cards}
    urls.discard("")
    if not urls:
        return {}
    images = {}
    local = {}
    batch_ids = {str(piece["source"]["batch_id"]) for card in cards
                 for piece in card.get("pieces", []) if piece.get("source", {}).get("batch_id")}
    limit = asyncio.Semaphore(IMAGE_CONCURRENCY)

    async def prepare():
        if batch_ids:
            try:
                batches = await db["mezan_preparation_batches_v2"].find(
                    {"user_id": merchant_id, "id": {"$in": sorted(batch_ids)}},
                    {"_id": 0, "lines.selected_image_url": 1, "lines.resolved_image_url": 1, "lines.image_b64": 1},
                ).to_list(length=len(batch_ids))
                for batch in batches:
                    for line in batch.get("lines", []):
                        url = line.get("selected_image_url")
                        if url in urls and line.get("image_b64") and line.get("resolved_image_url", url) in (None, url):
                            local[url] = line["image_b64"]
            except Exception:
                pass  # Optional image storage may be temporarily unavailable.
        async with httpx.AsyncClient(timeout=httpx.Timeout(2.5, connect=1.5),
                follow_redirects=False, trust_env=False,
                limits=httpx.Limits(max_connections=IMAGE_CONCURRENCY),
                headers={"User-Agent": "AMASI-Supplier-Invoice/2.0"}) as client:
            async def hydrate(url):
                async with limit:
                    key = hashlib.sha256((merchant_id + "\0" + url).encode()).hexdigest()
                    try:
                        asset = None
                        if url.startswith(MEZAN_PREFIX):
                            image_id = url[len(MEZAN_PREFIX):]
                            if not image_id or "/" in image_id or "?" in image_id:
                                return
                            asset = await db["order_review_mezan_images"].find_one(
                                {"user_id": merchant_id, "id": image_id, "deleted_at": {"$exists": False}},
                                {"_id": 0, "data_base64": 1})
                            if not asset:
                                return
                        saved = await db[CACHE].find_one({"_id": key, "user_id": merchant_id})
                        if saved:
                            raw = decode_image(saved.get("image_b64"))
                            image = await run_in_threadpool(thumbnail, raw)
                            if image:
                                images[url] = image
                                return
                        image = await run_in_threadpool(thumbnail, decode_image(local.get(url)))
                        if not image and asset:
                            image = await run_in_threadpool(thumbnail, decode_image(asset.get("data_base64")))
                        if not image:
                            image = await _download(client, url)
                        if image:
                            images[url] = image
                            await db[CACHE].update_one({"_id": key, "user_id": merchant_id},
                                {"$set": {"image_b64": base64.b64encode(image).decode("ascii"),
                                          "content_type": "image/jpeg"}}, upsert=True)
                    except Exception:
                        pass  # Neither CDN nor optional cache failure can drop the PDF.
            await asyncio.gather(*(hydrate(url) for url in sorted(urls)))

    try:
        await asyncio.wait_for(prepare(), timeout=IMAGE_DEADLINE_SECONDS)
    except TimeoutError:
        pass
    return images
