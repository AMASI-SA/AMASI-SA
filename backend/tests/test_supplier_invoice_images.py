"""Offline image/PDF regression contracts; no merchant/provider access."""
import asyncio
import base64
from copy import deepcopy
import io
import threading
import time

import httpx
from PIL import Image
import pytest

from test_supplier_native_invoice_v2 import env
import supplier_invoice_images as images
from supplier_invoice_pdf import generate_supplier_invoice_pdf


@pytest.fixture(autouse=True)
def generous_unit_budget(monkeypatch):
    # Validation tests target security/data seams independent of worker startup
    # under host load. The dedicated slow-image test uses the actual 3s budget.
    monkeypatch.setattr(images, "IMAGE_DEADLINE_SECONDS", 30)


def png():
    out = io.BytesIO()
    Image.new("RGB", (24, 24), "red").save(out, "PNG")
    return out.getvalue()


class Cursor:
    def __init__(self, rows): self.rows = rows
    async def to_list(self, length): return self.rows[:length]


class Collection:
    def __init__(self): self.rows = []; self.writes = 0
    async def find_one(self, query, projection=None):
        for row in self.rows:
            if all((k not in row if v == {"$exists": False} else row.get(k) == v) for k, v in query.items()):
                return deepcopy(row)
        return None
    def find(self, query, projection=None):
        return Cursor([deepcopy(row) for row in self.rows if row.get("user_id") == query["user_id"]
                       and row.get("id") in query["id"]["$in"]])
    async def update_one(self, query, change, upsert=False):
        self.writes += 1
        self.rows.append({**query, **change["$set"]})


class DB(dict):
    def __missing__(self, key):
        value = self[key] = Collection()
        return value


def invoice(count=1, duplicate=False):
    rows = [{"selected_image_url": f"https://cdn.salla.sa/{0 if duplicate else i}.png",
             "product_name": "Synthetic", "quantity": 1, "product_unit_price_halalas": 100,
             "total_halalas": 100, "services": [], "pieces": []} for i in range(count)]
    return {"invoice_number": "SYNTHETIC", "total_halalas": 100 * count, "lines": rows,
            "display": {"cards": [{**row, "effective_cost": {"numerator": 100, "denominator": 1}} for row in rows]}}


def install_transport(monkeypatch, handler):
    client = httpx.AsyncClient
    monkeypatch.setattr(images.httpx, "AsyncClient", lambda **kwargs: client(transport=httpx.MockTransport(handler), **kwargs))


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [1, 10, 50])
@pytest.mark.parametrize("duplicate", [False, True])
async def test_bounded_deduplicated_persisted_thumbnails(monkeypatch, count, duplicate):
    calls = []; active = 0; maximum = 0
    async def handler(request):
        nonlocal active, maximum
        calls.append(str(request.url)); active += 1; maximum = max(maximum, active)
        await asyncio.sleep(.002)
        active -= 1
        return httpx.Response(200, content=png(), headers={"content-type": "image/png"})
    install_transport(monkeypatch, handler)
    db = DB(); document = invoice(count, duplicate); original = deepcopy(document)
    prepared = await images.prepare_invoice_images(db, "merchant", document)
    assert len(calls) == (1 if duplicate else count)
    assert maximum <= images.IMAGE_CONCURRENCY
    assert len(prepared) == len(calls)
    assert generate_supplier_invoice_pdf(document, images=prepared).startswith(b"%PDF")
    # A fresh call has no process-local cache and still performs no network.
    again = await images.prepare_invoice_images(db, "merchant", document)
    assert len(again) == len(prepared) and len(calls) == len(prepared)
    assert document == original
    assert set(db) == {images.CACHE}


@pytest.mark.asyncio
async def test_reuses_mezan_upload_and_exact_batch_snapshot(monkeypatch):
    async def handler(request): raise AssertionError("network forbidden")
    install_transport(monkeypatch, handler)
    db = DB(); doc = invoice(2)
    local_url = images.MEZAN_PREFIX + "upload"
    doc["display"]["cards"][0]["selected_image_url"] = local_url
    url = doc["display"]["cards"][1]["selected_image_url"]
    doc["display"]["cards"][1]["pieces"] = [{"source": {"batch_id": "batch"}}]
    db["order_review_mezan_images"].rows.append({"user_id": "merchant", "id": "upload", "data_base64": base64.b64encode(png()).decode()})
    db["mezan_preparation_batches_v2"].rows.append({"user_id": "merchant", "id": "batch", "lines": [{"selected_image_url": url, "image_b64": base64.b64encode(png()).decode()}]})
    assert set(await images.prepare_invoice_images(db, "merchant", doc)) == {local_url, url}
    assert await images.prepare_invoice_images(db, "foreign", doc) == {}


@pytest.mark.parametrize("url", ["http://cdn.salla.sa/a", "https://salla.sa.evil/a", "https://127.0.0.1/a", "file:///a", "https://user:pass@cdn.salla.sa/a", "https://cdn.salla.sa:8443/a"])
def test_untrusted_urls(url): assert not images.safe_remote_url(url)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["missing", "bad_image", "redirect", "large"])
async def test_optional_invalid_images(monkeypatch, kind):
    calls = []
    async def handler(request):
        calls.append(request)
        if kind == "redirect": return httpx.Response(302, headers={"location": "https://127.0.0.1/secret"})
        if kind == "missing": return httpx.Response(404)
        if kind == "large": return httpx.Response(200, content=b"x", headers={"content-type": "image/png", "content-length": str(images.MAX_IMAGE_BYTES + 1)})
        return httpx.Response(200, content=b"not-an-image", headers={"content-type": "image/png"})
    install_transport(monkeypatch, handler)
    doc = invoice(50, True)
    assert await images.prepare_invoice_images(DB(), "merchant", doc) == {}
    assert len(calls) == 1
    assert generate_supplier_invoice_pdf(doc).startswith(b"%PDF")


@pytest.mark.asyncio
async def test_whole_invoice_deadline_cancels_slow_images(monkeypatch):
    cancelled = 0
    async def handler(request):
        nonlocal cancelled
        try: await asyncio.sleep(20)
        except asyncio.CancelledError: cancelled += 1; raise
    install_transport(monkeypatch, handler)
    monkeypatch.setattr(images, "IMAGE_DEADLINE_SECONDS", .05)
    started = time.perf_counter()
    assert await images.prepare_invoice_images(DB(), "merchant", invoice(50)) == {}
    assert time.perf_counter() - started < .5
    assert cancelled == images.IMAGE_CONCURRENCY


def test_renderer_never_accesses_network(monkeypatch):
    import urllib.request
    import socket
    def forbidden(*a, **kw): raise AssertionError("renderer attempted network")
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    assert generate_supplier_invoice_pdf(invoice(50)).startswith(b"%PDF")


@pytest.mark.asyncio
async def test_pdf_route_renders_off_api_loop_and_preserves_financial_source(monkeypatch):
    from fastapi import FastAPI
    import supplier_receiving_routes as routes
    doc = invoice(10); doc.pop("display"); original = deepcopy(doc)
    main_thread = threading.get_ident(); render_thread = None
    async def context(*a): return {"merchant_id": "merchant"}
    async def viewer(*a, **kw): return doc
    async def display(*a): return invoice(10)["display"]
    async def prepare(*a): return {}
    async def current(): return {"id": "employee"}
    def render(value, **kw):
        nonlocal render_thread
        render_thread = threading.get_ident(); time.sleep(.05)
        return b"%PDF-synthetic"
    monkeypatch.setattr(routes, "_actor_context", context)
    monkeypatch.setattr(routes, "_require_permission", lambda *a: None)
    monkeypatch.setattr(routes, "_supplier_invoice_for_viewer", viewer)
    monkeypatch.setattr(routes, "load_invoice_display", display)
    monkeypatch.setattr(routes, "prepare_invoice_images", prepare)
    monkeypatch.setattr(routes, "generate_supplier_invoice_pdf", render)
    app = FastAPI(); app.include_router(routes.make_supplier_receiving_router(DB(), current))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://synthetic.test") as client:
        response = await client.get("/supplier-receiving-v1/invoices/invoice/pdf")
    assert response.status_code == 200 and response.content.startswith(b"%PDF")
    assert render_thread != main_thread
    assert doc == original


@pytest.mark.asyncio
async def test_real_three_second_budget_keeps_event_loop_responsive(monkeypatch):
    calls = 0; beats = 0; finished = False
    async def handler(request):
        nonlocal calls
        calls += 1
        await asyncio.sleep(20)
        return httpx.Response(200, content=png(), headers={"content-type": "image/png"})
    async def heartbeat():
        nonlocal beats
        while not finished:
            beats += 1
            await asyncio.sleep(.02)
    install_transport(monkeypatch, handler)
    monkeypatch.setattr(images, "IMAGE_DEADLINE_SECONDS", 3.0)
    beat = asyncio.create_task(heartbeat()); started = time.perf_counter()
    prepared = await images.prepare_invoice_images(DB(), "merchant", invoice(50))
    elapsed = time.perf_counter() - started; finished = True; await beat
    assert prepared == {} and calls == images.IMAGE_CONCURRENCY
    assert 2.8 <= elapsed < 4.5 and beats > 50
    assert generate_supplier_invoice_pdf(invoice(50), images=prepared).startswith(b"%PDF")


@pytest.mark.asyncio
async def test_streaming_size_limit_without_content_length(monkeypatch):
    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            for _ in range(7): yield b"x" * (1024 * 1024)
    async def handler(request):
        return httpx.Response(200, stream=Stream(), headers={"content-type": "image/png"})
    install_transport(monkeypatch, handler)
    assert await images.prepare_invoice_images(DB(), "merchant", invoice()) == {}


def test_pixel_limit_precedes_image_decode(monkeypatch):
    class Source:
        width = 100_000; height = 100_000
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def thumbnail(self, *a): raise AssertionError("oversize image decoded")
    monkeypatch.setattr(images.Image, "open", lambda *a: Source())
    assert images.thumbnail(b"header") is None


@pytest.mark.asyncio
async def test_batch_fallback_cannot_replace_selected_variant(monkeypatch):
    calls = []
    async def handler(request): calls.append(str(request.url)); return httpx.Response(404)
    install_transport(monkeypatch, handler)
    db = DB(); doc = invoice(); card = doc["display"]["cards"][0]
    card["pieces"] = [{"source": {"batch_id": "batch"}}]
    db["mezan_preparation_batches_v2"].rows.append({"user_id": "merchant", "id": "batch", "lines": [{
        "selected_image_url": card["selected_image_url"], "resolved_image_url": "https://cdn.salla.sa/different-option.png",
        "image_b64": base64.b64encode(png()).decode()}]})
    assert await images.prepare_invoice_images(db, "merchant", doc) == {}
    assert calls == [card["selected_image_url"]]


@pytest.mark.asyncio
async def test_deleted_mezan_upload_not_reused_from_cache(monkeypatch):
    async def handler(request): raise AssertionError("network forbidden")
    install_transport(monkeypatch, handler)
    db = DB(); doc = invoice(); url = images.MEZAN_PREFIX + "image"
    doc["display"]["cards"][0]["selected_image_url"] = url
    asset = {"user_id": "merchant", "id": "image", "data_base64": base64.b64encode(png()).decode()}
    db["order_review_mezan_images"].rows.append(asset)
    assert url in await images.prepare_invoice_images(db, "merchant", doc)
    asset["deleted_at"] = "synthetic"
    assert await images.prepare_invoice_images(db, "merchant", doc) == {}


@pytest.mark.asyncio
async def test_success_cache_survives_fresh_database_client(env, monkeypatch):
    from motor.motor_asyncio import AsyncIOMotorClient
    from test_supplier_native_invoice_v2 import OWNER
    import os
    db, _ = env; doc = invoice(10, True); calls = []
    async def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, content=png(), headers={"content-type": "image/png"})
    install_transport(monkeypatch, handler)
    assert len(await images.prepare_invoice_images(db, OWNER, doc)) == 1
    assert len(calls) == 1
    # No Python memory cache exists. A separate Motor client reads persisted bytes.
    fresh = AsyncIOMotorClient(os.environ["MZ2_TEST_MONGO_URI"])
    try:
        assert len(await images.prepare_invoice_images(fresh[db.name], OWNER, doc)) == 1
        assert len(calls) == 1
        assert await db[images.CACHE].count_documents({}) == 1
    finally:
        fresh.close()

def router_index_gate(db):
    import inspect
    import supplier_receiving_routes as routes
    async def current(): return {}
    router = routes.make_supplier_receiving_router(db, current)
    endpoint = next(route.endpoint for route in router.routes if route.path.endswith("/catalog"))
    return inspect.getclosurevars(endpoint).nonlocals["ensure_indexes_once"]


@pytest.mark.asyncio
async def test_index_initialization_serializes_and_retries_failure(monkeypatch):
    import supplier_receiving_routes as routes
    calls = []; fail = True
    async def initialize(db):
        calls.append(db)
        await asyncio.sleep(.01)
        if fail: raise RuntimeError("synthetic index failure")
    monkeypatch.setattr(routes, "ensure_supplier_receiving_indexes", initialize)
    db = object(); gate = router_index_gate(db)
    with pytest.raises(RuntimeError, match="index failure"):
        await gate()
    fail = False
    await asyncio.gather(gate(), gate(), gate())
    assert calls == [db, db]
    await gate()
    assert calls == [db, db]
    other = object(); await router_index_gate(other)()
    assert calls == [db, db, other]
