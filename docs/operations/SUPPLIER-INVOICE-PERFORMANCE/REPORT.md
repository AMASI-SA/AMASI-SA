# Supplier invoice performance — Draft review evidence

Scope: supplier receiving and invoice PDF only. Base production source: `d55b1b86bbf24572debc700d7bf462bd40e87b3b`; base tree: `640293e1b9e78587c0d4437a0fabf0f2ba940fbf`. No production request, merge, release intent, deployment, OTA, or financial test write outside a disposable loopback database.

## Proven mechanisms and changes

1. The original async PDF endpoint called the synchronous ReportLab renderer directly. `_product_image` performed `urlopen(timeout=4)` for every displayed row, including duplicates. All these network waits blocked the API event loop; neither a durable image cache nor a whole-invoice deadline existed.
2. Canonical PDF cards already get their selected image from finalized receiving event snapshots (`load_invoice_display`). The endpoint's live Product V2 `main_image` enrichment changed invoice lines, but not the canonical cards which the renderer consumes. This ineffective lookup has been removed; historic product/option selection remains authoritative.
3. New `supplier_invoice_images.py` prepares optional thumbnails asynchronously, with six concurrent hydrations, three seconds for the entire image phase, 5 MiB streamed input, 16 million decoded pixels, 420px JPEG thumbnails and 256 KiB output limits. Only HTTPS Salla CDN domains on port 443 are allowed, without userinfo, redirects, proxy environment inheritance or arbitrary URLs. Optional errors omit the image; they cannot reject or alter the financial invoice.
4. Tenant-scoped local Mezan uploads and the invoice's own preparation-batch thumbnails are preferred. Batch fallback bytes for a different resolved URL are rejected rather than substituting another option's image. Deleted local assets are checked even on a cached reprint. Successful thumbnails are durably upserted to `mezan_supplier_invoice_thumbnails_v1` using a tenant + exact snapshot-URL hash as `_id`; financial collections are not modified. A separate Motor client demonstrates cache reuse without a Python memory cache. Failed images retry on later printing within the same deadline.
5. PDF generation now accepts prepared bytes and runs in Starlette's bounded worker pool. It contains no URL/network loader. Font registration is serialized for concurrent render workers.
6. Real native Mezan 2 save-sequence measurements found 12 `createIndexes` calls on catalog, another 12 on close, and another 12 on close replay. Existing index definitions remain identical. Successful initialization is retained per router/database under an async lock; a failure leaves initialization retryable. Subsequent requests retain all authorization, transactional session reads, financial revalidation, duplicate-invoice protection and unique indexes.
7. Fixed-name stage timings record only stage name and elapsed milliseconds: actor, indexes, session read/events, service catalog, invoice read, integrity read, image preparation and rendering. No identifiers, URLs, request values, errors or customer data are logged by these timers.

## API and client sequence

No route, response shape, financial contract, invoice totals, services, prices, ledger or payable calculation changes.

- `GET /supplier-receiving-v1/catalog`: same response; definitions ensured once per router/database.
- `POST /supplier-receiving-v1/sessions/{id}/refresh`: unchanged mutation/revalidation contract.
- `GET /supplier-receiving-v1/sessions/{id}`: unchanged ownership/readback contract.
- `POST /supplier-receiving-v1/sessions/{id}/close`: same atomic Mezan 2 posting and verified readback; repeated successful index DDL removed.
- `GET /supplier-receiving-v1/invoices/{id}`: same authenticated integrity-verified read.
- `GET /supplier-receiving-v1/invoices/{id}/pdf`: before: authorize → verify financial source → ineffective live catalog enrichment → canonical projection → synchronous downloads + PDF on event loop. After: authorize → same financial verification → canonical projection → bounded optional image preparation/cache → worker-thread PDF. Content type/disposition and layout remain compatible.

Android was inspected only, never edited. `AMASI-SA/AMASI-SA` does not contain `SupplierReceivingNative.tsx`. Read-only local Android references were `amasi-mobile` at `ce94b5295b745c1a43b1fb74486d6e5a79bc1da2` and the newer guarded recovery source `supplier-invoice-session-recovery` at `06725dbc0460e07eaa4d7213e1ada9cd40772133`. The latter screen's confirmed save reloads catalog, refreshes the session, reads the session before close, posts close once, and reads the persisted invoice. Ambiguous POST outcomes use session + invoice recovery reads. Sharing subsequently requests the PDF. This is source inspection, not proof of the installed Android version. Those safety reads and fresh employee permission checks were not removed.

## Measured comparison

Measurements are actual local executions of the pinned baseline source and current source. They are not Production measurements and not a promise of deployed p95. Host scheduling and test setup affect wall times.

`pdf-benchmark.json` runs the real baseline/current PDF renderers with a simulated CDN that actually waits 20ms per request; cache storage in this benchmark is in-memory. Separate tests verify the real durable Mongo cache. Representative milliseconds:

| Products | Images | Before | After | Reprint | Network requests before / after / reprint |
|---|---|---:|---:|---:|---|
| 1 | distinct | 96.49 | 103.42 | 35.72 | 1 / 1 / 0 |
| 10 | distinct | 267.01 | 120.63 | 43.44 | 10 / 10 / 0 |
| 50 | distinct | 1727.55 | 489.10 | 182.83 | 50 / 50 / 0 |
| 50 | repeated | 1240.24 | 184.52 | 180.23 | 50 / 1 / 0 |
| 50 | repeated unavailable | 1718.08 | 138.80 | 151.29 | 50 / 1 / 1 |

One-product cold printing has additional async/client/cache overhead in this run; it is not claimed faster. The event loop no longer waits on synchronous network or rendering. A separate test holds each image response for 20 seconds and verifies the actual three-second whole-image budget, cancellation of six in-flight requests, a responsive 20ms event-loop heartbeat, and a valid invoice PDF without photos.

`save-profile.txt` runs actual ASGI requests and native Mezan 2 transaction posting against a disposable Mongo replica set, with existing live cost wrappers installed and no simulated DB latency. Stages below are before → after, milliseconds:

| Pieces | Catalog | Refresh | Session before | Close | Session after | Invoice read |
|---:|---|---|---|---|---|---|
| 1 | 1172.17 → 1048.32 | 55.78 → 33.02 | 40.92 → 33.41 | 212.11 → 193.05 | 12.13 → 16.58 | 44.03 → 30.96 |
| 10 | 1153.64 → 1070.90 | 53.13 → 67.63 | 45.38 → 43.66 | 177.10 → 183.43 | 12.56 → 10.53 | 34.87 → 46.06 |
| 50 | 1136.40 → 1114.66 | 100.57 → 118.63 | 46.29 → 47.04 | 296.72 → 339.64 | 16.42 → 13.65 | 37.13 → 39.23 |

Do not interpret the noisy save wall times as a demonstrated total-save speedup: two after-close samples were slower. The proven reduction is repeated DDL: 36 → 12 commands including replay, with baseline close spending 16.5–19.7ms in repeated index checks and after-close spending zero. Cold catalog initialization still creates all indexes (~0.9–1.0s locally). No other permission/session/financial check was proven safely redundant, so none was removed.

## Verification

PowerShell environment (all synthetic Mongo writes remain loopback):

```powershell
$env:PYTHONPATH='backend;backend/tests'
$env:MZ2_TEST_MONGO_URI='mongodb://127.0.0.1:27128/?replicaSet=supplierPerf'
$env:BUILD20_INVOICE_TEST_MONGO_URL=$env:MZ2_TEST_MONGO_URI
C:/Users/amasi/build37-test-venv/Scripts/python.exe -m pytest backend/tests/test_supplier_invoice_images.py backend/tests/test_supplier_invoice_financial_integrity.py backend/tests/test_supplier_invoice_display_readonly.py backend/tests/test_supplier_invoice_display_projection.py backend/tests/test_supplier_native_invoice_v2.py backend/tests/test_supplier_receiving.py backend/tests/test_supplier_invoice_save_profile.py -q --junitxml=docs/operations/SUPPLIER-INVOICE-PERFORMANCE/pytest.xml
```

Result: **162 passed, zero skipped, exit 0**, 111.67 seconds. This includes native totals, services, posting/payables, authorization and ownership, financial-source corruption rejection, transaction rollback, close replay and duplicate protections; 1/10/50 products, repeated/missing images, cache persistence, selected-option image source, deleted uploads, streamed/pixel limits, no network during rendering and off-loop rendering. Index initialization additionally exercises failed-first setup, concurrent initialization and independent database/router state.

Independent coordinator verification after the final runtime changes: the same complete command passed **162 tests, zero skipped, exit 0**, in **126.16 seconds**. This fresh run includes the final stream-chunk limit and font-registration lock.

An earlier run without `MZ2_TEST_MONGO_URI` had 123 passed/26 skipped; it is superseded by the fully configured run. The first image unit run had two failures because optional image work timed out under host load before the mocked download. Security/validation tests now explicitly use a generous 30-second test-only budget; a separate test exercises the production three-second budget against 20-second delayed responses. This separates validation assertions from the optional deadline contract.

Reproduce performance:

```powershell
C:/Users/amasi/build37-test-venv/Scripts/python.exe scripts/benchmark_supplier_invoice_pdf.py
C:/Users/amasi/build37-test-venv/Scripts/python.exe -m pytest backend/tests/test_supplier_invoice_save_profile.py -q -s
```

The existing native invoice CI workflow now runs the new image and save-profile tests and fetches history to load the pinned baseline. No deployment workflow or release metadata was changed.

## Review limits and handoff

No Production/Android device latency, visual printer hardware behavior, or live Salla request was measured. Missing/corrupt/unavailable images intentionally produce a valid PDF without that optional photo; unprepared later images can be omitted when the whole-image deadline expires. The cache has no eviction policy (separate tenant-scoped collection; one bounded thumbnail per distinct snapshot URL). Cache initialization or storage failures may omit a photo but do not affect financial approval. No accounting or integrity module business logic changed.

The intended next action is owner review of a Draft PR and its CI. Merge/deployment/OTA/release intent are not authorized by this task.

## Draft CI follow-up

The first Supplier Invoice Display Grouping run (`38081596265`, job `114299463581`) produced **142 passed / 1 failed**. The failure was its pre-existing source-checksum guard for `close_session`: replacing the repeated index DDL call with `ensure_indexes_once()` changed the source hash despite unchanged financial code. This omission was reproduced locally before the fix.

The guard now normalizes exactly one literal index-initialization call before checking the **original** approved financial function hashes. It asserts exactly one replacement and absence of the old call; all original function/file expected hashes remain unchanged. No authorization, posting, session linkage, totals, transaction or integrity code is normalized. Dedicated tests separately cover index initialization failures, retries and concurrency. This follow-up changes only the test/evidence, not runtime behavior.

The coordinator rechecked Production at `094d0ef7fa001d3b3794f2600c77795190ddb1a8`; the intervening PR #1319 changes TikTok files without overlapping this patch. The published task branch remains based on `d55b1b86bbf24572debc700d7bf462bd40e87b3b`, which also remains the pinned benchmark source; no rebase or force-push is needed.

The coordinator observed the native Supplier V2 workflow succeed on `ecf5b8f6dc8017b4c192eb083d50cb92d2440882`, together with the other workflows except the known display checksum test above. Those CI results precede this test-only follow-up and are not represented as fresh checks of its later commit.


Follow-up local verification: `pytest --noconftest --asyncio-mode=auto -q -ra` over the exact display CI list (`test_supplier_invoice_display_projection`, `test_supplier_invoice_display_readonly`, `test_supplier_display_financial_boundary`, `test_supplier_receiving`, `test_supplier_native_invoice_v2`, `test_supplier_invoice_financial_integrity`, `test_supplier_invoice_private_history`) plus `test_supplier_invoice_save_profile`: **149 passed, zero skipped, exit 0**, 134.28 seconds. JUnit: `display-ci-followup.xml`. Focused financial-boundary + index initialization check: **2 passed, 26 deselected**, exit 0. These use the same loopback Mongo and environment shown above.
