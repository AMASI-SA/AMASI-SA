# Operational balances completion — 2026-11-12

## Current state

LOCAL IMPLEMENTATION AND VERIFICATION COMPLETE; RELEASE IN PROGRESS. The owner
explicitly authorized public-repository upload, merge and publication after the
public destination was identified. This is one integrated release task;
no production data, runtime, release lease, merge or publication has changed
through this task.

- Repository: AMASI-SA/AMASI-SA.
- Branch: codex/operational-balances-completion-20261112.
- Base: 06d0002fab257e1976dd2344865da4de77c158c3 (PR1893 merge).
- Local implementation checkpoint: `b2a2d818eba388ab1f94de771e3f87e6db6fb128`.
  All 200 implementation paths are committed. The integrated backend gate and
  supplemental canonical startup test passed on this checkpoint; the following
  verification-record commit changes this document only. This is not a
  published or reviewed v5 A/B release pair.
- Worktree: /workspace/scratch/e00762959313/mezan-operational.
- Reviewed Production integration base J:
  `88589786f4c30ed78a5c9d185fae6ce7786ca9da` (PR1895 merge). The 15 paths added
  or changed by PR1894/PR1895 do not overlap the 200 task paths. Their supplier
  embroidery and dashboard behavior is preserved. The remote source candidate
  must be a descendant of J with its intent unchanged throughout J..A.
- Preserve the complete PR1892 demand-only report behavior and all PR1893 changes.
- Root owns integration, shared routes/service, report core/UI, source-refresh
  diagnosis, acceptance and release. Three bounded contributors own payments,
  shipping evidence and daily financial activity respectively.

## Owner requirements

1. Complete all remaining operational-balance issues in one reviewed release.
2. Recognize an evidenced customer-paid amount using the Salla order source
   behind the dashboard payment totals; missing capture time alone must not erase
   an otherwise established paid amount. Provider payout to a bank is a separate
   settlement. Do not fabricate a timestamp or duplicate an opening/capture.
3. Show each provider/carrier/driver's established amounts even when individual
   orders are unresolved. State partial coverage alongside those amounts.
4. Show counted deliveries and a count of distinct unresolved orders. Clicking
   that count opens a modal listing order number and concrete reason(s). Multiple
   issues for one order count once; unidentified owners are not guessed.
5. Isolate completeness per source/table/party, including actual noncash review
   facts, rather than propagating an unrelated issue to every table.
6. Complete shipping-charge recognition from established completion evidence
   after the configured start, preserving carrier/tariff identity, prior charges,
   separate COD/cash evidence, and reviewed historical impact.
7. Complete daily movement filters, ten-row pagination, actor/account/party,
   after-balances and permission filtering with bounded reads/calculation.
8. Complete read-only forecast/profit computations with explicit evidence scope,
   matching period and no invented zero for missing inputs.
9. The owner's latest instruction is strict demand-only operation: no background
   balance worker, scheduled refresh, hidden-page loading or polling. Calculate
   only for a visible page request; cancel reads/calculation when it leaves.
   Keep one refresh per owner across processes and preserve completed snapshots.

## Live observations before implementation

- Financial-position page displayed start 2026-11-01 00:30 Asia/Riyadh.
- Latest successful calculation displayed 2026-11-12 01:07 Asia/Riyadh while a
  later report generation displayed 18:32. A source-refresh failure warning was
  visible. Expanding the saved page's diagnostics confirmed
  `operational_capacity_requires_archive`; a collection row limit was not proven.
- Previous implementation published in PR1892 removed UI polling and reused the
  COD allocation index. It did not change background refresh cadence, shipping
  eligibility, profit/forecast placeholders or daily activity.
- Existing PR1846 and PR1847 are unfinished reference implementations. Do not
  merge them wholesale or reuse an old release intent.
- PR1893 was merged while this review continued. Current work starts above it.

## Verification contract

- Evidence-based amounts, same-owner identities, currencies and existing opening
  balances/settlements must remain correct and idempotent.
- Prove paid/full/partial/split/refunded and missing-time cases; unsettled money
  must not enter bank cash merely because an order is paid.
- Prove known carrier/driver amounts survive unresolved orders, distinct order
  counters and modal row/reason correctness, with cross-owner denials.
- Prove shipping charge/event/time/tariff and historical-review boundaries.
- Prove daily activity pagination/filters/after-balances and limited account
  access before counts or totals are computed.
- Prove complete and incomplete forecast/profit cohorts and no double-counting.
- Prove source-refresh recovery diagnostics and no recurring report polling.
- Run existing relevant suites and independent integrated review before release.

## Implemented behavior

- The scheduled operational worker is removed from runtime registration, and
  its legacy entry points perform no work. The visible POST refresh owns its
  reads, cancellation, short distributed lease and final atomic save.
- There is no polling, hidden-page preload, focus refresh after a completed
  read, scheduled acquisition or second reconciliation loop. A cancelled read
  may resume only when the user returns to the visible page. A completed read
  remains completed until another explicit action.
- Reports, daily activity, supplier workspaces, allocation choices, supplier
  invoice/native purchase previews and completion-fee previews own their server
  work and join cancelled CPU/source tasks. Explicit approvals and financial
  writes do not carry the read AbortSignal; an atomic commit already started is
  observed to completion before its request releases ownership.
- The same lifetime now covers historical shipping/platform reviews, salary
  and opening-inventory previews, inventory/catalog/invoice reads, customer
  panels, account statements, old movement feeds and audit/context reads.
  The final child-panel audit added 27 read-only route lifetimes. Small ad
  settings dialogs and supplier-adjustment lookups also defer hidden reads.
- Packed snapshot/issue decoding and audit-page validation run outside the
  event loop in request-owned preparation. The request joins them on departure.
  Visible refresh also owns its large-state copy, comparison and storage plan;
  cancellation before commit admission cannot persist a candidate snapshot.
- Source conversion cooperatively yields within the existing CPU budget, and
  its payroll/ad/recurring preparation is cancellable and joined. A real parser
  regression previously consumed all 20,000 synthetic orders after a disconnect
  at order ten; it now stops early. Separate deadline regressions prove HTTP
  408, stopped preparation before lease release, and no remaining tasks. A
  preparation helper also owns a stop event when no read scope was inherited.
- Missing native bank, courier and paid-SAR evidence is acquired under the
  visible request's lease, at most four candidates per source within a shared
  25-second acquisition budget, then one reconciliation. Newly discovered gaps
  remain disclosed until a later user refresh; no background retry is queued.
- Financial snapshot maps use a lossless owner-bound BSON packet under the same
  revision CAS when the 12 MiB guard is exceeded. Opening balances, movements,
  idempotency records, salary guard indexes and audit history remain intact.
- Evidenced full/partial paid amounts, known provider subtotals, unique pending
  order details, per-party shipping coverage, reviewed completion recognition,
  scoped daily activity and saved profit/forecast sources are implemented.
- Paid-order counts are separate from recognition/fee-complete claim counts.
  A proved unpaid order is not an unidentified exception. Pending-only parties
  remain visible with unknown amounts, and one order with multiple reasons is
  counted once. The modal lists all available order identities/reasons, with
  search and ten-row pagination; the export preserves those details.
- Unproved fees cannot become zero fees or a fully known provider net. A missing
  wallet statement cannot become a zero liability or an invented foreign wallet
  ledger. Known amounts remain separately available.
- COD source cohorts are reconciled by identity: missing canonical sources and
  uncovered current waybills block a complete total without erasing known rows;
  a covered order is not also counted as missing in another cohort.

## Local verification

- Pinned Node 22.23.2 / Yarn 1.22.22 toolchain verified.
- Frontend consolidated run: **640 passed, 47 suites passed**, zero failures.
  Covers all operational UI suites plus suppliers, employees and navigation.
- Earlier shared payment/shipping compatibility run: **919 passed, 168 subtests passed**;
  one intentionally skipped memory-only transaction-rollback case, with the
  corresponding replica-set cases enabled. Uses the existing auto asyncio
  fixture mode and a disposable Mongo 7.0.16 replica set.
- Final consolidated backend run: **3585 passed**, exit **0**, in 331.46 seconds.
  It covers the balance/shipping suites and affected report, inventory
  eligibility, background-worker and noncash review consumers. Its single
  skipped case needed `QOYOD_TEST_MONGO_URI`; that exact canonical worker startup
  case then passed separately, exit **0**, in 13.66 seconds. Thus all **3586**
  selected backend cases have passed; there is no outstanding skipped case in
  this selected scope.
- The canonical startup case boots the actual child runtime against an empty
  isolated database, proves `operational-balances` is absent from registered
  jobs, and drains the child successfully. Initially missing local runtime
  packages were installed at the existing repository pins (`xlsxwriter`,
  `openai`, `webauthn`); no dependency file or application source was altered.
- Mongo 7.0.16 used a disposable `operationalphysical` replica on loopback
  port 27462, with all test URIs pointing to that process. A 1 GiB WiredTiger
  test cache supported retained large legacy snapshots. The earlier 0.25 GiB
  fixture failed during seeding before the source under test; the corrected
  fixture and final capacity/snapshot cases passed.
- Additional request-lifetime regressions cover actual ASGI disconnection,
  no financial writes, cancellation during packed-state preparation, and
  inventory CPU cleanup before its snapshot session closes. Independent focused
  runs passed and the final consolidated gate includes these regressions.
- The actual financial-report/dialog component fixture builds successfully with
  pinned Node. Its generated data uses three priced deliveries and seven unique
  unresolved orders. Browser execution is **not verified here**: local Chromium
  exits before opening a page because this environment denies its Unix socket.
  No browser assertion or screenshot is claimed from that failed launch.
- Independent reviews covered source/party completeness, worker removal and
  live UI consumers, request cancellation, wallet unknowns, provider counts,
  snapshot packet integrity and the driver bank/POS writer. Concrete findings
  were fixed and checked before this source freeze.
- Final source/test manifest verification covered 199 files with zero hash
  mismatches after the backend gate. The working source remains the tested
  implementation checkpoint; only this verification record follows it.
- Earlier intermediate failures were addressed, including the daily query
  parameter type, unrelated fact payload parsing, unknown wallet totals,
  provider pending counts and visibility/cancellation gaps. Final results above
  supersede intermediate counts; exact-source CI remains a separate gate.
- CLI previews distinguish a partial known provider net from a complete net.
  A pure reducer result without a service-stamped snapshot timestamp is also
  explicitly incomplete. Mobile history retains the account ACL and redacted
  display contract; granted read accounts never confer write permission.

### Reproduction scope

The local disposable runner supplied `OPERATIONAL_TEST_MONGO_URI`,
`MZ2_TEST_MONGO_URI`, `BUILD37_TEST_MONGO_URI` and (for the supplemental startup
case) `QOYOD_TEST_MONGO_URI`. Each suite creates and drops its own synthetic
test database. No application database configuration or production fallback is
used. The backend invocation used `PYTHONPATH=backend:backend/tests` and:

```bash
python -m pytest -q \
  backend/tests/test_operational_balance*.py \
  backend/tests/test_operational_completed_shipping*.py \
  backend/tests/test_operational_driver_review_mongo.py \
  backend/tests/test_operational_driver_review_packed_state.py \
  backend/tests/test_operational_financial_review_groups.py \
  backend/tests/test_operational_read_runtime.py \
  backend/tests/test_operational_open_cost_exposure.py \
  backend/tests/test_operational_bundle_cost_projection.py \
  backend/tests/test_operational_supplier_invoice_cutover.py \
  backend/tests/test_operational_inventory_projection_eligibility.py \
  backend/tests/test_background_worker_process.py \
  backend/tests/test_store_delivery_noncash_review.py \
  --asyncio-mode=auto --tb=short -rs
```

Frontend verification used the pinned Node binary, React Scripts Jest,
`--watchAll=false --runInBand`, all `src/pages/operational` suites, suppliers,
employees, supplier services and navigation shell tests. The existing local
Python environment is not a claim of full production dependency-lock parity;
the repository's exact-source CI remains a separate release gate.

## Evidence semantics

The report distinguishes known amounts from complete totals. Missing source
evidence, fees, wallet statements and historical shipping approval are not
fabricated. Daily-only expense sources do not prove partial-day allocation;
financing and income-tax inputs with no available source contract remain
explicitly unknown. A partial known subtotal is not certified as full net profit.

## Publication authorization

Earlier automatic approval review required explicit authorization to disclose
the task source and operational documentation to the verified public repository
`https://github.com/AMASI-SA/AMASI-SA.git`. The owner answered the concrete
destination-specific request with "ارفع وادمج وانشر". Upload, merge and governed
publication are now authorized. The previous authorization blocker is resolved.
The task PR will carry the exact remote candidate, CI, intent and deployment
identities without changing a frozen source/intent pair.

## Pending

Real-browser acceptance, exact-source CI and the repository's v5 release gates
remain pending. Source implementation, local
review and selected regression verification are complete. Production code and
financial data have not been changed by this task.

## Continuation

Preserve an authenticated remote checkpoint and verify its complete source tree
before continuing release work. Issue1006 previously reached GitHub's comment
limit; preserve a verified task PR and this document as the continuation record.
Do not change another publisher's /app checkout or
lease. Before an authorized publication, re-read current production and shared
guard, rebuild a new immutable source/intent pair if production advanced, and
verify the published identity three times using the official guard.
