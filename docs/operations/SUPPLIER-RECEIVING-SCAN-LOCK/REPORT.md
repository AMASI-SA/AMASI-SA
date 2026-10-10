# Supplier receiving scan lock repair

Source inspected first: production branch `hotfix/prod-snap-meta-final`,
`094d0ef7fa001d3b3794f2600c77795190ddb1a8`. Work is isolated from the user's
unrelated accounting edits. No production write, release lease, merge, deploy,
restart, APK or OTA was performed.

## Proven defects and changes

On the unmodified production source, two real-replica regression tests failed:

- `test_same_piece_recovery_releases_lock_for_next_piece`: a successful human
  re-scan returned early while retaining `scan_lock_token`.
- `test_cancelled_request_after_lock_does_not_strand_session`: `CancelledError`
  bypassed `except Exception`, leaving the lock present.

Additional source defects: normal execution released the lock before receipt
events were saved; compensating failure cleanup did not constrain its session
update to the originating token; expired ownership could fall through and keep
writing. These are separate mechanisms, not a claim that every field incident
had the same cause. No production incident trace was available.

The new durable request document fences all writers. Receipt writes and event
proof commit atomically. Every internal exit releases only its own token;
aborted transactions leave no partial piece/event writes. Unknown commit results
are read/fenced, never undone. A startup reconciler settles abandoned requests.
Exact same-session alias recovery, explicit cancellation, historical finalized
receipts and multi-piece partial removal all preserve physical identity.

Whole-session cancellation now runs atomically. The existing transactional
receipt reader rejects pending attempts before refreshing approval quantities
and services. The approved `close_session` implementation and financial helper
files/checksum tests remain unchanged.

Files: `backend/supplier_receiving_routes.py`, new
`backend/supplier_scan_attempts.py`, two new regression suites, existing scan CI
workflow, benchmark and cross-repository HTTP verification scripts and this API/report directory. Android is a separate
repository/PR; see [API.md](API.md).

## Evidence and acceptance

All Mongo tests use unique disposable databases on a dedicated loopback MongoDB
8.0.12 replica set (`127.0.0.1:27129`, `supplierScan`). Provider writes are absent;
authentication/instruction boundaries are synthetic in the scan fixture.

| Requirement | Evidence |
| --- | --- |
| 10 / 50 sequential pieces | Exact event, piece and counter cardinality; delayed midpoint with HTTP caller timeout and GET recovery |
| Re-scan releases lock | Original regression now passes; next piece succeeds |
| Cancellation / partial-write interruption | Actual asyncio cancellation before/after piece writes; transaction rollback and released lease |
| Lost successful commit acknowledgement | Injected error after actual transaction commit; GET confirms unchanged receipt |
| Stale worker | Replace token during paused execution; old transaction cannot write or clear replacement token |
| Crash / process recovery | Failed cleanup retained in durable journal; expiry sweep and application lifespan settle it |
| Concurrency / employee authorization | Competing attempt rejected, other employee denied, first remains protected |
| Cancel pending session | Refused until settlement, then atomic cancellation without orphan pieces |
| Financial approval | HTTP scan → pending barrier → exact products/services approval → idempotent repeat, one invoice/two ledger legs |
| Never-arrived POST | Deadline GET remains read-only; late POST and late admission commit cannot receive |
| Android lifecycle | Actual queue/repository → local HTTP → real Mongo: 50 captures, midpoint lost commit ACK, queue reconstruction and GET-only recovery; actual device reopening/restart remains unverified |

Initial broad regression run: **145 passed, 0 skipped**, including native invoice,
financial integrity, source checksum and real Mongo refresh suites. Final
exact-HEAD test command/results are recorded in the Draft PR after the final
source commit. No financial test was changed. Known warnings are FastAPI's
existing-style `on_event` deprecation, not failed lifecycle execution.

Reproduction/final command from `backend` (all URI variables use the loopback
replica set above; `PYTHONPATH=.:tests` on Linux, `.;tests` on Windows):

```text
python -m pytest tests/test_supplier_scan_lock.py tests/test_supplier_scan_approval.py tests/test_supplier_scan_recovery.py tests/test_supplier_receiving.py tests/test_supplier_invoice_financial_integrity.py tests/test_supplier_multi_supplier_lifecycle.py tests/test_supplier_invoice_live_cost_session.py tests/test_supplier_invoice_service_eligibility.py tests/test_supplier_native_invoice_v2.py tests/test_supplier_display_financial_boundary.py tests/test_supplier_refresh_real_mongo.py -q --tb=short
```

Set `BUILD20_SCAN_TEST_MONGO_URL`, `BUILD20_INVOICE_TEST_MONGO_URL`,
`MZ2_TEST_MONGO_URI` and `BUILD37_TEST_MONGO_URI` explicitly. The workflow retains
source identity and pytest artifacts and does not deploy.

## Local timings

Same host/replica set, sequential ASGI requests, real Mezan product-price lookup,
synthetic authentication/instruction checks; no internet/provider latency.
Run `scripts/benchmark_supplier_scan.py <checkout/backend>` under
`BUILD20_SCAN_TEST_MONGO_URL`. Baseline checkout is the production SHA above.

| Series | Before median / p95 | After median / p95 | Before / after total |
| --- | --- | --- | --- |
| 10 pieces | 104.43 / 121.12 ms | 91.94 / 109.44 ms | 1118.82 / 990.55 ms |
| 50 pieces | 93.68 / 123.24 ms | 92.90 / 107.83 ms | 4784.49 / 4878.76 ms |

Price lookup median was 4.17 → 4.60 ms in the 50-piece sample. No slow external
step was found on this synthetic workload, so no price cache or financial-path
optimization was introduced. An earlier after-run median was 47.33 ms for 50 pieces, but the final repeated run
was 92.90 ms: host/run variance is material, and no sustained speedup is claimed.
The table uses the final after-run. These are local mechanism measurements, not
production percentiles or a performance SLA. This benchmark uses legacy payloads
without a client deadline; the cross-repository HTTP test exercises the new deadline.

## Overlap and limits

Inspected #1320 head `f810ae8f65080afc99d56d751d7b9eb164c29ee0` and #1321 head
`b99f9fe30a1310dff1dcb807b4c4eab807a87a2f`. #1320 changes image preparation/PDF
threading, timing decorators and index setup; this patch changes scan recovery
and transactions. Its full patch passed `git apply --check` against this working
tree, without applying/merging it. Image/index improvements were not copied,
reverted or replaced. Combined-runtime validation remains the eventual integration
owner's task. #1321 has no edited-file overlap with this repair.

Backend requires a replica set, as existing financial approval already does.
New deadline clients must follow the exact immutable deadline contract. Legacy
sent attempts lacking deadline and durable evidence may remain unresolved.
New journal rows are retained for idempotency; no destructive TTL was added.
Actual Android queue/repository code was also executed against this local HTTP Backend and replica set via `scripts/verify_supplier_mobile_contract.py <mobile-checkout>` (requires uvicorn 0.35.0 plus the mobile TypeScript dependency). It confirmed 50 exact pieces, 50 unique POSTs, storage reconstruction/GET-only recovery after the 26th ACK was discarded, and no invoice side effects. Mobile source: Draft #265, final HEAD `7728769b12d1e4d44b85a702ecdf8563cc6d4e99`.

Mixed old/new live writers and real Android hardware were not tested. This
delivery therefore does **not** claim FINAL PASS for production/device rollout.
