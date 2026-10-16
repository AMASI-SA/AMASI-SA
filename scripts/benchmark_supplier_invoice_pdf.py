"""Offline before/after PDF benchmark against the immutable production baseline.
Run from repository root: python scripts/benchmark_supplier_invoice_pdf.py
Network is simulated with a real 20 ms delay per CDN request, never contacted.
"""
import asyncio
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "backend/tests")]
from test_supplier_invoice_images import DB, invoice, png
import supplier_invoice_images as images
import supplier_invoice_pdf as current
import httpx
from starlette.concurrency import run_in_threadpool

BASELINE = "d55b1b86"
DELAY = .02
source = subprocess.check_output(["git", "show", BASELINE + ":backend/supplier_invoice_pdf.py"], cwd=ROOT, text=True, encoding="utf-8")
spec = importlib.util.spec_from_loader("baseline_supplier_pdf", loader=None)
before = importlib.util.module_from_spec(spec)
before.__file__ = str(ROOT / "backend/supplier_invoice_pdf.py")
exec(compile(source, before.__file__, "exec"), before.__dict__)


class Response(io.BytesIO):
    headers = {"Content-Type": "image/png"}


async def benchmark(count, duplicate, missing):
    doc = invoice(count, duplicate)
    counters = {"before": 0, "after": 0}
    def fetch(*a, **kw):
        counters["before"] += 1
        time.sleep(DELAY)
        if missing: raise TimeoutError("synthetic optional photo unavailable")
        return Response(png())
    async def handler(request):
        counters["after"] += 1
        await asyncio.sleep(DELAY)
        return httpx.Response(404) if missing else httpx.Response(200, content=png(), headers={"content-type": "image/png"})
    # Font registration is excluded equally from repeated route workload.
    before._register_font(); current._register_font()
    with patch.object(before, "urlopen", fetch):
        started = time.perf_counter(); before.generate_supplier_invoice_pdf(doc)
        baseline_ms = (time.perf_counter() - started) * 1000
    client = httpx.AsyncClient; db = DB()
    with patch.object(images.httpx, "AsyncClient", lambda **kw: client(transport=httpx.MockTransport(handler), **kw)):
        started = time.perf_counter(); prepared = await images.prepare_invoice_images(db, "synthetic", doc)
        image_ms = (time.perf_counter() - started) * 1000
        started = time.perf_counter(); await run_in_threadpool(current.generate_supplier_invoice_pdf, doc, images=prepared)
        render_ms = (time.perf_counter() - started) * 1000
        first_calls = counters["after"]
        started = time.perf_counter(); prepared = await images.prepare_invoice_images(db, "synthetic", doc)
        await run_in_threadpool(current.generate_supplier_invoice_pdf, doc, images=prepared)
        reprint_ms = (time.perf_counter() - started) * 1000
    return {"products": count, "duplicate": duplicate, "missing": missing,
            "before_ms": round(baseline_ms, 2), "after_image_ms": round(image_ms, 2),
            "after_render_ms": round(render_ms, 2), "after_ms": round(image_ms + render_ms, 2),
            "reprint_ms": round(reprint_ms, 2), "before_requests": counters["before"],
            "after_requests": first_calls, "reprint_requests": counters["after"] - first_calls}


async def main():
    rows = []
    for count in (1, 10, 50):
        for duplicate, missing in ((False, False), (True, False), (True, True)):
            rows.append(await benchmark(count, duplicate, missing))
    print(json.dumps({"baseline": BASELINE, "scope": "local synthetic CDN delay; real PDF rendering; in-memory cache DB",
                      "cdn_delay_ms": DELAY * 1000, "results": rows}, indent=2))

if __name__ == "__main__": asyncio.run(main())
