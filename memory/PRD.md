# PRD — MEZAN E-commerce Accounting App

## Root Cause Found — Qoyod Requires BOTH `name` + `contact_name` (2026-02-26)
**User report**: Order #268316484 had `customer_name = "هيفاء الحيدر الشمري"` in the raw payload but failed at `FAILED_CUSTOMER` with Qoyod returning `contact_name: ["Can't be blank"]`.

### Root cause
Qoyod's `POST /customers` endpoint requires TWO required fields:
- `name` — business/account name
- **`contact_name`** — contact person (this was missing in our payload)

Verified via web search of Qoyod API docs: our payload only sent `name`, so `contact_name` defaulted to empty and Qoyod rejected. The customer-name fallback I added earlier worked correctly all the way through to the DTO — the bug was at the LAST mile inside the Qoyod payload builder.

### Fix (`customer_resolver.py::_build_contact_payload`)
```python
safe_name = (customer.name or "").strip() or _safe_guest_name(customer)
payload = {
    "name":         safe_name,
    "contact_name": safe_name,   # ← was missing
    ...
}
```
Both fields are always populated with the same safe-name string for B2C orders (verified the field has been missing since Day-1; this was a latent bug exposed by today's order).

### Forensic logging added
`ResolutionResult.qoyod_request_payload` now carries the EXACT payload sent to `POST /customers`, populated on BOTH success and failure paths. This propagates to the inbox row via `to_log_dict()` and is surfaced on the First-Sync Monitor under the "إنشاء/مطابقة العميل" step. Operator can now see the exact body without rerunning the order.

### Defense-in-depth in legacy adapter
`legacy_adapter.py` now ALSO writes `customer.full_name` (in addition to `first_name + last_name`) so the normalizer's fallback chain (full_name → name → phone → guest) still has the original `customer_name` string if `_split_name` ever returns empty parts (single-character names, RTL marks, etc.).

### Tests
- **`tests/test_qoyod_contact_name_end_to_end.py`** (new, 9 tests, all pass):
  1. Direct payload-builder: both `name` AND `contact_name` populated.
  2. Blank DTO: both fallback to safe guest label — never blank.
  3. Email-only DTO: contact_name uses email label.
  4. Empty DTO: contact_name is literal "ضيف".
  5. **End-to-end repro of user's order**: Make payload → adapter → normalizer → builder. Asserts the final Qoyod payload has `contact_name: "هيفاء الحيدر الشمري"`.
  6. Single-character name: `full_name` defense in adapter saves the day.
  7. `resolve_customer` returns the payload snapshot on success.
  8. `resolve_customer` returns the payload snapshot on failure.
  9. `to_log_dict` propagates the snapshot to the inbox row.
- **Full Qoyod suite: 422 tests pass** (no regressions).



## Final Pre-Go-Live Fixes — Customer Name Fallback + Arabic COD Aliases (2026-02-26)

### Issue 1: `contact.name Can't be blank` from Qoyod
**Root cause**: `_normalize_customer` fell back to literal "ضيف" but the resulting DTO could still produce a blank name in edge cases (e.g. customer payload missing first/last/full name and the normalizer didn't have order context).

**Fix (`integrations/qoyod/normalizer.py::_normalize_customer`)**: now takes optional `order_number` arg and falls back in this strict order:
1. `first_name + last_name`
2. `full_name`
3. `name`
4. `"عميل {phone}"` (so the customer is recognizable in Qoyod)
5. `"ضيف #{order_number}"` (distinguishable guest per order)
6. `"ضيف"` (last-resort literal)

**Belt-and-suspenders (`customer_resolver.py::_build_contact_payload`)**: even if the DTO somehow has a blank name (legacy rows), the payload builder NEVER sends blank — uses phone/email as label or last-resort "ضيف".

### Issue 2: COD Arabic variant `النوع عند الاستلام` unmapped
**Root cause**: `_canonical_payment_method` had no Arabic entries. Salla sent the Arabic native string and it was canonicalized to `النوع_عند_الاستلام` (the user saw this raw value in the Settings page).

**Fix (`integrations/qoyod/normalizer.py`)**: Arabic COD variants now canonicalize to `cod` at write-time:
- `الدفع عند الاستلام` → `cod`
- `النوع عند الاستلام` → `cod`
- `الدفع نقدا عند الاستلام` → `cod`
- `نقد عند الاستلام` → `cod`
- Plus `cash` (was previously `cash`, now `cod` for consistency)
- Plus Arabic provider names: `تمارا → tamara`, `تابي → tabby`, `تحويل بنكي → bank_transfer`.

**Fix (`payment_methods.py`)**: `PAYMENT_METHOD_ALIASES` also covers already-underscored Arabic keys (legacy rows written before the normalizer extension). E.g. `الدفع_عند_الاستلام → cod`.

### Frontend (`pages/QoyodSettings.jsx`)
Payment-method rows now also render a slate badge **"من سلة: «<native>»"** when the original Salla string differs from the canonical key. This shows the user EXACTLY what Salla sent, so they can never wonder where a row came from.

### Tests
- **`tests/test_qoyod_customer_name_and_cod.py`** (new, 27 tests, all pass):
  - 9 tests for the customer-name fallback chain (first+last priority, full_name, name field, phone label, guest with order number, bare guest, string customer payloads).
  - 4 tests for `_build_contact_payload` belt-and-suspenders.
  - 8 parametrized tests for COD canonical resolution (English + 5 Arabic variants).
  - 4 parametrized tests for other Arabic provider names.
  - 2 alias-table coverage tests for already-normalised Arabic keys.
- **Full Qoyod suite: 413 tests pass** (no regressions).

### Verified end-to-end live
- Seeded an inbox row with `payment_method_native: "النوع عند الاستلام"`, `payment_method: "cod"`.
- `/payment-methods/used` returned `key: "cod"`, `label_ar: "الدفع عند الاستلام"`, `native_examples: ["النوع عند الاستلام"]`.
- Settings UI rendered: title "الدفع عند الاستلام", badge "من سلة: «النوع عند الاستلام»", mapping single account ID once covers ALL Arabic + English COD variants.



## Outstanding-Failures Watermark Fix (2026-02-26, final)
**User-reported bug**: QYD-GO blocked with "27 فاتورة إنتاجية فشلت" but the user had never activated Go-Live yet — the 27 rows were old test data from before the dry_run flag existed (or before it was reliably set per-row).

**Root cause**: My previous fix used `dry_run: {$ne: True}` as the production filter. Legacy rows from before the worker fix had **no `dry_run` field at all**, so they passed the `$ne True` test and got counted as production failures.

**User spec**:
1. Pre-Go-Live (`go_live_activated_at` not set) — check ALWAYS passes; old test rows can never block first-time activation.
2. Post-Go-Live — count only rows with `received_at ≥ go_live_activated_at` AND `dry_run != True`.
3. Legacy rows without `dry_run` flag created before activation are by definition pre-activation noise.

### Backend changes
- **`integrations/qoyod/go_live.py::_check_outstanding_failures`**:
  - Reads `settings.go_live_activated_at` (with fallback to legacy `activated_at`).
  - Parses ISO-string timestamps as a defensive fallback.
  - **If unset → always returns `ok: True`** with detail: "لم يتم تفعيل الإنتاج بعد. السجلات القديمة (ما قبل التفعيل) لا تُحسب كفشل إنتاجي."
  - If set → counts only `pipeline_stage ∈ {DEAD_LETTER, PARTIAL_FAILURE} AND dry_run != True AND received_at ≥ activated_at`.
- **`activate_go_live`**: writes BOTH `go_live_activated_at` (new canonical) and `activated_at` (legacy compat) on flip.
- **Function signature** updated to take optional `settings` param (passed from checklist to avoid double DB load).

### Tests
- **`tests/test_qoyod_go_live_qyd_fix.py`** — rewrote outstanding_failures tests to use the watermark spec:
  - Pre-Go-Live always passes (even with 27 missing-dry_run rows — the exact user scenario).
  - Post-Go-Live: pre-activation rows ignored, post-activation dry-run ignored, post-activation production COUNTED.
  - Legacy `activated_at` field honoured (backward compat).
  - ISO-string activation timestamp parsed correctly.
- **`tests/test_qoyod_qydgo_clear_failures_persistence.py`** — rewrote on real Mongo (5 tests):
  - Pre-Go-Live with mixed rows (dry/non-dry/missing) all green.
  - Post-Go-Live pre-activation rows ignored.
  - Post-Go-Live post-activation production counts.
  - Refresh stability ×5 stays green pre-activation.
  - Legacy `activated_at` field works.

### Verification
- ✅ **18 watermark tests pass**; **386 total Qoyod tests pass** (no regressions).
- ✅ Live API: `/go-live/checklist` returns outstanding_failures with `ok: True` + the new pre-activation detail message.
- ✅ Screenshot: QYD-GO page shows green ✓ "لا فواصل إنتاجية عالقة" with the new message. Total progress moved from blocked → 6/11 passed.



## Payment-Method Alias Resolution — Final Pre-Go-Live Fix (2026-02-26)
**User-reported bug**: Order reached `PRODUCT_RESOLVED` then failed at `INVOICE_CREATED` with `payment_method_mapping_missing` because Salla sent `tamara_installment` but only `tamara` was mapped in settings.

**User spec**:
1. No hardcoded provider names — accept anything Salla sends.
2. New methods auto-appear as Settings rows, never as runtime crashes.
3. Built-in alias table: `*_installment` variants collapse to their base provider (`tamara_installment → tamara`, `tabby_installment → tabby`, etc.) so one mapping covers both.
4. User can still explicitly map a variant to a different Qoyod account if needed.

### Backend
- **`integrations/qoyod/payment_methods.py`** (NEW):
  - `PAYMENT_METHOD_ALIASES` table covering Tamara/Tabby/Emkan installment variants, bank/wire, cash/cod, applepay/stcpay typo variants, credit/card.
  - `provider_family(method)` — collapses a variant to its base provider.
  - `resolve_payment_account(settings, method)` — direct match → alias fallback → None.
  - `explain_resolution(settings, method)` — diagnostic for UI/monitor.
- **`integrations/qoyod/invoice_builder.py::_resolve_payment_account`**: delegates to the new resolver.
- **`integrations/qoyod/preflight.py`**: uses `resolve_payment_account`; failure message now surfaces `provider_family` hint so operator sees which base to map.
- **`integrations/qoyod/setup_validation.py`**: a method is "mapped" if direct OR alias resolves. The blocker carries `extra.alias_hints` listing variants that could be solved by mapping a base provider.
- **`integrations/qoyod/go_live.py::_collect_eligible_skus_and_methods`**: same alias-aware unmapped detection.
- **`GET /api/integrations/qoyod/payment-methods/used`**: each row now carries `provider_family`, `mapped_via` ("direct"|"alias"|null), `matched_key`, `resolved_account_id`. The endpoint also returns the full `aliases` table.

### Frontend (`pages/QoyodSettings.jsx`)
- Payment-method mapping table renders each used method as before, but when a row is alias-covered:
  - Sky-blue row background instead of red.
  - "✓ مربوط (Alias)" badge in status column instead of "مطلوب".
  - Inline hint badge "عبر تمارا" next to the method name.
  - Input placeholder shows "(اختياري — يستخدم tamara)".
- Client-side validation mirrors backend: rows with `mapped_via === "alias"` are NOT counted as missing.

### Tests
- **`tests/test_qoyod_payment_method_aliases.py`** (new, 13 tests, all pass):
  - Provider family collapses known aliases, passes through unknown methods, handles empty/None.
  - Resolver: direct match takes priority over alias; falls back to alias; returns None when neither; ignores blank account IDs; case-insensitive + whitespace-tolerant.
  - Preflight passes when variant resolves via alias; fails with helpful family hint when no alias covers.
  - Settings validation treats variants as mapped when alias covers them.
- Full Qoyod suite: **384 tests pass** (no regressions).

### Verified end-to-end
- Seeded a `tamara_installment` inbox row with only `tamara` mapped in settings.
- `/payment-methods/used` returns the row with `mapped_via: "alias"`, `matched_key: "tamara"`, `resolved_account_id: "A-9999"`.
- `/setup/validate` returns `ok: true` — no blocker.
- Settings UI shows sky-blue row with "✓ مربوط (Alias)" badge and "عبر تمارا" hint.
- Bottom save banner: "✅ كل الحقول مكتملة — جاهز للحفظ".



## QYD-GO Dry-Run Awareness + Migration SSOT Clarification (2026-02-26)
**User decisions**:
1. `_check_outstanding_failures` counts ONLY production (`dry_run != True`) failures. Dry-run failures NEVER block Go-Live.
2. Archive flow for dry-run failures stays on the First-Sync Monitor page (already built — "أرشفة فشل الاختبار القديم").
3. Dry-run failures remain visible in the Monitor for diagnosis until archived.
4. Production failures (`dry_run: False`) always block readiness — no exclusion mechanism.
5. The «مرحلة الانتقال» page is review-only; SSOT for products/customers post-Go-Live is **Mezan + Salla**, NOT imported Qoyod data.

### Backend changes
- **`integrations/qoyod/go_live.py`**:
  - `_check_outstanding_failures` filter changed from `excluded_from_checklist: {$ne: True}` → `dry_run: {$ne: True}`.
  - Detail messages updated: green says "لا توجد فواصل إنتاجية عالقة. فشل Dry Run (إن وُجد) معروض في صفحة المراقبة فقط." Red says "X فاتورة إنتاجية فشلت — يجب مراجعتها قبل المتابعة (فشل Dry Run لا يُعيق الجاهزية)."
  - Checklist label changed to "لا فواصل إنتاجية عالقة".
- **`integrations/qoyod/product_resolver.py`** + **`customer_resolver.py`**: docstrings explicitly state Mezan+Salla SSOT; runtime never reads `qoyod_external_*` / `qoyod_migration_*`.
- The `clear-test-failures` endpoint remains for backward compat (no UI uses it anymore) — `excluded_from_checklist` field is no longer consulted by any check.

### Frontend changes
- **`pages/QoyodGoLive.jsx`**: removed the obsolete "🗑️ تنظيف فشل الاختبار" button + its handler + state. The button could have masked real production failures and is now dangerous.
- **`pages/QoyodMigration.jsx`**: added blue notice banner explaining the page is review-only and Mezan+Salla are SSOT.

### Tests
- **`tests/test_qoyod_go_live_qyd_fix.py`**: rewrote the 2 outstanding-failures tests + added a "missing dry_run treated as production" defensive test (3 new tests).
- **`tests/test_qoyod_qydgo_clear_failures_persistence.py`**: rewritten end-to-end on real Mongo (5 tests):
  - Dry-run failures alone never block.
  - Production failures always block.
  - Mixed: only production count.
  - Refresh stability ×3.
  - Worker-produced new dry failures still don't block.
- **`tests/test_qoyod_runtime_ssot_isolation.py`** (new, 1 test): tokenises every runtime module and fails if any of them references `qoyod_external_*` / `qoyod_migration_*` collections in executable code (docstrings allowed). CI guardrail against future drift.

### Verification
- 35 tests pass (all Qoyod-touched).
- Screenshot of QYD-GO confirms: green "لا فواصل إنتاجية عالقة" with new detail, NO clear-test-failures button visible.
- Screenshot of «مرحلة الانتقال» confirms SSOT notice banner renders correctly.

### Architectural confirmation
A grep across `/app/backend/integrations/qoyod/` (excluding `migration.py` + `migration_routes.py`) shows **zero references** to the 4 migration collections. The runtime pipeline only reads/writes `qoyod_products_mapping` + `qoyod_customers_mapping`, which are populated on-demand by the resolvers themselves from Salla-supplied data.



## QYD-GO "Outstanding Failures Returns RED After Refresh" — Investigation (2026-02-26)
**User report**: After clicking "تنظيف فشل الاختبار" the checklist goes 11/11 green, but on refresh "لا فواشل عالقة" returns to RED. User suspected exclusion is not persisted or not read.

### Investigation result
**The exclusion mechanism is fully correct** — proved by 5 new integration tests in `tests/test_qoyod_qydgo_clear_failures_persistence.py`:
1. Cleanup endpoint writes `excluded_from_checklist=true` + `excluded_at=<datetime>` to Mongo for all matched rows.
2. `_check_outstanding_failures` correctly filters `{"$ne": True}` on the flag.
3. Three successive checks after cleanup all return OK (= page stays green across refreshes).
4. Cleanup is idempotent.
5. **NEW** failures appearing after cleanup correctly DO surface (this is the actual cause).

There is **only one code path** that counts outstanding failures (`_check_outstanding_failures` in `go_live.py`); no second un-filtered query exists.

### Most likely root cause
The background worker keeps draining old in-flight orders (NORMALIZED, CUSTOMER_RESOLVED, INVOICE_CREATED, …) and when any of them fails it produces a **brand-new** DEAD_LETTER row. The new row has no `excluded_from_checklist` flag (correct — exclusion only stamps existing rows at the time of the click). So:

```
T0: 5 DEAD_LETTER rows from previous tests
T1: user clicks "تنظيف فشل الاختبار" → all 5 excluded → green
T1+δ: worker processes 3 stuck NORMALIZED rows → 2 fail → 2 NEW DEAD_LETTER rows
T2: user refreshes → 2 new rows counted → RED
```

The user perceives "old failures came back" but in reality these are new ones.

### Files
- `/app/backend/tests/test_qoyod_qydgo_clear_failures_persistence.py` (new — 5 tests, all pass)
- No production code changed yet — awaiting user decision on the fix strategy.

### Proposed fixes (awaiting user choice)
- (a) **Dry-run-aware check**: `_check_outstanding_failures` only counts `dry_run: False` failures. Production failures still block; dry-run noise never blocks. Removes the need for the "clear test failures" button entirely.
- (b) **Watermark exclusion**: cleanup also writes `qoyod_settings.failures_excluded_since=now()`; the check only counts rows with `received_at > since` AND `excluded_from_checklist != True`. Closer to current UX but adds a hidden "since" timestamp.
- (c) **Archive-on-cleanup**: replace `excluded_from_checklist` with the new archive flow — physically move dry-run DEAD_LETTER rows to `integration_inbox_archive` (already built for First-Sync Monitor). Cleanest, most durable.



## First-Sync Monitor → Permanent Operational Dashboard (2026-02-26)
**User request**: Transform the First-Sync Monitor from a dev-only diagnostic into a daily operational tool with sidebar integration, status counters, failure alerts, and a safe cleanup tool for old dry-run test noise.

### Sidebar Integration (frontend)
- New link **"🩺 مراقبة مزامنة قيود"** added under التكاملات → قيود (Sidebar.jsx).
- Polls `/api/integrations/qoyod/first-sync-monitor/stats/summary` every 20s.
- Red pulsing dot + numeric badge next to the link when `failed > 0`.
- Red dot next to "التكاملات (Integrations)" section header so the alert is visible even when the section is collapsed.
- testids: `nav-qoyod-first-sync-monitor`, `nav-qoyod-monitor-alert-dot`, `sidebar-integrations-alert-dot`.

### Status Counter Badges (frontend monitor page)
- 4 stat tiles at the top of `QoyodFirstSyncMonitor.jsx`:
  - **قيد المعالجة** — any non-terminal pipeline stage.
  - **فشل (DEAD_LETTER + PARTIAL)** — failed terminal states (red when >0).
  - **ناجحة (COMPLETED)** — successful terminal state.
  - **متخطّاة (SKIPPED)** — business-rule excluded.
- testids: `stat-processing`, `stat-failed`, `stat-success`, `stat-skipped`.

### Archive Failed Dry-Run Tests (renamed from "delete")
- Banner appears only when `stats.dry_failed > 0`.
- Button **"🗂️ أرشفة فشل الاختبار القديم"** opens a confirm modal.
- User must type **"CLEAN"** (case-sensitive) — submit button stays disabled until exact match.
- Modal explicitly lists safety guarantees: rows go to archive (recoverable), COMPLETED untouched, no Qoyod data touched, production failures untouched.
- testids: `archive-failed-tests-banner`, `btn-open-archive-modal`, `archive-modal`, `archive-confirm-input`, `btn-archive-confirm`, `btn-archive-cancel`, `archive-result`.

### Backend
- **`integrations/qoyod/first_sync_monitor.py`**:
  - `get_monitor_stats(db, user_id)` — `$group` aggregation by `pipeline_stage` + `dry_run`, buckets into `{processing, failed, success, skipped, dry_failed, total}`.
  - `archive_failed_dry_run_tests(db, user_id, confirm_token, actor)` — strict filter `{user_id, pipeline_stage ∈ {DEAD_LETTER, PARTIAL_FAILURE}, dry_run: True}`; copies matched rows to `integration_inbox_archive` with stamped metadata (`archived_at`, `archived_by`, `archive_reason`, `original_inbox_id`), then deletes with the same strict filter + a `trace_id` constraint for defense-in-depth.
  - Confirm token **"CLEAN"** enforced; missing/wrong raises `ArchiveRefused`.
- **`integrations/qoyod/routes.py`**:
  - `GET /api/integrations/qoyod/first-sync-monitor/stats/summary` — counter endpoint.
  - `POST /api/integrations/qoyod/first-sync-monitor/archive-failed-tests` — body `{confirm: "CLEAN"}` → `{matched, archived, deleted, archive_ids}`. Returns 400 `confirm_required` on wrong token.
  - Routes ordered so concrete paths come before the `{trace_id}` wildcard.

### Safety Contract (NEVER violated)
1. NEVER touches `pipeline_stage: COMPLETED` rows.
2. NEVER touches `dry_run: False` rows (production data is sacred).
3. NEVER touches rows from other tenants.
4. NEVER touches data inside Qoyod itself (local-only archive op).
5. Always copies before deletes — archive is the source of truth for recovery.

### Tests
- **`tests/test_qoyod_monitor_archive.py`** (7 tests, all pass):
  - Stats correctly bucket all stages including `dry_failed` subset.
  - Empty tenant returns all zeros.
  - Confirm-token enforcement (empty / lowercase / wrong word all rejected).
  - Strict filter test: 8-row mix → only the 2 dry+failed rows archived; 6 protected rows untouched.
  - Idempotent when no matches.
  - Cross-tenant isolation.
- E2E manual test passed: seeded 1 dry-run DEAD_LETTER row → sidebar badge + section dot + banner appeared → typed CLEAN → archive succeeded → row in `integration_inbox_archive` with full metadata, 0 rows in live collection.

### Files Changed
- `/app/backend/integrations/qoyod/first_sync_monitor.py` (+~120 lines)
- `/app/backend/integrations/qoyod/routes.py` (3 new endpoints, BaseModel)
- `/app/frontend/src/components/Sidebar.jsx` (link + 20s polling + alert dot)
- `/app/frontend/src/pages/QoyodFirstSyncMonitor.jsx` (badges + banner + modal)
- `/app/backend/tests/test_qoyod_monitor_archive.py` (new — 7 tests)



## QYD-GO Checklist Fixes — 3 Blockers (2026-06-27)
**User-reported**: After the worker wiring, QYD-GO page still blocked Go-Live with 3 false-positives.

### Fix 1 — Branch ID no longer a blocker
- `go_live.py::_check_branch` now returns `ok: True` regardless of branch value.
  - When set: detail shows the ID.
  - When blank: detail reads "اختياري — الحساب أحادي الفرع، سيستخدم قيود الفرع الافتراضي تلقائياً".
- Consistent with the earlier `setup_validation.py` change that demoted branch to a warning.

### Fix 2 — Outstanding-failures check ignores excluded test rows
- `_check_outstanding_failures` now filters `excluded_from_checklist: {$ne: true}` so stale DEAD_LETTER/PARTIAL_FAILURE rows from pre-fix tests don't block readiness.
- **New endpoint** `POST /api/integrations/qoyod/go-live/clear-test-failures`:
  - Marks all current DEAD_LETTER/PARTIAL_FAILURE rows as `excluded_from_checklist: True` with `excluded_at` timestamp.
  - Rows STAY in the database for First-Sync-Monitor visibility — only the QYD-GO check ignores them.
- **Frontend** `QoyodGoLive.jsx`:
  - When `outstanding_failures` fails, an inline button "🗑️ تنظيف فشل الاختبار (استبعاد من الفحص)" appears beneath the row.
  - On click: confirmation prompt → POST → toast with count → reload.

### Fix 3 — Eligible orders detects post-worker dry-run completions
- `_check_eligible_orders` now has TWO acceptance paths:
  - a) In-flight rows (NORMALIZED / CUSTOMER_RESOLVED) matching a trigger slug — existing behaviour.
  - b) **NEW**: At least one COMPLETED dry-run row in the last 24h matching a trigger slug.
- Path (b) is critical because the worker now drains rows quickly out of NORMALIZED, so (a) often returns 0 even when the pipeline is healthy and processing real webhooks.
- Detail reads "X طلب اكتمل في Dry Run خلال 24 ساعة — الـ pipeline نشط".

### Fix 4 (bonus) — Dry-run proof uses integration_inbox
- `_check_dry_run_proven` previously read `db.qoyod_invoices` which is not populated by the new pipeline.
- Now reads `integration_inbox` with `pipeline_stage: COMPLETED` + `dry_run: True`.

### Tests
- `tests/test_qoyod_go_live_qyd_fix.py` — 10 tests covering all 4 fixes incl. nested-field queries and edge cases.
- All Qoyod-touched tests still pass.


## Pipeline Worker Wiring — CRITICAL FIX (2026-06-27)
**User-reported bug**: Webhook order #268602475 stuck at NORMALIZED forever; never reached CUSTOMER_RESOLVED / INVOICE / RECEIPT.

### Root cause
Two bugs working together:
1. The pipeline was designed in two manual stages (`/pipeline/process-normalized`, `/pipeline/process-customer-resolved`) intended to be called by a background worker. **The worker was never wired into application startup.** Rows sat at NORMALIZED indefinitely.
2. Even when the orchestrator was called manually, `process_normalized_row` accepted `api_client=None` but never built a `DryRunQoyodClient` from settings — the customer resolver then attempted to hit the real Qoyod API (returning 401 in dry-run scenarios).

### Fix
- **NEW `integrations/qoyod/worker.py`** — asyncio loop that drains `process_pending_normalized` + `process_pending_customer_resolved` every 5s with batch_limit=25. Exposes `start_worker()`, `run_now()` (emergency manual trigger), `liveness()`, `is_running()`.
- **`server.py` startup hook (iter-262)** spawns the worker on app startup (idempotent, errors logged not raised).
- **`pipeline.py::process_normalized_row`** — now calls `_get_api_client(db, user_id, settings)` when `api_client=None`, so dry-run mode correctly skips real Qoyod calls.
- **`first_sync_monitor.py`** — new `_is_stuck()` helper + `stuck` field on each shaped row. Returns `{stage, waited_seconds, reason}` when a row in `{NORMALIZED, RULES_APPLIED, CUSTOMER_RESOLVED, INVOICE_CREATED}` exceeded 30s.
- **New endpoints**:
  - `GET /api/integrations/qoyod/worker/status` → `{running, last_run_at, last_run_ok, last_round}`.
  - `POST /api/integrations/qoyod/worker/run-now` → emergency drain trigger.
- **Frontend `QoyodFirstSyncMonitor.jsx`**:
  - Worker status pill in toolbar (✓ يعمل / ⚠ خطأ / ✗ متوقف).
  - "⏳ بانتظار العامل (Xs)" badge on stuck rows + red banner with "▶️ تشغيل الآن" emergency button.
  - Auto-refresh default = ON (was OFF).

### End-to-end verification
Sent a fresh dry-run webhook → 15s later monitor showed:
```
pipeline_stage: COMPLETED
customer  success qoyod_id=DRY:contact:4c6cc5e2
product   success qoyod_id=DRY:product:8cc97e87 (per-SKU)
invoice   success qoyod_id=DRY:invoice:eea30eba
receipt   success qoyod_id=DRY:receipt:7b45b841
```

### Tests
- `tests/test_qoyod_worker_and_stuck.py` — 9 tests covering stuck detection (under threshold, over threshold, all waiting stages, ISO timestamps, naive datetimes, COMPLETED/DEAD_LETTER skipped) + worker module liveness shape.
- All 72 Qoyod-touched tests pass cleanly when run in isolation.


## P0 Pre-Go-Live: Dynamic Salla Statuses + Product Type Label (2026-06-27)

### 1) Order-status trigger picker is now DYNAMIC
- **Backend** `routes.py` — new endpoint `GET /api/integrations/qoyod/salla-order-statuses`:
  - Primary source: calls Salla `GET /orders/statuses` via `call_salla()` (auto-refresh token).
  - Each row normalized to `{id, slug (lowercase), name, name_en, type, is_system}`.
  - Fallback when Salla disconnected/unreachable: scans `unified_orders` for distinct `order_status` slugs + Arabic names from `raw.status.name`.
  - Response includes `source: "salla_api" | "fallback"` and structured `error` so the UI can inform the operator.
- **Frontend** `QoyodSettings.jsx`:
  - Removed hardcoded `TRIGGER_STATUS_OPTIONS` (completed/delivered/paid/shipped).
  - New `loadSallaStatuses()` populates the picker from the endpoint.
  - Source badge: ✓ من Salla API (emerald) أو ⚠ من الطلبات المرصودة (amber).
  - Each row shows the merchant's actual Arabic status name + the immutable `slug:` (small mono) underneath.
  - Hint clarifies: "النظام يستخدم slug الحالة من Salla — تغيير الاسم الظاهر في Salla لا يكسر التكامل."
  - 🔄 Reload button per-section to force a fresh fetch.
  - Persisted to settings as lowercase slug array (existing pipeline already matches on `dto.order_status` which is also lowercase canonical — no behaviour change downstream).
- **Tests**: `test_qoyod_salla_order_statuses.py` — 3 tests (Salla success path normalization, fallback to observed orders, slug-not-name persistence).

### 2) Product Type label change (cosmetic only)
- **Frontend** `QoyodSettings.jsx` — the `PRODUCT_TYPE_OPTIONS` row for technical value `"service"` now reads:
  - **"منتجات بدون إدارة مخزون في قيود — موصى به لربط ميزان"** (was "خدمات (Service) — موصى به للمتاجر الرقمية").
- The wire value sent to Qoyod remains `service`. Pipeline / invoice builder unchanged.

### Tests
- Full Qoyod suite: **339 passed**, 0 regressions.


## P0 First Production Dry Run Readiness (2026-06-27)
**Goal**: Final pre-production polish before flipping Dry Run off.

### 1) Branch ID is now OPTIONAL
- `setup_validation.py` — `missing_branch_id` demoted from `blocker` to `warning` (single-branch Qoyod accounts don't need this set).
- `invoice_builder.py` — when `default_branch_id` is None/empty, the field is OMITTED from the invoice payload (no `branch_id: null` sent to Qoyod).
- Frontend `QoyodSettings.jsx` — label changed to "Branch ID — اختياري", `required` asterisk removed, hint reads "اتركه فارغاً إذا كان حسابك بفرع واحد".
- Tests cover both the validation severity change and the conditional payload behaviour.

### 2) Tax ID hardening
- `invoice_builder.py` — explicit comment that `default_tax_id` MUST be a Qoyod **Tax ID** (e.g. `"1"`), NOT a rate (e.g. `"15"`). When unset the line-item `tax_id` is OMITTED so Qoyod uses the item's own tax.
- Regression test `test_invoice_line_uses_tax_id_not_rate` asserts no `tax_rate` or `rate` field leaks into the line payload.

### 3) First-Sync Monitor (new page)
- **Backend** `integrations/qoyod/first_sync_monitor.py`:
  - `shape_inbox_row_for_monitor(row)` — reduces an `integration_inbox` doc into operator timeline: order summary, raw Make payload, canonical DTO, 4 step cards (customer/product/invoice/receipt) each with `payload` + `response` + `duration_ms` + per-step status.
  - `_status_for_stage()` — calculates per-step status: success / failed / pending / skipped based on `last_success_stage` and `last_failed_stage`.
  - Endpoints:
    - `GET /api/integrations/qoyod/first-sync-monitor?limit=N` — latest N rows reduced.
    - `GET /api/integrations/qoyod/first-sync-monitor/{trace_id}` — single row by trace.
- **Pipeline** persists raw Qoyod responses now:
  - `qoyod_responses.invoice = {body, qoyod_id, qoyod_number, duration_ms, received_at}` on success / `{error, duration_ms}` on failure.
  - Same for `qoyod_responses.receipt`.
- **Frontend** new page `pages/QoyodFirstSyncMonitor.jsx` at route `/integrations/qoyod/first-sync-monitor`:
  - Toolbar: refresh, auto-refresh-every-5s toggle, limit selector (1/3/5/10/25).
  - Per-row card with collapsed/expanded states.
  - 4 expandable step cards with side-by-side "📤 الإرسال إلى Qoyod" + "📥 الرد من Qoyod" JSON blocks.
  - Stage History timeline with timestamps, actors, notes, and error blocks.
  - Side-by-side Make raw + canonical DTO + business rules + preflight.
  - Empty-state guides the operator to send first Make payload.

### Tests
- `tests/test_qoyod_first_sync_monitor.py` — 11 tests covering status calculation, shaper output, branch-id-optional in builder, tax-id-not-rate, setup-validation severity downgrade.
- Updated `test_qoyod_setup_validation.py` to expect branch as warning instead of blocker.
- Full Qoyod suite: **336 passed**, 0 Qoyod regressions.


## Qoyod Fresh-Start Cleanup — Plan + Execute (2026-06-27)
**Goal**: Simplified deletion workflow (Audit → Plan → Execute) gated by `DELETE-CONFIRM` token. No Dry-Delete; environment not yet productive.

### Backend
- **NEW `integrations/qoyod/fresh_start_cleanup.py`**:
  - `EXPECTED_CONFIRM_TOKEN = "DELETE-CONFIRM"` (exact case-sensitive match).
  - `PROTECTED_ENTITIES` constant lists what is NEVER touched: chart_of_accounts, branches, taxes, settings, users, financial_accounts.
  - `build_plan()` paginates 4 entities and persists ID lists to `qoyod_fresh_start_cleanups`.
  - `execute_cleanup()` — refuses unless token matches exactly AND a planned job exists. Deletion order: **Receipts → Invoices → Products → Customers** (FK-safe).
  - `_delete_batch()` — continues past failures, treats 404 as success, aborts batch on 405 (Qoyod doesn't support DELETE), 100ms cushion between calls.
  - Raises `CleanupRefused` on bad token or missing plan.
- **API client**: added GET-only `list_invoices`/`list_receipts` and DELETE methods `delete_invoice/receipt/product/customer` (gated by cleanup module only).
- **New endpoints**:
  - `POST /api/integrations/qoyod/fresh-start/plan/build` — builds a plan job, returns full ID list + totals.
  - `GET /api/integrations/qoyod/fresh-start/plan/latest` — returns latest plan (also exposes `expected_confirm_token` and `protected_entities`).
  - `POST /api/integrations/qoyod/fresh-start/execute` — body `{job_id, confirm}`. Returns final report with `deleted` counts and `failed` array.

### Frontend (`pages/QoyodFreshStart.jsx` enhancement)
After a successful audit, three new sections appear:
1. **🗂️ Plan section** — explicit lists of "سيُحذف فقط" (4 entities) vs "لن يُمَس إطلاقاً" (6 protected), big-numbers preview of the current plan, build/rebuild button.
2. **🔴 Execute section** — appears only when `plan.status === "planned"`. Final warning + `DELETE-CONFIRM` text input that turns green when typed correctly. Execute button disabled until exact match.
3. **📊 Execute Result section** — per-entity deleted counts (success cards) + a failures table (entity/id/code/message) if any. On full success, prompts navigation to Settings to disable Dry Run and send first Make.com test.

### Tests
- `tests/test_qoyod_fresh_start_cleanup.py` — 15 tests covering: token constant, protected list, ID extraction edge cases, batch happy path, 404-as-success, continue-past-failures, 405 abort, token gating (wrong/whitespace/case/missing-plan), deletion ORDER assertion, partial failure reporting, confirm-token audit trail persistence, plan building from all 4 entities.
- Full Qoyod suite: **333 passed**, 0 regressions.


## Qoyod Fresh-Start Audit — READ-ONLY Snapshot (2026-06-27)
**Goal**: Forensic audit of what already exists in Qoyod (legacy direct-Salla integration data) before Mezan becomes the sole source. STRICTLY read-only; no DELETE/PUT/PATCH.

### Backend
- **NEW `integrations/qoyod/fresh_start_audit.py`** — orchestrator + pure analysers:
  - `run_fresh_start_audit()` paginates ONLY 4 endpoints: `/invoices`, `/receipts`, `/products`, `/customers`. 100ms cushion between pages.
  - Per-entity analysers: counts, monthly histograms, sample rows (first 5), and link-completeness checks.
  - `_build_flags()` derives cross-entity risk warnings: invoices without receipts, orphan receipts, products without SKU, customers without invoices, ref/invoice mismatch.
  - Run persisted to `qoyod_fresh_start_audits` (one row per run; latest is fetched by `latest_audit()`).
  - Hard scope: **never** queries `/accounts`, `/branches`, `/taxes`, or any settings endpoint.
- **`integrations/qoyod/api_client.py`** — added `list_invoices(page, limit)` and `list_receipts(page, limit)` (GET only).
- **New endpoints** in `routes.py`:
  - `POST /api/integrations/qoyod/fresh-start/audit/run` — kicks off a new audit.
  - `GET /api/integrations/qoyod/fresh-start/audit` — returns latest audit snapshot.

### Frontend (`pages/QoyodFreshStart.jsx`)
- New route: `/integrations/qoyod/fresh-start`.
- Header explicitly states "قراءة فقط — لا تعديل ولا حذف".
- Pre-run state: 🛡️ safety guarantees panel.
- Post-run state: 4 total cards + per-entity sections with month histograms + sample tables + risk flags pill list.
- Failed state: shows structured error (e.g. `qoyod_unauthorized`).
- Footer reminds operator: deletion is a separate gated phase requiring `DELETE-CONFIRM` and explicit criteria.

### Tests
- `tests/test_qoyod_fresh_start_audit.py` — 16 tests covering ref pattern detection, month-bucket parsing, list extraction robustness, all 4 analysers, and 4 flag-building edge cases.
- Full Qoyod suite: 307 passed, 2 skipped, 0 regressions.


## Qoyod Settings — Final One-Time Setup Page (2026-06-27)
**Goal**: After saving once, operator never needs to revisit unless a new payment method is added or accounting setup changes.

### Backend
- **NEW `integrations/qoyod/setup_validation.py`**
  - `CANONICAL_PAYMENT_METHODS`: ordered list of 11 canonical methods (mada, apple_pay, visa, mastercard, credit_card, stc_pay, bank_transfer, tamara, tabby, emkan, cod).
  - `collect_used_payment_methods(db, user_id)` — scans `unified_orders` (payment_method + raw.payment_method) and `integration_inbox.canonical_payload.payment_method`. Returns grouped `{key,label_ar,count,sources,native_examples}` rows.
  - `validate_settings_for_setup(db, user_id)` — runs every Settings-page check:
    - `missing_branch_id` (blocker)
    - `missing_tax_id` (blocker)
    - `unmapped_payment_methods` (blocker) — every USED canonical key must have a non-empty `qoyod_account_id`.
    - `missing_inventory_account` / `missing_cost_account` (blocker, only if product_type=inventory)
    - `missing_default_customer` (warning) — optional guest fallback.
- **`integrations/qoyod/normalizer.py`** — added `emkan` (and Arabic `إمكان`) to canonical payment-method table.
- **New endpoints** in `routes.py`:
  - `GET /api/integrations/qoyod/payment-methods/used` — returns `{used:[…], catalogue:[…]}` for the mapping table.
  - `GET /api/integrations/qoyod/setup/validate` — returns full validation result for fail-safe server-side gate.

### Frontend (`pages/QoyodSettings.jsx`)
Complete rewrite as a one-time setup page:
1. **Setup Status banner** (top) — live client-side validation that mirrors the backend logic and updates as user types. Red banner with `Jump to →` buttons when blockers exist.
2. **Master switches** — enabled/auto_send/auto_receipt/dry_run_mode.
3. **API Key** + Test Connection.
4. **Webhook Token** (unchanged) — Make.com inbound auth.
5. **Core IDs**: Branch ID, Tax ID, Default Customer ID (optional) — manual input + datalist suggestions.
6. **💳 Payment Method Mapping table** (the most important section):
   - One row per used method (mandatory) + addable canonical methods.
   - Each row: Salla method label, Account ID input, status badge (مربوط / مطلوب / اختياري).
   - Rejected rows have rose-50 highlight.
7. **📦 Inventory Accounts** (conditional, only when `default_product_type === "inventory"`) — Inventory Account + COGS Account.
8. **Advanced** — trigger statuses, invoice date source, product type, trigger_once_only.
9. **Capability flags** — create_customers/products/invoices/receipts.
10. **📋 Setup Guide** (inline expandable) — 6 cards (Branch, Tax, Account, Default Customer, Inventory, API Key) with step-by-step instructions + direct `legacy.qoyod.com` links.
11. **Sticky save bar** — disabled when blockers exist. Save calls server validate after PUT as fail-safe.

### Tests
- `tests/test_qoyod_setup_validation.py` — 9 tests covering catalogue completeness, missing branch/tax blocking, used-but-unmapped detection, inventory mode requirements, payment method normalisation grouping, partial mapping handling.
- Full Qoyod suite: 291 passed, 2 skipped, 0 regressions.


## QYD-GO — Production Readiness Layer (2026-06-26)
Independent, read-only verification layer. NO new business logic; refuses to let
the operator flip the connector to live-mode unless every check passes.

### Backend (`integrations/qoyod/go_live.py`)
**Checklist — 11 items** (returned as `[{key,label,ok,detail,extra?}]`):
1. `api_key`              — credentials saved + fingerprint visible.
2. `branch`               — `default_branch_id` set.
3. `tax`                  — `default_tax_id` set.
4. `payment_mapping`      — at least one mapping AND all observed PMs mapped.
5. `product_mapping`      — local SKU mappings exist (or DryRun will create).
6. `customer_mapping`     — local customer mappings exist (or DryRun will create).
7. `dry_run`              — `dry_run_mode==True` NOW AND ≥1 completed dry-run row.
8. `outstanding_failures` — no rows stuck in DEAD_LETTER or PARTIAL_FAILURE.
9. `eligible_orders`      — ≥1 eligible row in NORMALIZED / CUSTOMER_RESOLVED.
10. `products_lookup`     — live GET /products against Qoyod succeeds.
11. `customers_lookup`    — live GET /contacts against Qoyod succeeds.

**Report — 8 numbers** (the operator stares at these before clicking activate):
- `eligible_orders_count`, `products_needing_creation`, `products_already_in_qoyod`,
  `qoyod_products_total`, `customers_needing_creation`, `customers_already_local`,
  `qoyod_contacts_total`, `unmapped_payment_methods` (+count), `would_fail_if_live_now`,
  `dry_run_mode_currently_on`.

**Activation** (`activate_production_mode()`):
- Re-runs the checklist server-side (defense-in-depth — UI can't bypass).
- On failure → raises `ActivationBlocked(reasons=…, items=…)` → HTTP 409 with the
  closed list of failing labels for the toast.
- On success → atomic `update_one(..., $set:{enabled:true, dry_run_mode:false,
  activated_at:now()})`.

### API endpoints
- `GET  /api/integrations/qoyod/go-live/checklist`
- `GET  /api/integrations/qoyod/go-live/report`
- `POST /api/integrations/qoyod/go-live/activate`

### API client extension
- `list_products(page,limit)` + `list_contacts(page,limit)` added to `QoyodAPIClient`
  (used only by Go-Live lookup checks).

### Frontend (`QoyodGoLive.jsx` at `/integrations/qoyod/go-live`)
- Top status banner: traffic-light (emerald = live, blue = ready, amber = blocked) +
  ACTIVATE button disabled until `all_passed`.
- Report card: 8 stat cells with tone (rose for ‘would_fail’, amber for ‘needs work’,
  emerald for safe).
- Checklist card: 2-column grid of items with ✓/✗ circle + detail text +
  "X/11 اجتاز" badge.
- Confirmation dialog before activation; toast surfaces the failing reasons on 409.
- New sidebar entry under Integrations → قيود: "🚀 جاهزية الإنتاج (QYD-GO)" with
  testid `nav-qoyod-go-live` (above Invoices).

### Tests — 10/10 ✅ (full Qoyod suite: 140/140)
- `tests/test_qyd_go_production_readiness.py` covers:
  - Checklist happy path (full readiness → all_passed=true).
  - Each negative path: missing api_key / missing branch+tax / unmapped payment /
    dry_run disabled / outstanding DEAD_LETTER / Qoyod lookup error.
  - Report: products/customers creation-vs-mapped counts, unmapped PMs, would_fail.
  - Activation: blocked when checklist fails (settings stay unchanged) ·
    succeeds when all pass (flips dry_run_mode→false + enabled→true atomically).

### Live curl + UI smoke ✅
- `GET /go-live/checklist` → 11 items rendered in Arabic with ✓/✗.
- `GET /go-live/report` → all 8 fields populated.
- `POST /go-live/activate` (with one check failing) → 409 `activation_blocked` with
  the failing labels in `detail.reasons`.

## Qoyod MVP — Day 5 + Pre-Day-5 Safety Rules (2026-06-26)
**User-locked safety rules** (implemented BEFORE any live Qoyod write):

### 1. Dry Run Mode (`settings.dry_run_mode: bool`)
- Master toggle in `QoyodSettings.jsx` (🧪 وضع التشغيل الجاف).
- New `DryRunQoyodClient` is a drop-in replacement for `QoyodAPIClient` with the
  same async interface (`create_contact / create_product / create_invoice / create_receipt`).
- Returns deterministic fake ids of the form `DRY:<entity>:<sha8>` so downstream stages
  still build receipt payloads correctly. Records every "POST" it WOULD make in `self.calls`.
- The orchestrator picks the client based on `is_dry_run_mode(settings)`.
- Rows complete the pipeline fully — payloads snapshotted, ledger rows written with
  `dry_run=true` + `status="pending"`. Audit fields populated identically to live mode.

### 2. Pre-flight Checklist (`preflight.py`)
Six required checks, ALL must pass before any invoice POST:
  1. **customer**   — `qoyod_customer_id` populated.
  2. **products**   — every line item has a `qoyod_product_id`.
  3. **tax**        — `default_tax_id` configured OR all items carry `tax_amount`.
  4. **payment**    — Salla method present AND mapped to a Qoyod account.
  5. **status**     — canonical order_status in `invoice_trigger_statuses`.
  6. **idempotency**— no prior `status="sent"` invoice row (when `trigger_once_only=True`).
Failure → row routes through `FAILED_INVOICE → DEAD_LETTER` with the full `preflight.failures[]`
recorded in `pipeline_error.preflight` for diagnosis.

### 3. Payload Snapshot
- Invoice payload written to `row.qoyod_payloads.invoice` + `invoice_snapshot_at` BEFORE the
  POST attempt — same for receipt. Survives even if Qoyod returns 5xx mid-call.
- Snapshots are full Qoyod request bodies (not the canonical DTO) so the operator can
  diff-test against a future Qoyod API change.

### 4. Partial Failure (`PARTIAL_FAILURE` terminal stage)
- New terminal stage in `state_machine.py`. Added to `TERMINAL_STAGES`. The edge
  `FAILED_RECEIPT → PARTIAL_FAILURE` is the ONLY path in.
- When invoice POST succeeds but receipt POST raises → row goes
  `INVOICE_CREATED → FAILED_RECEIPT → PARTIAL_FAILURE` (NOT DEAD_LETTER — invoice IS in Qoyod).
- `qoyod_invoices.status="invoice_sent_receipt_failed"`, `pipeline_stage="PARTIAL_FAILURE"`,
  `last_failed_stage="FAILED_RECEIPT"`, `last_success_stage="INVOICE_CREATED"`.

## Day 5 — Pipeline Completion (4b → 4c → 4d)

### New backend modules
- `product_resolver.py` — `resolve_products()` walks `dto.items`, hits
  `qoyod_products_mapping` first, creates in Qoyod on miss with idempotency
  key `mzn-{trace}-product-{sku}`. Builds product payload from `default_product_type`.
  `ProductsResolutionResult.items` carries per-sku status.
- `invoice_builder.py` — pure `build_invoice_payload()` / `build_receipt_payload()`,
  `DryRunQoyodClient`, `is_dry_run_mode()`. Maps DTO line items → Qoyod line_items with
  resolved product ids + `default_tax_id`. Receipt resolves `account_id` from
  `payment_method_mapping`.
- `preflight.py` — pure `run()` returning `PreflightResult(passed, failures)`.
- `pipeline.py` extended with `process_customer_resolved_row()` chain:
  `CUSTOMER_RESOLVED → PRODUCT_RESOLVED → (preflight) → INVOICE_CREATED → RECEIPT_CREATED → COMPLETED`,
  with the three safety nets above wired into every hop. Also `process_pending_customer_resolved()`
  batch driver and `day4_report()` aggregation.

### New API endpoints
- `POST /api/integrations/qoyod/pipeline/process-customer-resolved?limit=25`
  — drains CUSTOMER_RESOLVED rows up to limit.
- `GET  /api/integrations/qoyod/reports/day4`
  — aggregates `by_stage`, `skipped_reasons`, `dead_letter_by_stage`, `totals`.

### Frontend
- `QoyodSettings.jsx` — new Dry Run toggle (`data-testid="toggle-dry-run-mode"`).
- `QoyodInvoices.jsx` — new **Day 4 Report Card** (`qoyod-day4-report-card`):
  6 stat cells (normalized/customer_resolved/completed/skipped/dead_letter/partial_failure)
  + skipped-reason pills + dead-letter-by-stage pills
  + 2 action buttons (`run-process-normalized` blue, `run-process-customer-resolved` green)
  with hint "يحترم Dry Run Mode + Pre-flight + Payload Snapshot".
- Reconciliation + Compliance + Orphans + Invoices Tables unchanged.

### Tests — 130/130 ✅
- `tests/test_qoyod_day5_invoice_receipt.py` — **15/15** new tests:
  - State-machine: PARTIAL_FAILURE terminal + FAILED_RECEIPT edge.
  - Preflight: 6 checks (passes / each fails individually).
  - Payload builders: invoice/receipt include all required fields, resolve payment account.
  - DryRunQoyodClient: records calls, returns deterministic fake ids.
  - E2E: dry-run completes without POST + payloads snapshotted +
    `pending` ledger row · preflight blocks on missing tax · receipt failure routes to
    PARTIAL_FAILURE + ledger reflects split state · snapshot timestamps present ·
    day4_report aggregates correctly.
- All prior suites unchanged: Day1×28, State Machine×24, Compliance×11, Day3×28, Day4×25.

### Live UI smoke ✅
- Day 4 Report card visible at top of `/integrations/qoyod/invoices` with 6 stat cells.
- Both action buttons render with correct test ids.
- Sidebar sub-grouping intact.

## Qoyod MVP — Day 4 — Business Rules + Customer Resolution (2026-06-26)
**User-locked Invoice Trigger Policy (foundational rule):**
```
invoice_trigger_statuses = ["completed"]        # list, NEVER hard-code "paid"
invoice_date_source      = "trigger_status_date"
trigger_once_only        = true
```
Rationale: VAT + Zakat compliance forces the invoice date to come from
a configurable status transition, not from `paid`. Multiple statuses
allowed for merchants who fire on completed+delivered.

**Day 4 scope (strictly stopped at CUSTOMER_RESOLVED):**
1. `NORMALIZED → RULES_APPLIED` — eligibility decision per Invoice Trigger Policy.
2. `RULES_APPLIED → CUSTOMER_RESOLVED` (4a only) — local mapping hit OR Qoyod create.
3. NO products, NO invoice, NO receipt — pending merchant review.

### New backend modules
- `integrations/qoyod/business_rules.py` — pure `evaluate(dto, settings, existing_invoice_row)`
  returns `RulesDecision`. Tokens: `eligible / not_in_trigger_statuses / already_sent`.
  Resolves invoice date from `trigger_status_date | completed_at | paid_at | created_at`
  with safe fallback to `order_date`. Includes a status→date-field map so each canonical
  status (completed/delivered/paid/shipped/processing) picks the right timestamp.
- `integrations/qoyod/customer_resolver.py` — `resolve_customer()` walks the lookup chain
  `phone → email → guest_order`. Hits `qoyod_customers_mapping` first; only calls
  Qoyod `POST /contacts` on miss (with idempotency key `mzn-{trace}-contact-{kind}-{key}`).
  Returns `ResolutionResult(success, qoyod_customer_id, lookup_key, lookup_kind, created_new, error)`.
- `integrations/qoyod/pipeline.py` — orchestrator. `process_normalized_row()` advances ONE row
  and is idempotent on stage check. `process_pending_normalized()` batches up to `limit` rows
  sequentially. Failures route through `FAILED_CUSTOMER → DEAD_LETTER` (two-hop, exact same
  pattern as Day 3). Records `business_rules_decision` + `customer_resolution` snapshots on
  the row for the Timeline UI.

### Settings model extension (with backwards-compat shim)
- `QoyodSettings`:
  - `invoice_trigger_statuses: list[str] = ["completed"]` (new canonical)
  - `invoice_date_source: Literal["trigger_status_date","completed_at","paid_at","created_at"]
     = "trigger_status_date"` (new default)
  - `trigger_once_only: bool = True`
  - `invoice_trigger_status` kept as legacy field (None default), reads migrated on the fly.
- `_load_settings()` auto-migrates: legacy `invoice_trigger_status` → list; legacy
  `invoice_date_source="completed_at"` → `"trigger_status_date"`; missing `trigger_once_only`
  → True.
- PUT /settings expands the singular field if a caller still sends it (safe rollout).

### New API endpoint
- `POST /api/integrations/qoyod/pipeline/process-normalized?limit=25` (JWT-protected).
  Drains NORMALIZED rows. Response: `{ok, processed, counts:{customer_resolved,skipped,dead_letter}, items:[…]}`.
  Each item carries the `decision` + `customer` snapshots so the operator can audit one click.

### Frontend (`QoyodSettings.jsx`)
- Replaced the single trigger dropdown with a **4-checkbox group** (completed/delivered/paid/shipped)
  + label "حالات الطلب التي تطلق إنشاء الفاتورة". Default pre-checks `completed`.
- Added `trigger_once_only` toggle ("إنشاء الفاتورة لمرة واحدة فقط").
- Updated invoice-date dropdown to include "تاريخ انتقال الطلب للحالة المؤهلة" (recommended default).
- Save payload now sends the new list field; empty list is never accepted (auto-resets to ["completed"]).

### Tests — 115/115 ✅
- `tests/test_qoyod_day4_rules_and_customer.py` — **25/25** new tests:
  - Rules (9): eligible/not-eligible/multi-trigger/once-only/never-implicit-paid/fallback date.
  - Resolver helpers (5): phone-preferred, email-fallback, guest, payload, id extraction.
  - Resolver DB (5): local hit · create-new + mapping persisted · API error path ·
    guest with default · guest without default.
  - Pipeline (6): happy CUSTOMER_RESOLVED · SKIPPED · DEAD_LETTER + audit · trigger_once_only
    blocks resend · idempotent on already-advanced rows · batch counter sums.
- `tests/test_qoyod_day3_webhook.py` — 28/28 unchanged.
- `tests/test_qoyod_state_machine.py` — 23/23 unchanged.
- `tests/test_qoyod_compliance.py` — 11/11 unchanged.
- `tests/test_qoyod_day1_foundation.py` — 28/28 (updated `test_settings_defaults_are_safe` to
  match the new tri-field policy).

### Live curl smoke (PREVIEW) ✅
1. Webhook ingestion of an order in "تم التنفيذ" → 200 NORMALIZED.
2. `/pipeline/process-normalized` → rules say eligible/triggered_by="completed"/
   invoice_date_source="completed_at". Customer call returns 307 (placeholder API key) →
   row → `DEAD_LETTER` with `FAILED_CUSTOMER` + Qoyod response excerpt. Row preserved.
3. Webhook with "تم الشحن" → run → `SKIPPED` with `reason="not_in_trigger_statuses"`.

### Critical invariants (locked by tests)
- "paid" is NEVER an implicit trigger — must be explicitly added by the merchant.
- `trigger_once_only=True` is enforced before customer resolution (no Qoyod call on resend).
- Day 4 ceiling: orchestrator never writes to `qoyod_invoices`, never calls
  `create_product/create_invoice/create_receipt`.
- DEAD_LETTER rows from FAILED_CUSTOMER carry the full Qoyod error excerpt for diagnosis.

### Outstanding for Day 5 (pending merchant review)
- Step 4b — Product Resolution (`CUSTOMER_RESOLVED → PRODUCT_RESOLVED`).
- Step 4c — Invoice Creation (`PRODUCT_RESOLVED → INVOICE_CREATED`).
- Step 4d — Receipt Creation (`INVOICE_CREATED → RECEIPT_CREATED → COMPLETED`).
- Background retry worker (RETRYING flow).
- Manual Action buttons activation + سجل المزامنة page wiring.

## Qoyod MVP — Day 3 — Reliable Webhook Reception Layer (2026-06-26)
**User-locked scope (8 steps, nothing else):**
1. Receive webhook  ·  2. Verify token  ·  3. Idempotency  ·
4. Save raw event  ·  5. Validation  ·  6. Normalization  ·
7. Canonical SalesOrderDTO  ·  8. STOP.

NO business rules, NO Qoyod output, NO sync-log activation — those land in Day 4-5.

### New modules
- `/app/backend/integrations/qoyod/dto.py` — `SalesOrderDTO`, `CustomerDTO`, `LineItemDTO`,
  `AddressDTO`. Pure Pydantic, `extra="forbid"`, `schema_version=1`.
- `/app/backend/integrations/qoyod/normalizer.py` — `validate()` returns closed-set error codes
  (`invalid_payload_type`, `missing_data_object`, `missing_order_id`, `missing_order_status`,
  `missing_items`, `empty_items`). `normalize()` builds the DTO. Helpers: `normalize_phone()`
  (E.164 / Saudi-aware), `normalize_email()`, `_canonical_status()` (Arabic + English map),
  `_canonical_payment_method()` (mada / visa / apple_pay / stc_pay / cash / cod / tamara / tabby…).
  `NormalizationError(code, message)` carries structured failure detail.
- `/app/backend/integrations/qoyod/webhook.py` — `POST /api/integrations/qoyod/webhook`. Token check
  (`X-Webhook-Token` against `QOYOD_WEBHOOK_TOKEN`, constant-time compare). Idempotency key
  resolution: `X-Idempotency-Key` header → `salla:order:<id>:<event>` → random UUID fallback.
  Atomic insert into `integration_inbox` (DuplicateKeyError → 200 `{duplicate:true, trace_id:…}`).
  Runs `_process_inbox_row()` synchronously: NEW → RECEIVED → VALIDATED → NORMALIZED. Failures route
  through the specific `FAILED_*` hop then to **DEAD_LETTER** (terminal, NOT deleted, NOT retried).

### State machine extension
- New failure stage `FAILED_NORMALIZATION` (resume target `VALIDATED`).
- `last_failed_stage` now EXCLUDES `DEAD_LETTER` so the operator always sees the *specific*
  failure that triggered dead-lettering (FAILED_VALIDATION / FAILED_NORMALIZATION / etc.).
- Webhook orchestration injects `pipeline_started_at` into the in-memory row right after the
  NEW→RECEIVED hop so DEAD_LETTER routing can compute `pipeline_duration_ms` in-flight.

### Endpoint contract
```
POST /api/integrations/qoyod/webhook
Headers:
  X-Webhook-Token:     <required — env QOYOD_WEBHOOK_TOKEN>
  X-Idempotency-Key:   <optional — overrides derived key>
Body: JSON object (Salla webhook shape)

200 OK happy:
  {ok:true, duplicate:false, trace_id, pipeline_stage:"NORMALIZED",
   salla_order_id, audit:{started_at,finished_at,duration_ms,last_success_stage,last_failed_stage},
   canonical_payload_present:true}

200 OK duplicate:
  {ok:true, duplicate:true, trace_id, idempotency_key, pipeline_stage, salla_order_id, received_at}

200 OK dead-letter:
  {ok:false, duplicate:false, trace_id, pipeline_stage:"DEAD_LETTER",
   audit:{…last_failed_stage:"FAILED_VALIDATION"…},
   error:{code,message}, canonical_payload_present:false}

401  invalid_webhook_token / missing_webhook_token
503  qoyod_webhook_token_not_configured
400  payload_must_be_json_object
```

### Live smoke (curl on PREVIEW) ✅
1. Bad token → 401 `invalid_webhook_token`.
2. Happy payload → 200 `NORMALIZED`, canonical_payload_present=true.
3. Re-send same `X-Idempotency-Key` → 200 `duplicate:true` (row count stays 1).
4. Payload missing `status` → 200 `DEAD_LETTER`, `last_failed_stage="FAILED_VALIDATION"`,
   stage_history = `[NEW, RECEIVED, FAILED_VALIDATION, DEAD_LETTER]`, row preserved in DB.

### Tests — 90/90 ✅
- `tests/test_qoyod_day3_webhook.py` — 28/28
  (token: 4 · idempotency: 3 · validate: 7 · normalize: 7 · e2e: 7).
- `tests/test_qoyod_state_machine.py` — 23/23 (FAILED_NORMALIZATION vocab + DEAD_LETTER exclusion).
- `tests/test_qoyod_compliance.py` — 11/11 unchanged.
- `tests/test_qoyod_day1_foundation.py` — 28/28 unchanged.

### Critical invariants (locked by tests)
- DEAD_LETTER rows are **never deleted, never auto-retried**.
- `last_failed_stage` always points to the specific FAILED_* stage, never DEAD_LETTER.
- `canonical_payload` is ONLY written on the NORMALIZED hop; failed rows have it as None.
- `qoyod_invoices` collection stays untouched — no rows are written from the webhook.

### Outstanding for Day 4-5 (waiting for sign-off)
- Business Rules step (RULES_APPLIED).
- 4a Customer / 4b Product / 4c Invoice / 4d Receipt — actual Qoyod API calls.
- Background retry worker (RETRYING → resume_from).
- Activation of the 6 Manual Action buttons + the "سجل المزامنة" page.

## Qoyod MVP — Pre-Day 3 Refinements v2 (2026-06-26 · Navigation IA + Reconciliation Card + Audit Trail)
**User mandate before starting Day 3:** three additive improvements that lock in the IA so we
don't need a refactor mid-Day-3.

### 1. Navigation reorganisation
- New top-level sidebar section: **التكاملات (Integrations)** with sub-grouped items per upstream platform.
- `Sidebar.jsx` now supports `subgroups: [{id,label,items}]` alongside flat `items`. Renderer prints
  a small uppercase subgroup header above each sub-list. `findSectionFor`, search filter,
  visibility dialog, and `totalMatches` all walk subgroups transparently.
- **Salla** subgroup: إعدادات سلة · Webhooks · مراقبة الطلبات · سجل الأحداث · مقارنة مصادر البيانات.
- **قيود** subgroup: إعدادات قيود · فواتير قيود — مراقبة · منتجات قيود · عملاء قيود · سجل المزامنة · سجل الأخطاء.
- Generic stub component `pages/IntegrationPlaceholder.jsx` powers the 6 "coming soon" routes
  (qoyod products/customers/sync-log/error-log + salla orders/events). Each carries `phase` (Day 3 /
  Day 4-5 / مرحلة لاحقة) and `related[]` links so the operator can navigate to ready siblings.
- Qoyod entries removed from "الاستيراد والربط".

### 2. Reconciliation Card (مطابقة قيود)
- New backend helper `compliance.reconciliation_check()` returns:
  `{eligible_orders_count, qoyod_invoices_count, difference, has_diff, drilldown_url, oldest_unsent_at}`.
- Endpoint: `GET /api/integrations/qoyod/compliance/reconciliation`.
- Frontend card on `/integrations/qoyod/invoices` — 3 stat cells + amber CTA "عرض الطلبات غير المرسلة"
  that scrolls to the Orphan Orders table. Renders an emerald "كل الطلبات وصلت" message when diff=0.

### 3. Audit Trail in `state_machine.transition()`
Per user spec, every transition now records:
- `pipeline_started_at`  — set on NEW → RECEIVED.
- `pipeline_finished_at` — set on entry to any terminal stage (COMPLETED/SKIPPED/DEAD_LETTER).
- `pipeline_duration_ms` — computed server-side from `existing_started_at` arg + finish timestamp.
- `pipeline_outcome`     — the terminal stage name.
- `last_success_stage`   — last happy-path stage we reached (excl. NEW).
- `last_failed_stage`    — last `FAILED_*` stage entered.
- `trace_id`             — already present, surfaced in the audit summary.

Models extended with the six new fields on both `IntegrationInbox` and `QoyodInvoiceRecord`.
Timeline Drawer in `QoyodInvoices.jsx` now opens with an "🧭 سجل التتبع" section listing all
nine audit fields in a compact 3-column grid.

### Tests (62/62 ✅)
- `tests/test_qoyod_state_machine.py` — 23/23 (vocabulary lock + graph + transition + audit trail +
  retry loop e2e).
- `tests/test_qoyod_compliance.py` — 11/11 (classification, orphan listing, summary, reconciliation
  with/without diff).
- `tests/test_qoyod_day1_foundation.py` — 28/28 (unchanged).
- curl Live on PREVIEW: `/compliance/reconciliation` returns the expected JSON shape.

### Frontend smoke (screenshot verified)
- Reconciliation card renders with 3 cells at top.
- Compliance Alert below with 5 cells.
- Orphan + Invoices tables render empty-state placeholders.
- New Integrations section in sidebar with Salla + قيود subgroups expanded; all 11 nav items present
  with the expected data-testids (`nav-salla-orders`, `nav-qoyod-products`, etc.).
- Old Qoyod links no longer appear in "الاستيراد والربط".

### Outstanding for Day 3
- `POST /api/integrations/qoyod/webhook` — idempotent insert into `integration_inbox`, initial
  transition `NEW → RECEIVED` (will auto-populate `pipeline_started_at`).
- Validation step → `VALIDATED` / `FAILED_VALIDATION`.
- Normalization step → `NORMALIZED` canonical SalesOrder DTO.

## Qoyod MVP — Pre-Day 3 Refinements (2026-06-25 · State Machine + Compliance Watch + UI Placeholders)
**User mandate before starting Day 3 webhook work:**
1. ✅ **State Machine** — canonical UPPERCASE vocabulary, allowed transitions, RETRYING transient stage,
   six failure stages (`FAILED_VALIDATION/CUSTOMER/PRODUCT/INVOICE/RECEIPT` + `DEAD_LETTER`),
   append-only `stage_history[]`, attempts counter bumped only on RETRY → resume.
2. ✅ **Compliance Watch** — eligibility classification with 5 statuses
   (`not_eligible / eligible_pending / sent_to_qoyod / failed_before_qoyod / invoice_sent_receipt_failed`)
   and 7 reasons. Dashboard Alert lives ONLY on Qoyod page (no scope creep into main Dashboard).
3. ✅ **UI Placeholders** — Invoices Data Grid, Timeline Drawer, 6 disabled Manual Action buttons,
   Compliance Alert card. No premature wiring; Day 4-5 will add real behaviour.

**New backend modules:**
- `/app/backend/integrations/qoyod/state_machine.py` — pure transition logic + `transition()`/`can_transition()`/`resume_target()`.
- `/app/backend/integrations/qoyod/compliance.py` — `classify_eligibility()`, `list_orphan_orders()`, `compliance_summary()`.

**Model extensions** (`integrations/qoyod/models.py`):
- `IntegrationInbox.pipeline_stage` default flipped to `"NEW"` (canonical) + new `stage_history[]` field.
- `QoyodInvoiceRecord` extended with `pipeline_stage`, `stage_history[]`, `eligibility_status`, `eligibility_reason`.
- New tuples `ELIGIBILITY_STATUSES`, `ELIGIBILITY_REASONS` + index `qoyod_invoices_eligibility`.
- Legacy lowercase `PIPELINE_STAGES` kept for backwards compat.

**New API endpoints** (under `/api/integrations/qoyod`):
- `GET /invoices` — Data Grid feed (status/eligibility filters, limit ≤500).
- `GET /invoices/{order_id}` — full record + matching inbox row + merged `stage_history` (timeline source).
- `GET /compliance/orphan-orders` — Salla "تم التنفيذ" orders missing from / failed in Qoyod.
- `GET /compliance/summary` — Dashboard Alert counts + closed vocabularies for UI.

**New frontend page:** `/app/frontend/src/pages/QoyodInvoices.jsx`
- Route `/integrations/qoyod/invoices`, sidebar entry `nav-qoyod-invoices`.
- Compliance Alert card (5 stat cells, oldest pending note).
- Orphan Orders table (eligibility badge + reason + drilldown).
- Invoices Data Grid (status / pipeline stage / Qoyod refs / attempts).
- Timeline Drawer with reversed `stage_history`, color-coded transitions, raw JSON details.
- 6 disabled Manual Action buttons (Retry / Recreate Customer/Products/Invoice/Receipt / Sync This Order)
  with `title="قريباً — المرحلة 4-5"`.

**Tests:**
- `tests/test_qoyod_state_machine.py` — 18/18 PASS (vocabulary lock, graph correctness, transition side-effects,
  retry loop end-to-end, terminal stages have no outbound edges).
- `tests/test_qoyod_compliance.py` —  9/9 PASS (classification scenarios, live orphan listing, summary aggregates,
  closed vocabularies enforced).
- `tests/test_qoyod_day1_foundation.py` — 28/28 still PASS (default `pipeline_stage = "NEW"` updated).
- Live curl on PREVIEW with the merchant token confirms `/compliance/summary`, `/orphan-orders`, `/invoices`
  and `/health` all return 200 with the expected shape.

**Outstanding for Day 3 (waiting on user sign-off):**
- `POST /api/integrations/qoyod/webhook` — receive Make.com payload, idempotent insert into `integration_inbox` in NEW state.
- Validation step → VALIDATED / FAILED_VALIDATION.
- Normalization step → NORMALIZED canonical SalesOrder DTO.


## Original Problem Statement
بناء تطبيق محاسبي ذكي للتجارة الإلكترونية (MEZAN) يحلل ملفات Excel من سلة، يستقبل بيانات Make.com، يتتبع التسويات، ويدير الأصول والالتزامات.

## Architecture
- **Backend:** FastAPI + Motor (Async MongoDB)
- **Frontend:** React + react-router + Tailwind + shadcn/ui
- **SSOT:** `general_ledger` (double-entry accounting). All balances computed from GL via `compute_balance()`.
- **Storage:** `MONGO_URL` / `DB_NAME` from env. No defaults.
- **Auth:** JWT (role-based: owner/admin/accountant/operations/viewer/user)
- **Language:** Arabic (RTL UI)

## Key Collections
- `general_ledger` — SSOT (double-entry, txn_group_id)
- `financial_movements` — detailed movements (supplier_invoice, general_expense, fixed_asset)
- `operating_salaries` — modern employee storage
- `employees` — legacy employee collection
- `counterparties` — suppliers / externals / couriers
- `accounts` — bank / cash / payment_platform
- `expense_categories` — hierarchical
- `unified_orders` — Salla orders pipeline
- `settlement_files` / `settlement_entries` — Salla/BNPL reconciliation

## Strict Rules from User
- READ-ONLY on existing financial data
- No migrations / no recompute / no cleanup without explicit permission
- All balances from `general_ledger` only
- `financial_movements` is detail-enrichment layer, never balance source
- Drift detected MUST be surfaced, never hidden

## Recompute Freshness — No Stale Drift / match_status (2026-06-25 · iter-261)

**Bug reported by user:** After updating "قيمة المنصة الآن" in the
reconciliation report, the row showed `ads_daily=542.03 = platform=542.03`
(matching) yet the report still displayed:
  • drift = 14.91% / 26.24%
  • match_status = "يحتاج مراجعة"
  • non-zero diff_sar

**Root cause:** `recompute_drift_for_day` updated `platform_manual_value_*`
and `drift_pct_vs_manual` only. It did NOT touch:
  • `match_status` (stale from a previous sync)
  • `diff_native` / `diff_sar`  (stale platform-vs-ssot delta)
  • `drift_pct_vs_platform`  (stale)
  • `platform_authoritative_native` / `_sar` (still old auto_reconcile values)

So even though the user just stated "the platform value equals my SSOT",
the report continued to read those stale figures.

**Fix (display-layer + recompute path — sync/FX untouched):**
- Every manual-value entry now re-derives the full drift family from
  the freshly entered value:
    diff_native = manual − spend_native
    diff_sar    = manual_sar − spend_sar
    drift_pct_vs_platform = |diff_native| / spend_native × 100
- Manual entry is treated as the platform-authoritative value:
  `platform_authoritative_native/sar` and `platform_last_checked_at`
  are also updated. This guarantees the report shows ONE
  consistent "platform value now" across the screen.
- `match_status` is recomputed by calling `_compute_match_status()`
  with the fresh drift / confidence / has_data inputs.
- FX precision guard: when the entered native value EQUALS
  spend_native (within 1e-9), reuse `spend_sar` directly instead of
  recomputing via `manual_value × fx_rate`. This prevents 1-cent
  rounding artefacts (e.g. 144.54 × 3.75 = 542.025 banker-rounding
  to 542.02 while the stored spend_sar was 542.03) from showing
  a non-zero diff when the user clearly meant "they match".

**Tests** (`tests/test_ads_v2_recompute_freshness.py` — 4/4 ✅,
full suite 28/28 ✅):
- Matching manual value: diff_sar=0, drift=0, match_status="matched",
  platform_authoritative mirrors the entered value.
- Real-drift manual value (9.09% diff): match_status="drift_review"
  (no longer stuck on the previous "matched").
- Stale diff_sar=9999 + drift_pct=9999 are completely overwritten on
  recompute.
- Source inspection confirms `_compute_match_status` is invoked and
  `match_status` is written inside `recompute_drift_for_day`.

**Architectural invariant added:**
Any function that mutates ads_daily values (sync, auto_reconcile,
manual entry) MUST recompute the complete drift family + match_status
from the resulting state. No caller may leave stale derived values
sitting next to fresh inputs.



## ARCHITECTURAL INVARIANT (LOCKED) — Orthogonal Data State vs Connection State (2026-06-25 · iter-260)

**This is a permanent, non-negotiable architectural rule for Ads V2.**
Approved by the user as the closing principle of Ads V2 Phase 1.
Any future code in Ads V2 must respect this separation.

### Rule

```
ads_daily holds TWO orthogonal status fields:

  • match_status            → DATA STATE only (accounting)
      values: matched | pending_platform | drift_review | no_data
              (sync_failed deprecated; kept only for legacy rows)

  • platform_check_status   → CONNECTION STATE only (technical)
      values: ok | last_check_failed | token_expired
            | rate_limited | api_error

An API failure may update ONLY platform_check_status.
It must NEVER mutate match_status, spend_native, spend_sar,
bank_fee_sar, fx_rate, or any accounting field, when valid SSOT
data already exists in ads_daily.
```

### Priority order

1. Data in `ads_daily` is the Single Source of Truth.
2. Live API checks are verification tools, never truth-sources.
3. If an API check fails but `ads_daily` data is valid:
   - Do NOT change match_status.
   - Do NOT touch the row's spend numbers.
   - Do NOT drop the report.
   - Only update platform_check_status + platform_check_error.

### Implementation

- **`models.py`** declares `MATCH_STATUSES` and `PLATFORM_CHECK_STATUSES`
  as separate tuples. `AdsDaily` has both `match_status` and
  `platform_check_status` as distinct fields.
- **`sync/core.py::_map_check_status()`** — the single mapping point
  from adapter `status.code` → `platform_check_status` value. Used
  by all 3 failure paths (sync_account_day + 2 in auto_reconcile_for_day).
- **`sync/core.py`** success paths write `platform_check_status="ok"`
  and `platform_check_error=None`. Failure paths only write the
  connection-state field; they touch `match_status` only when SSOT
  is empty.
- **`data_layer/reports.py::get_reconciliation_report`** exposes
  `platform_check_status` per row, synthesises it for legacy rows,
  and returns a separate `check_*` histogram alongside `match_*`.
- **Frontend `AdsV2Report.jsx`** renders TWO badges per row
  (data + connection) and TWO histogram strips in the summary,
  visually labeled "حالة البيانات (محاسبياً)" and "حالة الاتصال
  بالمنصة (تقنياً)".

### Architectural tests (6/6 ✅ — permanent lock-in)

`tests/test_ads_v2_orthogonal_states.py`:
1. `_map_check_status` canonical mapping table.
2. `models.py` declares both status tuples + AdsDaily field.
3. `auto_reconcile_for_day` token-failure path leaves `match_status`
   = "matched" untouched while flipping `platform_check_status` to
   the corresponding error code.
4. Report summary exposes both `match_*` AND `check_*` histograms.
5. Legacy rows without `platform_check_status` get it synthesised
   on read (no DB writes).
6. `sync/core.py` source inspection asserts iter-260 markers,
   `platform_check_status` writes in all relevant paths, and
   `_map_check_status` usage.

### Allowed future changes

- Adding new connection-state values (e.g. `degraded`, `partial`)
  requires extending `PLATFORM_CHECK_STATUSES` AND `_map_check_status`
  in one place.
- Adding new data-state values (e.g. `held_review`) requires extending
  `MATCH_STATUSES` AND `_compute_match_status`.
- **Forbidden**: writing to `match_status` from any function that
  inspects API responses or HTTP errors without first verifying
  SSOT data emptiness.

### Phase 2 entry condition

This architectural rule MUST be in force before Phase 2 (review +
GL posting) begins. The review UI will operate on `match_status`
exclusively; GL postings will never depend on `platform_check_status`.



## match_status SSOT Classification Fix (2026-06-25 · iter-259)

**Bug reported by user:** After iter-258 currency fix, several Snapchat
rows in the reconciliation report were shown as "فشل في المزامنة" even
though the data was clearly present in ads_daily:
- spend_sar = 471.71 SAR
- platform value = 437.01 SAR
- drift = 7.94%
- reason = "مزامنة قبل إغلاق اليوم"

**Root cause:** Two failure paths in `sync/core.py` were unconditionally
writing `match_status="sync_failed"` to existing rows when the platform
API call hiccupped — even when `ads_daily.spend_native` already held
valid SSOT data from a previous successful sync. This violated the
user's classification rule:

> "فشل في المزامنة" must appear ONLY when:
>  * API actually failed
>  * No response / HTTP error
>  * **AND no data saved to ads_daily**

**Fix (classification-only — sync/FX/spend logic untouched):**
1. `sync_account_day` (line ~260): when `fetched is None`, check if
   the existing row has `spend_native > 0`. If yes → record only
   `platform_check_error` + `platform_last_checked_at`; preserve the
   prior `match_status`.
2. `auto_reconcile_for_day` (two token/fetch failure paths): same
   conditional pattern. Token failures and adapter `None` results no
   longer overwrite a valid match_status.
3. `get_reconciliation_report` (display layer): reclassify legacy
   rows on the fly — if `match_status="sync_failed"` but
   `spend_native > 0`, surface as `drift_review` (when drift ≥ 5%) or
   `pending_platform` (when drift < 5%). No DB writes — the row will
   be properly reclassified the next time a successful sync runs.
4. Added `match_status_reason="platform_check_error_with_valid_ssot"`
   annotation so the UI can still surface "API check failed but data
   is valid" if desired.

**Tests** (`tests/test_ads_v2_match_status_ssot.py` — 4/4 ✅;
full suite 18/18 ✅):
- Legacy `sync_failed` + valid data + 7.94% drift → reclassified
  to `drift_review`. Summary counts updated accordingly.
- Truly failed (no SSOT data) → stays `sync_failed`.
- Valid data + small drift (<5%) → reclassified to `pending_platform`.
- Source inspection: confirms iter-259 markers in sync/core.py + the
  `ssot_has_data` gate is present in both failure paths.

**Untouched (per user directive):**
- ❌ Snapchat sync code
- ❌ FX rate fetching
- ❌ Spend calculation
- ❌ Adapter API call logic
- ❌ `ads_daily.spend_native` / `spend_sar` / `bank_fee_sar` values



## Currency SSOT Fix — ads_accounts is Authoritative (2026-06-25 · iter-258)

**Bug reported by user:** Account "متجر أماسي سعودي" is configured as
SAR in settings, but the Ads V2 report showed it as USD with a value
of 721.61 USD. Two screens, two different currencies for the same
account — a classic SSOT violation.

**Root cause (introduced by me in iter-257):**
`get_spend_by_account` was grouping by `ads_daily.currency_native` and
reading it back in projection. But `ads_daily.currency_native` is
whatever the platform adapter wrote — Snapchat's API always returns
USD micros, so Snap-sourced rows have `currency_native="USD"`
regardless of how the account is configured.

The user's SSOT rule (made explicit in this iteration):
- All ACCOUNT-LEVEL settings (currency, bank fee, FX policy) → `ads_accounts`
- All DAILY-SPEND numbers → `ads_daily`
- Reports JOIN the two. They must never duplicate or override.

**Fix (display layer only — sync untouched, ads_daily untouched):**
- `get_spend_by_account` now groups strictly by `(account_id, provider)`,
  joins with `ads_accounts.currency_native`, and uses that as the
  authoritative currency.
- `spend_native` field is set to `null` for SAR-billed accounts (no
  meaningful foreign value exists).
- `totals.spend_native_by_currency` excludes SAR-billed accounts entirely.
- `get_spend_by_provider` was restructured: stage-1 per-account
  aggregation + join with ads_accounts, stage-2 roll-up per provider
  with SAR amounts dropped from `spend_native_by_currency`.
- Frontend `ReportTable.renderCell()` renders `null` `spend_native`
  as "—" with `text-zinc-500`.

**Architectural rules the user codified for Ads V2 going forward:**
1. No duplication in code.
2. No duplication in storage.
3. No more than one source of truth.
4. All account settings come from `ads_accounts`.
5. All daily spend numbers come from `ads_daily`.
6. All reports flow through the unified reports layer.
7. Any material accounting change must be reflected structurally,
   not as a side-fix or duplicated field.

**Tests** (`tests/test_ads_v2_currency_ssot.py` — 3/3 ✅, full suite 14/14 ✅):
- SAR-billed Snap account: USD hidden, totals exclude it.
- USD-billed account: USD shows + totals include it.
- Mixed SAR+USD accounts: isolated cleanly.



## Ads V2 Bank Commission Display (2026-06-25 · iter-257)
**User directive (strict):** FREEZE the Snapchat spend sync logic — current
USD spend + FX conversion is canonical. NO changes to:
- FX rate fetching
- Spend calculation
- Snapchat API call (post the recent adapter fix)
- Sync mechanism

**What user needs visible in the report:**
1. الصرف بالدولار (Spend USD)
2. الصرف بالريال قبل العمولة (Spend SAR pre-commission)
3. نسبة العمولة البنكية (Bank commission %)
4. قيمة العمولة البنكية (Bank commission SAR)
5. إجمالي التكلفة بعد العمولة (Total after commission)

**Implementation (display-only — sync untouched):**
- `/app/backend/ads_v2/data_layer/reports.py`:
  - `get_spend_by_account` now exposes `spend_native`, `bank_fee_pct`
    (derived = bank_fee_sar / spend_sar × 100), and `configured_bank_fee_pct`
    (from settings, audit-only). `totals.spend_native_by_currency` for
    multi-currency aggregation.
  - `get_spend_by_provider` similarly extended with `bank_fee_pct` and
    per-currency native breakdown.
  - All derived from `ads_daily` SSOT — no recomputation, no new
    sync paths.
- `/app/frontend/src/pages/AdsV2Report.jsx`:
  - 5 StatCards (USD card auto-hides when no USD spend).
  - Per-account table: 10 columns including `spend_native` (USD/native)
    and `bank_fee_pct` (effective %).
  - Per-provider table: includes `bank_fee_pct`.
  - `ReportTable` now has `renderCell()` that formats pct and native
    currency with proper suffixes and tooltips.

**Verified scenario (user-provided Snapchat numbers):**
Given `spend_native=105.41 USD`, `spend_sar=395.76`, `bank_fee.rate_pct=0.023`:
- `bank_fee_sar` = round(395.76 × 0.023, 2) = **9.10** ✅
- `gross_sar`    = 395.76 + 9.10        = **404.86** ✅
- Effective `bank_fee_pct` derived from SSOT = **2.299** ≈ 2.30% ✅

**Tests** (`/app/backend/tests/test_ads_v2_bank_fee_report.py` — 5/5 ✅):
- `test_snapchat_bank_fee_matches_user_scenario`
- `test_effective_bank_fee_pct_matches_configured_rate`
- `test_bank_fee_disabled_returns_zero`
- `test_pct_plus_flat_method`
- `test_report_layer_returns_new_fields`

**Live aggregation curl test:** Inserted a temp `ads_daily` row matching
the user scenario; `get_spend_by_account` returned exactly the 6
required fields with the correct numbers.

**⚠️ Sync code is frozen — any future change to spend or FX requires
explicit user approval.**



## Dashboard Shipping SSOT Consolidation + Accordion UX (2026-06-25 · iter-256)
**User report:** ProfitSummaryCard's "إجمالي تكاليف الشحن" total included VAT
but the inline table only showed unit price WITHOUT tax (e.g. iMile 21×15 was
shown but total was 362.25). Also: clicking operating-expenses tooltip felt
like content jumped below the page.

**Fix — Backend (`/app/backend/server.py` ~line 1939):**
- `/api/dashboard` now passes `all_orders` through
  `shipping_cost_ssot.aggregate_breakdown()` and OVERRIDES
  `matched_all["shipping_breakdown"]` / `["total_shipping_cost"]` /
  `["deferred_shipping_cost"]` with the SSOT result.
- Each shipping row now carries SSOT-canonical per-unit fields:
  `cost_per_unit`, `tax_per_unit`, `total_per_unit`, `vat_rate`
  (alongside the legacy fields kept for backward-compat).
- Net effect: the dashboard and `/api/shipping-ledger` now use the
  SAME math source — never any drift.

**Fix — Frontend (`/app/frontend/src/components/ProfitSummaryCard.jsx`):**
- Replaced the hover-only tooltips on الشحن & المصروفات التشغيلية with
  **inline accordion sections** (`expandable`/`expanded` props on `Line`).
- Click the row → accordion expands inline directly below the row.
  Click again → collapses. The rest of the summary stays visible.
- Shipping table columns are now identical to ShippingLedger:
  الشركة · الشحنات · سعر الوحدة (بدون الضريبة) · ضريبة الوحدة (VAT) ·
  إجمالي الوحدة (سعر + ضريبة) · الإجمالي.
- Footer reads: "نفس مصدر دفتر الشحن التفصيلي (shipping_cost_ssot.py)".

**Regression tests** (`/app/backend/tests/test_dashboard_shipping_ssot.py` — 3/3 ✅):
- Verifies SSOT per-unit math matches the user-reported numbers (21 × 15
  → 362.25; 2 × 15 → 34.50).
- Asserts the dashboard source code contains the consolidation block and
  all SSOT canonical fields.
- Verifies `is_deferred` summation stays correct after SSOT consolidation.

**Smoke verified on PREVIEW:**
- `/api/dashboard` returns rows with `cost_per_unit`, `tax_per_unit`,
  `total_per_unit`, `vat_rate` populated. Example:
  سمسا (2040 oc, cpu=25.0, tpu=3.75, total_per_unit=28.75, total=58,539.60).
- Clicking the shipping line in ProfitSummaryCard expands the breakdown
  inline directly below.

**Note for user:** PREVIEW only. Redeploy via Emergent to push to
`mezansalla.com`.



## Snapchat Adapter Bug Fixes (2026-06-25)
**Reported by user via Diagnose UI on two Snapchat accounts:**
1. `efcdd251 (Self Service)` → API error:
   `Unsupported Stats Query: Only field 'spend' should be used when querying AdAccount stats.`
2. `cf8ea7c9 (السعودي)` → API error:
   `Invalid query parameters in request URL: [Invalid StartDateTime, 2026-06-24T00:00:00.000 03:00]`
   (the '+' in the +03:00 timezone offset became a space)

**Root cause (both bugs in `/app/backend/ads_v2/sync/adapters.py::fetch_snapchat_day`):**
- The Snapchat API endpoint `/adaccounts/{id}/stats` ONLY accepts the
  `spend` field at the account level. Asking for impressions/swipes is rejected.
- The URL was built with f-string interpolation
  (`f"...&start_time={start_iso}&end_time={end_iso}..."`), so the
  `+03:00` timezone offset stayed as a literal `+` in the URL — which
  is decoded by HTTP servers as a space character (RFC 3986 query rules).

**Fix:**
- Removed `impressions,swipes` from the field list; account-level
  endpoint now requests `fields=spend` only.
- Switched from f-string URL to httpx `params=` dict so `start_time`
  and `end_time` are URL-encoded correctly (`+` → `%2B`).
- `impressions`/`clicks` set to `0` in the returned row (Snapchat does
  not expose those at account level — would need campaign-level later).

**Regression tests:**
- `/app/backend/tests/test_snapchat_adapter_fix.py` (3 tests, all PASS):
  - asserts source no longer has the buggy URL shape
  - end-to-end captures the final httpx URL and verifies `%2B` encoding
  - verifies spend parsing from a stub response works

**Note for user:** This fix is on PREVIEW. To apply on PRODUCTION
(`mezansalla.com`), the app must be redeployed from the Emergent
platform.


- Arabic-only UX (RTL)

## Implemented in this Session (Iter-250b · P1.5)

### P1.5.L — BNPL Internal-Transfer Block (deployed)
- Block bank/cash → Tamara/Tabby in `/new-transaction`
- Backend guard in `universal_accounting_routes._account_blocks_internal_transfer`
- Frontend filter in `UnifiedEntryScreen.isInternalTransferIneligible`
- Salla NOT blocked

### P1.5.n — Employee Lookup Forensic (deployed)
- `GET /api/audit/employee-lookup?entity_id=...&name_hint=...`
- Read-only diagnostic: matches across `operating_salaries`, `employees`, `employees_archive`, `employees_legacy`

### P1.5.o — Preview Debug Overlay (deployed)
- Shows selected employee {id, name, monthly_amount, source} ONLY on Preview/localhost
- Helps verify frontend bindings

### P1.5.p — Widened Employee Guard (deployed)
- `ledger_core.create_entry` guard now checks BOTH `operating_salaries` AND `employees`
- Rejects `archived=true / deleted=true`
- Accepts `status=active|stopped`
- 7/7 unit-test PASS

### P1.5.q — Custody as Payment Source for Operating Expenses (deployed)
- New perm `accounting.custody.spend_any` (owner/admin/accountant/user role)
- `POST /api/financial-movements` accepts `custody_employee_id` for `general_expense`
- New endpoint `GET /api/accounting/custody/spendable-sources`
- UI toggle in `Iter245MovementForm`: bank/cash vs employee custody
- Custody balance check, no overdraft, single source per transaction
- Custody-funded movements credit `employee.custody` in GL (no bank touched)
- Reversal restores custody balance via standard GL reversal

### P1.5.r — Entity Ledger Deep-Link Route (deployed)
- Route `/entity-ledger/:type/:id`
- Page `EntityLedgerByIdPage` auto-opens drawer for the matching entity
- Backward-compat: `/entity-ledger/supplier/:id` now redirects to `/suppliers/:id/ledger-detail`

### P1.5.s — Supplier Ledger Detail Page (deployed)
- Backend: `GET /api/accounting/suppliers/{id}/ledger-detail?from=&to=`
- 7 sections: supplier card, drift banner, period summary, chronological timeline, invoice cards (with line items + GL legs + payments), manual entries, drift diagnostic
- Frontend: `/suppliers/:id/ledger-detail` page
- Print/PDF via `react-to-print`
- Filters: YTD (default), current month, last 90d, all, custom
- SSOT-strict: all balances from GL, `financial_movements` for detail only

### P1.5.t — Movements↔GL Drift Analyzer (deployed)
- `GET /api/audit/movements-gl-drift?from=&to=&movement_type=`
- Categorises every drifted movement into 6 causes:
  legacy_pre_gl / gl_creation_failed / no_group_id_at_all / voided_or_draft / import_batch / manual_legacy_data
- Roll-ups by cause, supplier, year
- Read-only — pure diagnostic, no writes

## P1.5.s.fix — Supplier Ledger Cash-Invoice Reclassification (2026-02 · READ-ONLY)
Backend: `supplier_ledger_detail_routes.py` reconciliation block now classifies orphans into 3 buckets:
  - `cash_invoices` — paid_amount ≥ total_amount AND GL exists for the group_id (just no payable leg). Valid postings, NOT drift.
  - `drift_credit` — paid_amount < total_amount AND no supplier-payable leg in GL. Real drift, needs review.
  - `ledger_failed` — no GL row at all for group_id (or status=ledger_failed). True GL post failure.
Period block exposes `total_cash_purchases` + `cash_invoices_count`. `drift_detected` no longer fires on cash invoices.
Frontend: `SupplierLedgerDetailPage.jsx` now renders 3 separate sections (📗 / 🟠 / 🔴) instead of one «orphan» bucket. New summary card «إجمالي مشتريات نقدية». Excel export splits to 3 sheets.

## Phase 2A.5 — Provider Invoice Calendar (2026-02 · CORE FIX)
**Problem solved:** Tamara Dry-Run used arbitrary ISO-week buckets from order_date, so simulated invoice_date diverged from real Tamara dates (23/05, 30/05, 06/06, 13/06, 20/06).

**User-confirmed Tamara cycle:** invoice issued Saturday → period covers Saturday → next Friday (`invoice_date` is the FIRST day of the 7-day period, not the last). All real invoice dates are Saturdays.

Backend:
  - New module `provider_invoice_calendar.py`:
      • Per-provider `_PERIOD_LAYOUTS`:
          – `tamara` → `"invoice_as_start"` (Sat → Fri).
          – `tabby`/`imkan`/`salla` → `"invoice_as_end"` (legacy).
      • Overridable via `settings.calendar_period_layout_<provider>`.
      • `extract_calendar_from_settlement_entries`: walks distinct `settlement_date` rows. For `invoice_as_start`, period_start=invoice_date, period_end=invoice_date+6. For `invoice_as_end`, period_start=prev_invoice+1 (or invoice-6 for first), period_end=invoice_date.
      • Transfer offset depends on layout: Tamara `invoice_as_start` defaults to 9 days (Mon after Fri end). Tabby `invoice_as_end` defaults to 1.
      • `rebuild_calendar` (idempotent, preserves manual entries).
      • `upsert_manual_entry`, `delete_entry`.
  - New collection: `provider_invoice_calendar` with `(user_id, provider, invoice_date)` unique key.
  - Modified `_simulate_weekly` (Dry-Run): when calendar exists → uses calendar periods exactly; orders bucket by `period_start ≤ order_date ≤ period_end`. Surfaces `invoice_date` + `expected_transfer_date` per invoice. Falls back to ISO-week buckets only when calendar is empty.
  - Modified `_build_bnpl_periods` (Phase 2B): identical change — calendar → `compute_settlement_for_provider(period_start, period_end)` per entry.
  - Rule resolution (commission/VAT) **still** comes from `_merchant_fee_rates` — calendar only governs period boundaries.

Endpoints:
  - `GET    /api/settlement-engine/calendar?provider=&from_date=&to_date=`
  - `POST   /api/settlement-engine/calendar/rebuild` body `{provider, dry_run}`
  - `POST   /api/settlement-engine/calendar/manual` body `{provider, invoice_date, period_start, period_end, expected_transfer_date}`
  - `DELETE /api/settlement-engine/calendar/{id}`

Frontend: `SettlementDashboard.jsx`
  - New tab "📅 تقويم الفواتير" — provider picker, **dynamic layout badge** (Tamara: "تاريخ الفاتورة = أول يوم الفترة (السبت → الجمعة)" vs Tabby: "= آخر يوم الفترة"), rebuild button, manual-add form, per-invoice table with source badge, delete action.
  - Dry-Run modal table now shows columns: تاريخ الفاتورة + تاريخ التحويل المتوقع (real calendar dates).

Tests: `tests/test_iter251_phase2a5_invoice_calendar.py` — 5/5 PASS.
  - Layout-aware extraction (2026-05-23 → period 23-29), idempotent rebuild, manual-entry protection, end-to-end Dry-Run uses calendar with Sat→Fri buckets, delete.

## Phase 2B — Settlement Engine Generation (2026-02 · FEATURE-FLAG GATED)
Backend:
  - New module `settlement_engine_generation.py` — pure generation logic that delegates rule resolution to:
      • `bnpl.settlements_service.compute_weekly_settlements` + `_merchant_fee_rates` for Tamara/Tabby (same source as `/bnpl-settlements/register`).
      • `db.settlement_entries` grouped by `settlement_reference` for Salla.
      • `imkan` returns `rule_source_missing` (no central rules yet — no hard-coded fallback).
  - New collections: `settlement_periods`, `settlement_invoices`, `expected_transfers` (linked via FK ids).
  - Invoice lifecycle: draft / generated / waiting_transfer / pending_review / confirmed / confirmed_with_difference / cancelled.
  - All writes gated by `settings.settlement_engine_enabled` (defaults OFF). 403 returned when disabled.
  - `dry_run=true` ALWAYS allowed (no persistence) — for the merchant to preview output safely.
  - Idempotent on `(user_id, provider, period_from, period_to)`: re-runs reuse existing ids.
  - No GL writes, no bank_transfer_review creation here. Phase 2C will wire those in.

Endpoints (under `/api/settlement-engine`):
  - `POST /generate` — body `{provider, date_from, date_to, dry_run}` → counts + ids
  - `GET  /periods?provider=&status=&from_date=&to_date=`
  - `GET  /invoices?provider=&status=&from_date=&to_date=`
  - `GET  /invoices/{id}` → invoice + period + expected_transfer
  - `POST /invoices/{id}/cancel` body `{reason}`
  - `GET  /expected-transfers?provider=&status=`
  - `GET  /stats` → totals + per-provider counts

Frontend: `SettlementDashboard.jsx`
  - Two tabs: 🔬 Dry-Run | 📦 Generated Invoices (Phase 2B)
  - Generated tab shows: feature-flag banner (ON / OFF), counts, generation form (provider, date range, Dry-Run / Generate buttons), invoices table with status badges, cancel action.
  - "Generate" button disabled when `settlement_engine_enabled` flag is OFF.

Tests: `tests/test_iter251_phase2b_settlement_generation.py` — 6/6 PASS.
  - Block when flag OFF, dry-run persistence-free, Salla persistence + linking, idempotency, cancel transitions, unknown-provider rule_source_missing.

## Pending / Backlog
- [P0] Analyze Tamara settlement JSON (26,279.64 vs 10,509.12 SAR discrepancy) — waiting for user to re-paste
- [P1] Analyze Production drift report from P1.5.t (waiting for user output)
- [P1] Phase 2 — Custody as payment source for supplier_invoice (cash mode)
- [P1] Phase 3 — Unified Employee Custody Ledger (chronological timeline of all custody movements per employee)
- [P1] Ad-Account sync stopped for Snapchat (Riyadh) — needs forensic
- [P1] Read-only forensic for `/purchase-invoices` and `/shipping/transfers`
- [P1] Walid / Khatai employee balance analysis (read-only)
- [P2] Execute Financial Reset / Ad-Account Recompute (postponed)
- [P2] Category Reports & Expense Analysis Dashboard
- [P2] Product Linkage (Inventory, SKUs)
- [P2] Phase 2 of Supplier Unification — provide a (gated) "Link Ledger-only supplier to db.suppliers" action once user reviews the forensic report

## P1.5.ab — Suppliers Unification (2026-02 · READ-ONLY)
Backend: `suppliers_unification_forensic_routes.py`
  - `GET /api/suppliers-unified` — merged list (db.suppliers + db.counterparties + GL/FM ghosts) with `link_status` ∈ {new_only, linked, ledger_only}, `editable` flag, GL balance per row
  - `GET /api/audit/suppliers-unification-forensic` — full diagnostic dump: counts, lists per category, ghosts (GL/FM IDs missing from both tables), duplicate suspects by name/phone/email
Frontend:
  - `SuppliersPage.jsx` Management tab now calls `/suppliers-unified` instead of `/suppliers` and shows badges + summary cards + link-status filter
  - `SuppliersUnificationForensicModal.jsx` — modal with 6 tabs (نظرة عامة، مورد جديد، Ledger فقط، مربوط، GL/FM أيتام، تكرارات مُشتبه بها)
Tests: `tests/test_p15ab_suppliers_unification_forensic.py` — 3/3 PASS

## Phase 4 — Product Cost Auto-Update on Supplier Invoice (2026-02)
Backend: `financial_movements_routes._apply_product_cost_updates`
  - Hook fires after a `supplier_invoice` movement is successfully posted to GL.
  - Walks every `line_items[i].product_id`; for each match:
    - Appends a new `cost_history` record with `{supplier_id, supplier_invoice_id, invoice_date, quantity, unit_cost, total_cost, source: "supplier-invoice", amount, at}`.
    - Sets `cost_current = unit_cost` (latest).
    - Recomputes `cost_avg` as quantity-weighted average across all history entries that carry `quantity`+`unit_cost`.
    - Sets `needs_cost = false`.
  - APPEND-ONLY: never deletes or overwrites prior history entries (excel-import / quick-create seeds preserved).
  - Failures are logged but never break invoice creation.
Frontend: `Iter245MovementForm.jsx` payload now sends `product_id` + `product_sku` per line item.
Tests: `tests/test_iter250b_phase4_product_cost_update.py` — 5/5 PASS. End-to-end curl verified weighted avg ((10×15 + 30×7) / 40 = 9.0).


## Test Credentials
See `/app/memory/test_credentials.md`.

## Shipping Cost SSOT — Priority Flip + Warning Banner (2026-06-25, iter-254/255)
**Bug fix:** User reported that the detailed shipping ledger was using
Salla's per-order shipping_cost even for companies that had a
configured `cost_per_order` in `/shipping/settings`. The new policy:

**Priority 1 — company-config `cost_per_order`** (the rate the merchant
maintains in `/shipping/settings`).
**Priority 2 — Salla `shipping_cost`** ONLY when no system cost is
configured (temporary fallback; UI surfaces a warning).

**SSOT change** (`shipping_cost_ssot.py::shipping_breakdown`):
   The `if order_ship>0 else cfg_cost` branch was flipped to
   `if cfg_cost>0 else salla_ship`. Source field now reports
   `company_config | salla | none`.

**Warning system** (`shipping_ledger_routes.py`):
   Per-company breakdown now sets `uses_salla_fallback = (
   from_salla_count > 0 AND configured_cost <= 0)`. Each affected
   company yields a structured warning emitted in the top-level
   `warnings` array:
       {shipping_company, orders_affected, reason,
        message: "شركة الشحن … لا يوجد لها سعر في إعدادات شركات الشحن…"}
   The frontend renders an amber banner above the per-company table
   with a "الانتقال إلى إعدادات شركات الشحن" link.

**Coverage** — all 4 consumers now go through SSOT:
   1. ✅ `/api/shipping-ledger` (detailed orders + per-company)
   2. ✅ `/api/shipping-accounts` (deferred-liability accrual) —
        `compute_owed_per_company` rewritten to call
        `shipping_breakdown` per order (replacing the inline
        `cost*(1+vat)` formula that bypassed SSOT priority).
   3. ✅ `/api/balances` (Phase-1 splits via `compute_balances`)
   4. ✅ `/api/financial-position` (same balances wiring)

**Frontend** (`ShippingLedger.jsx`):
   - Amber warning banner with per-company message + settings link
   - "من سلة (مؤقت)" badge on Salla-fallback rows
   - Per-company row gets amber tint when fallback active

**Tests:**
   - `test_shipping_cost_ssot.py` — 15/15 PASS (priority flip
     verified in unit tests).
   - `test_shipping_accounts_ssot_iter255.py` — 6/6 PASS
     (legacy `/shipping-accounts` path now SSOT-consistent).
   - Verified by `testing_agent_v3_fork` iter-254 (100% backend +
     frontend) and iter-255 (100% backend, 21/21 tests). Critical
     proof scenario: Salla=999, settings=20 @ 15% VAT → owed=23.00
     per order (not 1148.85).

## Shipping Cost SSOT — Base + Tax + Total (2026-06-25)
**User mandate:** every shipping-cost figure in the app uses
`total = base + tax`, with the three values visible separately. No
default VAT% is fabricated for historical data; the actual configured
`vat_percent` per shipping company in `/shipping/settings` is the only
source of truth.

**New module:** `/app/backend/shipping_cost_ssot.py` exposes:
   - `shipping_breakdown(order, company_cfgs) → {base, tax, total,
     vat_rate, source}`  – one shipment.
   - `aggregate_breakdown(orders, company_cfgs)` – list aggregation
     with per-company stats (cost_per_unit, tax_per_unit,
     total_per_unit).
   - `get_company_configs(db, user_id)` – reads from the canonical
     `settings.shipping_companies[]` array and accepts BOTH
     `vat_rate` (decimal) AND `vat_percent` (0–100). Name lookup is
     resilient to casing + accidental quoting (`'مندوب الرياض'`).

**Latent bug fixed:** previous `shipping_accounts.py` read
`cfg.get("vat_rate")` but settings stored `vat_percent`. So VAT was
silently treated as 0 everywhere. Now both fields are recognised.

**Refactored consumers (all calls go through SSOT):**
   - `balances.py::compute_balances` — new optional `company_cfgs`
     param. Without it, no fake default VAT (legacy callers unchanged).
   - `shipping_accounts.py::compute_owed_per_company` — returns
     `shipping_base`, `shipping_tax`, `shipping_cost` (= base+tax)
     per company + totals.
   - `shipping_ledger_routes.py::shipping_ledger` — rows now expose
     `shipping_base`, `shipping_tax`, `shipping_cost`,
     `shipping_vat_rate`. Per-company block adds `cost_per_unit`,
     `tax_per_unit`, `total_per_unit`.
   - `server.py` — both `/balances` and `/financial-position` callers
     pass `company_cfgs` so FP, P&L, executive summary, and balances
     align with the rule.

**Frontend:**
   - **Shipping Ledger detail page** (`ShippingLedger.jsx`): 6-column
     per-company table — الشركة · عدد الشحنات · سعر الوحدة (بدون الضريبة)
     · ضريبة الوحدة (مع %) · إجمالي الوحدة (سعر + ضريبة) · الإجمالي.
     Order rows split shipping_base / shipping_tax / shipping_cost.
     Summary cards split: "إجمالي سعر الشحن" + "إجمالي ضريبة الشحن"
     + "إجمالي تكلفة الشحن (شامل الضريبة)".
   - **Profit Executive Summary** (`ProfitSummaryCard.jsx`): same
     6-column table for the analysis-report shipping breakdown.

**Historical-data preservation:** the SSOT helper recomputes live
reports from the current `vat_percent` setting. POSTED `general_ledger`
entries are never mutated — they keep whatever VAT was applied when
they were originally written. Reports = dynamic, journal = immutable.

**Tests:** `tests/test_shipping_cost_ssot.py` — 13/13 PASS,
   including the bug-fix cases (`vat_percent` recognised, malformed
   inputs, historical preservation when no cfg present).

## Ads V2 — Snapchat Safe Re-link Flow (2026-06-25)
**Resolved user request:** "زر إعادة ربط Snapchat داخل تقرير التشخيص"
with 7 explicit safety constraints. All 7 are enforced + tested.

**New collection:** `ads_v2_pending_tokens`
   - Stores new tokens in isolation from V1 until the merchant
     explicitly approves them. Schema: `{id, user_id, provider,
     status (awaiting_callback|pending|approved|discarded),
     access_token, refresh_token, expires_at, source (oauth|
     manual_paste), comparison_snapshot, created_at, updated_at}`.

**New backend module:** `/app/backend/ads_v2/relink.py`
   Routes (all under `/api/ads-v2/settings/snapchat/relink`):
   - `POST /start` → returns Snapchat OAuth URL (state JWT carries the
     V2 purpose marker `ads_v2_snapchat_relink`).
   - `POST /manual` → fallback path for pasting tokens directly.
   - `GET /pending` → list (never returns the secret tokens).
   - `POST /{id}/compare` → live probes both old V1 token and new
     pending token; returns side-by-side identity + organizations +
     ad_accounts + can_access_self_service + can_access_riyadh + diff.
   - `POST /{id}/approve` → backs up V1 doc into `legacy_versions[]`
     array, then atomically swaps `access_token`/`refresh_token` to
     the new pending values. Audit logged in `ads_sync_logs` as
     event `account_relinked_v1`.
   - `POST /{id}/discard` → soft-marks discarded (kept for audit).

**OAuth handshake (zero new redirect URI needed):**
   The V2 flow reuses V1's `client_id`/`client_secret`/`redirect_uri`.
   The V1 OAuth callback (`/api/snapchat/oauth/callback`) was extended
   with a single dispatch check: if the JWT state has
   `purpose=ads_v2_snapchat_relink`, the request is handed off to
   `relink.handle_v2_relink_callback()` which writes ONLY to
   `ads_v2_pending_tokens`. V1 callback's existing logic untouched
   for legacy states.

**Snapchat API probe (`_probe_snapchat_token`):**
   Queries `/me`, `/me/organizations`, `/organizations/{id}/adaccounts`.
   Heuristically detects "Self Service" and "Riyadh" access by
   matching name patterns. Returns a normalized snapshot used by
   both `/compare` and the cached `comparison_snapshot` field.

**Frontend (`AdsV2Settings.jsx`):**
   - `RelinkSnapchatPanel` — shown inside the Diagnose dialog
     only when `provider==='snapchat' && token in
     ['needs_relink','expired','missing']`. Two CTAs: "بدء OAuth"
     and "إدخال يدوي (احتياطي)".
   - `RelinkComparisonView` — two-column side-by-side compare with
     org/account lists, Self Service / Riyadh access indicators,
     diff summary (added/removed orgs and accounts), red callout if
     the new token loses any access, then "اعتماد" / "تجاهل"
     buttons. The approve button is disabled if new token isn't
     valid (probe returned `unauthorized`).
   - `useEffect` reads `?relink_pending_id=...` from URL after OAuth
     round-trip and auto-loads comparison.

**Safety invariants (all in pytest):**
   1. ✅ V1 NOT touched by `/start` — verified `test_relink_start_does_not_touch_v1`
   2. ✅ V1 NOT touched by `/manual` — verified `test_relink_manual_stores_pending_v1_untouched`
   3. ✅ V1 NOT touched by `/compare` — verified `test_compare_does_not_modify_v1`
   4. ✅ V1 NOT touched by `/discard` — soft-discard only — verified `test_relink_discard_is_soft`
   5. ✅ `/approve` appends `legacy_versions[]` AND atomically swaps —
        verified `test_approve_appends_legacy_and_swaps`
   6. ✅ Pending without access_token CANNOT be approved (returns 404)
        — verified `test_approve_awaiting_callback_returns_404`
   7. ✅ Tokens never leak in list endpoints — verified
        `test_relink_pending_omits_secrets`

**Tests:** `/app/backend/tests/test_ads_v2_snapchat_relink.py` — 8/8 PASS.
   Total ads_v2 tests: 39/39 passing (relink + diagnose + auto-reconcile
   + drift + phase1).

## Ads V2 — Phase 1 (3-Tier Status + Diagnostics) (2026-06-25)
**User complaint resolved:** Snapchat row showed "Token: OK" but
"Status: خطأ" — paradoxical and uninformative. Replaced with a
3-tier per-account status model + a Diagnose button.

**Backend:**
   - `data_layer/settings.py::_compute_account_status()` — returns
     `{token, connection, connection_reason, sync_run, reason,
       days_with_data_30d, last_sync_finished_at, last_sync_error}`
     where each tier has its own controlled vocabulary:
       - **token:** ok / expired / needs_relink / missing
       - **connection:** connected / unreachable / timeout / api_error / unknown
       - **sync_run:** synced / awaiting_first / no_data / last_failed / disabled
   - `_compute_account_status` mixes V1 token health + recent sync_logs
     api_status + ads_daily row count to produce a structured `reason`
     code (e.g. `token_no_access_to_account`, `no_data_for_account`,
     `awaiting_first_sync`, `api_rate_limit`). Translated to Arabic
     in the UI dictionary `REASON_AR`.
   - `data_layer/settings.py::diagnose_account()` — comprehensive
     read-only diagnostic. Includes:
       - Token check (V1 doc presence)
       - **Live API probe** — calls `adapters.fetch_day` for yesterday
         and records the result (code, body excerpt, fetched spend)
       - ads_daily stats: days_in_last_30d, days_with_spend,
         total_daily_rows, last_synced_date, last sync started/finished
       - Last 10 ads_sync_logs events for the account
   - **POST /api/ads-v2/settings/accounts/{id}/diagnose** — Returns
     the full diagnostic in one payload.

**Frontend (AdsV2Settings.jsx):**
   - Accounts table replaced bare "Status / Token" columns with:
     **حالة التوكن / حالة الاتصال / حالة المزامنة / السبب الحقيقي**
     (4 columns, colored badges, never says bare "خطأ").
   - New **"تشخيص"** button per account → opens a Dialog displaying:
     3-tier badges, primary reason callout, stats grid, live API probe
     result + raw response body excerpt, last 10 events.
   - `EVENT_AR` dictionary translates event names (sync_run → "مزامنة
     ناجحة", reconciliation_checked → "مطابقة من المنصة", etc.).
   - `ActivityRow` component renders each event as a clean Arabic
     summary instead of raw JSON dump.
   - `REASON_AR` translates 17 specific reason codes (e.g.
     `token_no_access_to_account` → "التوكن لا يملك صلاحية هذا الحساب",
     `no_data_for_account` → "الحساب لا يحتوي على بيانات صرف").

**Tests:** `tests/test_ads_v2_account_diagnose.py` — 8/8 PASS,
   including the exact "Token OK + no data" case the user described,
   which now produces `reason='no_data_for_account'` instead of
   bare "error". Total 31/31 ads_v2 tests pass.

## Ads V2 — Phase 1 (Auto-Reconcile, Final) (2026-06-25)
**Resolved User 5-point Conditional Approval:**
1. ✅ **API-driven auto-fetch, manual demoted to fallback** —
   New endpoint **POST /api/ads-v2/report/auto-reconcile** body
   `{dates:[...], account_ids?:[...]}` re-queries every enabled
   (account × date) from its provider API and stores the freshly-
   fetched figure in **shadow** fields `platform_authoritative_native`,
   `platform_authoritative_sar`, `platform_last_checked_at` — without
   touching `spend_native` (the SSOT row stays stable for Phase 2
   review). Manual entry endpoint kept but UI button renamed
   "إدخال يدوي (احتياطي)".
2. ✅ **Enhanced reconciliation report fields** — Per (account, date):
   `spend_native/sar` (ads_daily), `platform_authoritative_*` (current
   API), `diff_native`, `diff_sar` (signed), `drift_pct_vs_platform`,
   `drift_reason.likely_causes` (Arabic), `confidence`,
   `last_synced_at`, `platform_last_checked_at`, `match_status`.
3. ✅ **Unified Meta/Snapchat/TikTok** — Single `auto_reconcile_user()`
   loop dispatches through `adapters.fetch_day()`. Token-missing path
   degrades to `match_status='sync_failed'` (no 500).
4. ✅ **Phase 2 boundary intact** — Zero writes to `general_ledger` and
   zero `ledger_txn_group_id` on any ads_daily row. Verified by
   dedicated invariant tests post auto-reconcile.
5. ✅ **Status indicators 🟢🟡🟠🔴⚪** — New `_compute_match_status()`
   returns one of `matched / pending_platform / drift_review /
   sync_failed / no_data` (priority order: failed > no_data >
   drift_review > pending_platform > matched). UI renders 5-card
   legend at top of reconciliation tab + colored badge per row with
   emoji icon.

**Backend additions:**
   - `core.py`: `_compute_match_status`, `auto_reconcile_for_day`,
     `auto_reconcile_user`. `run_sync_for_account` now also sets
     `match_status` on every sync (and `sync_failed` when fetch fails).
   - `reports.py`: reconciliation rows expose new fields + summary
     histogram (`match_matched`, `match_pending_platform`,
     `match_drift_review`, `match_sync_failed`, `match_no_data`).
   - `routes.py`: POST `/report/auto-reconcile` (bulk) and
     `/report/auto-reconcile/account/{id}/day/{date}` (single).

**Frontend (AdsV2Report.jsx):**
   - Blue button "إعادة المطابقة من المنصات" beside green sync button.
   - Default tab is now "المطابقة" (recon).
   - 5-card legend (MatchStatCard) showing counts per status with
     colored borders matching the indicator color.
   - 6 new table columns: الحالة (with emoji badge), قيمة المنصة الآن,
     قيمة Ads Manager (يدوي), الفرق (SAR), سبب الفرق, آخر مزامنة,
     آخر فحص للمنصة.
   - Dictionary `MATCH_STATUS_AR` maps backend status → icon + Arabic
     label + Tailwind color classes.
   - Manual dialog re-labeled "إدخال يدوي (احتياطي)" + explanatory
     banner pointing users to the auto-reconcile button.

**Tests:** `tests/test_ads_v2_auto_reconcile.py` — 6/6 PASS;
   `tests/test_ads_v2_auto_reconcile_invariants_iter253.py` — 5/5 PASS;
   Phase 1 + drift regressions — 17/17 still PASS. Total 28/28.
**Verified by testing_agent_v3_fork (iter-253):** Backend 100%,
   Frontend 100%, all 5 demands satisfied. **Phase 1 ready for final
   user sign-off.**

## Ads V2 — Phase 1 (Final, post-rejection fix) (2026-06-25)
**Resolved User Rejection (3 demands):**
1. ✅ **Full Arabic UI** — Replaced all English UI terms (Reconciliation,
   Drift, Flags, Confidence, Status, Pending, Provisional, Final, Source,
   Layer, Sync, Token, OK, active, paused, discovered) with proper Arabic
   via dictionaries in `AdsV2Report.jsx` (PROVIDER_AR, REVIEW_STATUS_AR,
   CONFIDENCE_AR, ANOMALY_AR, DRIFT_CAUSE_AR) and `AdsV2Settings.jsx`
   (`statusAr()` helper). Only platform names (Meta/Snapchat/TikTok)
   remain in English.
2. ✅ **Contrast & font-weight upgrade** — Stat cards: `text-3xl
   font-extrabold tabular-nums text-zinc-50` (was text-2xl font-bold
   text-zinc-100). Table cells: `text-zinc-50 font-semibold`. Backgrounds
   stay zinc-900/950. Verified by test agent.
3. ✅ **Meta discrepancy 36.06 SAR** — Adopted "merchant-as-ground-truth"
   model:
   - **POST /api/ads-v2/report/manual-value** `{account_id, date,
     manual_value_native, note?}` → records the Ads Manager value
     entered by the merchant and **recomputes drift instantly** (no
     provider re-fetch). Audit row appended to `ads_sync_logs`.
   - Reconciliation rows now expose `platform_manual_value_native/_sar`,
     `has_manual_value`, `drift_pct_vs_manual`, and structured
     `drift_reason.likely_causes` (sync_before_close,
     late_reporting_window, ads_manager_value_differs,
     post_close_provider_update, missing_fx_rate).
   - `_compute_anomaly_flags` returns **`None` (not 0.0)** for drift
     when there is no comparison anchor → frontend renders "—".
     Eliminates the "false 0% drift" issue.
   - Meta adapter (`adapters.py`) upgraded with
     `use_account_attribution_setting=true`,
     `use_unified_attribution_setting=true`, `limit=500`,
     `account_currency` & `date_start/date_stop` echoed back, ensuring
     numbers track Ads Manager's stated attribution.
**Frontend additions:**
   - `ManualValueDialog` component — Per-row "إدخال قيمة Ads Manager"
     button → modal entry with native-currency value + optional note;
     on save calls the new endpoint and refreshes recon view.
   - `ReconRow` shows colored drift % (emerald/amber/red) ONLY when a
     comparison exists; em-dash otherwise; likely-causes printed as
     Arabic captions beneath the % value.
**Tests:** `tests/test_ads_v2_drift_logic.py` — 7/7 PASS (drift NULL
   when no anchor, manual-value endpoint persistence, reconciliation
   field exposure, no_drift_inflation invariant). Phase 1 regression
   `tests/test_ads_v2_phase1.py` — 10/10 still PASS.
**Verified by testing_agent_v3_fork (iter-252):** Backend 100%
   (17/17), Frontend 95% (all flows pass). Phase 1 ready for user
   sign-off.

## Ads V2 — Phase 1 (2026-06-24) — superseded by post-rejection fix above
Backend (new):
  - `ads_v2/sync/adapters.py` — Meta/Snap/TikTok day-fetchers (read-only,
    use V1 access_token via v1_token_ref). Snap uses TZ-anchored TOTAL
    granularity; Meta uses level=account `time_increment=1`.
  - `ads_v2/sync/core.py` — `run_sync_for_account()` and
    `run_sync_user()`. Idempotent upsert into `ads_daily` keyed by
    `idempotency_key`. Reconciliation drift + anomaly flags embedded
    on the same row. Tracks `sources_count` (re-sync increments).
  - `ads_v2/data_layer/reports.py` — SSOT readers
    (`get_spend_by_day`, `get_spend_by_account`, `get_spend_by_provider`,
    `get_daily_rows`, `get_reconciliation_report`, `get_sync_health`).
    Every response carries `meta.source_layer` + `meta.ssot`.
  - `ads_v2/routes.py` — `/sync/run`, `/sync/account/{id}/day/{date}`,
    `/sync/health`, `/report?group_by=day|account|provider`,
    `/report/reconciliation`, `/report/daily`.
Frontend (new):
  - `pages/AdsV2Report.jsx` — 4 tabs (by day/account/provider/
    reconciliation) + "مزامنة الفترة الآن" trigger + SSOT footer
    badge.
  - UI fixes: `FInput`/`FLabel`/`FSelect*` wrappers enforce white text
    on dark backgrounds across all Ads V2 forms.
Tests: `tests/test_ads_v2_phase1.py` — 10/10 PASS.
Verified: 5 days of Meta data fetched, totals match across all three
groupings (3203.9 SAR), idempotency holds, V1 untouched, no GL writes.
Pending: Snap & TikTok sync — Snap token on preview is `token_invalid`
so live verification awaits production deploy.

## Ads V2 — Phase 0 (2026-06-24)
Approved design: `/app/memory/ADS_V2_FINAL_DESIGN.md` (simplified, 4-collection).
Backend (new):
  - `ads_v2/__init__.py` · `ads_v2/models.py` · `ads_v2/routes.py`
  - `ads_v2/data_layer/discovery.py` — reads V1 tokens read-only,
    lists Meta/Snap/TikTok ad accounts; falls back to V1 cached
    collections when token call fails.
  - `ads_v2/data_layer/settings.py` — CRUD for `ads_accounts`, FX
    & bank_fee patches, audit log to `ads_sync_logs`.
  - `server.py` — `_ads_v2_ensure_indexes()` on startup; mounts
    `/api/ads-v2/*` router.
Frontend (new):
  - `pages/AdsV2Settings.jsx` — 4 tabs (الحسابات/العملة/العمولات/المراجعة)
  - `App.js` route `/ads-v2/settings`, `Sidebar.jsx` entry under
    "إدارة التشغيل".
Collections created (Phase 0): `ads_accounts`, `ads_daily`, `ads_sync_logs`.
Invariants (verified by tests): NO writes to general_ledger, NO writes
to ads_daily, NO modification to snapchat_connections / meta_connections,
NO OAuth flow triggered.
Tests: `tests/test_ads_v2_phase0.py` — 11/11 PASS.

## Iter-251 v12 — Ad-Spend Scheduler Diagnostics (2026-06-24, READ-ONLY)
  - `ad_spend_scheduler_diagnostics.py` — new `/api/ad-spend-rca/scheduler-diagnostics`
    endpoint returning: (1) heartbeat history from `cron_runs` filtered by
    iter-215 types, (2) per-counterparty dry-run preview computing
    cumulative_spend / would-be AM & PM amounts / skip reasons WITHOUT
    writing to GL, (3) selected snapchat ad accounts state,
    (4) ads_currency_settings snapshot, (5) raw source row samples.
  - `server.py` — instrumented `_ad_spend_window_post_loop` to persist
    heartbeat rows into `cron_runs` on every loop tick (types:
    `ad_spend_window_post_loop_start`, `ad_spend_window_catchup`,
    `ad_spend_window_post`) with per-row `skipped_reasons` histogram.
Tests: `tests/test_iter251_v12_scheduler_diagnostics.py` — 3/3 PASS
Purpose: Conclusively determine WHY iter-215 is skipping all 486
counterparties in Production (per-account blocker / reason histogram).

## Tests
- `/app/backend/tests/test_shipping_accounts_ssot_iter255.py` — 6/6 PASS (priority flip + accrual SSOT)
- `/app/backend/tests/test_shipping_cost_ssot.py` — 15/15 PASS
- `/app/backend/tests/test_ads_v2_snapchat_relink.py` — 8/8 PASS (safe re-link flow)
- `/app/backend/tests/test_ads_v2_account_diagnose.py` — 8/8 PASS (3-tier status + diagnose)
- `/app/backend/tests/test_ads_v2_auto_reconcile.py` — 6/6 PASS (Phase 1 auto-reconcile)
- `/app/backend/tests/test_ads_v2_auto_reconcile_invariants_iter253.py` — 5/5 PASS
- `/app/backend/tests/test_ads_v2_drift_logic.py` — 7/7 PASS
- `/app/backend/tests/test_ads_v2_phase1.py` — 10/10 PASS
- `/app/backend/tests/test_ads_v2_phase0.py` — 11/11 PASS
- `/app/backend/tests/test_p15L_bnpl_transfer_block.py` — 11/11 PASS
- `/app/backend/tests/test_p15p_employee_guard_widened.py` — 7/7 PASS
- `/app/backend/tests/test_p15ab_suppliers_unification_forensic.py` — 3/3 PASS
- `/app/backend/tests/test_iter251_v12_scheduler_diagnostics.py` — 3/3 PASS

## Iter-256 — Qoyod Existing-Data Migration (2026-06-26, READ-ONLY)
  - **User-locked policy** (Arabic spec, 2026-06-26):
    - Products: SKU exact → `auto_mapped`; SKU + name OR price differs → `mapped_with_warning`; name-only → `candidate_match` (NO auto mapping); else `unmapped`.
    - Customers: phone (E.164) → `auto_mapped`; email when no phone → `auto_mapped`; name-only → `candidate_match`; else `unmapped`.
    - STRICTLY read-only against Qoyod — no POST/PUT calls.
  - New module `integrations/qoyod/migration.py`: phone/sku/email/name normalisation, paginated GET importers (`import_qoyod_products`, `import_qoyod_customers`), Mezan-side extractors from `order_items` + `unified_orders` + `custom_app_customers`, `match_products`/`match_customers`/`run_migration` orchestrator with idempotent upserts.
  - New collections (additive, isolated from runtime resolver mappings):
    `qoyod_external_products`, `qoyod_external_customers`,
    `qoyod_migration_products`, `qoyod_migration_customers`,
    `qoyod_migration_runs`.
  - New routes (under `/api/integrations/qoyod/migration/*`):
    `POST /run`, `GET /status`, `GET /report`, `GET /{kind}`,
    `POST /{kind}/confirm`, `GET /{kind}/export.csv`.
  - Frontend: new page `/integrations/qoyod/migration` (`QoyodMigration.jsx`) — status cards, products/customers tabs, status filter, search, CSV export, "Confirm Match" for candidates; sidebar link `nav-qoyod-migration`.
  - Day-5 failing test fixed (`test_dry_run_client_records_calls_and_returns_fake_ids`) — updated to `{"customer": {...}}` envelope post `legacy.qoyod.com` migration.
  - **Tests**: `tests/test_qoyod_migration.py` — 27/27 PASS;
    `tests/test_qoyod_migration_http_iter256.py` — 9/9 PASS (+1 skip);
    full Qoyod suite: **184/184 GREEN** — zero regression.

## Pending (User-gated)
- ▶ Dry Run on real data — blocked until migration report is approved by user.
- ▶ Go Live (disable `dry_run_mode`) — blocked until Dry Run is approved.


## Iter-257 — Last Order Date column on Migration page (2026-06-26)
  - Added `last_order_date` to both `qoyod_migration_products` and `qoyod_migration_customers`.
  - Extraction logic:
    - **Products**: max(unified_orders.order_date over the SKU's order_numbers), fallback to received_at, then order_items.created_at.
    - **Customers**: max(unified_orders.order_date) keyed by phone/email, fallback to custom_app_customers.updated_at/created_at.
  - New API params on `GET /api/integrations/qoyod/migration/{kind}`: `sort` (occurrences|last_order_date|status), `sort_dir` (asc|desc), `last_order_after` (YYYY-MM-DD).
  - CSV export now includes `last_order_date` as a column.
  - UI: sortable column "آخر طلب" on both tabs, date filter "آخر طلب بعد".
  - **Policy guarantee** (locked by test): Last Order Date is metadata only — it does NOT change auto_mapped vs candidate_match decisions.
  - Tests: `test_qoyod_migration.py` — 31/31 PASS (+4 new); HTTP smoke `test_qoyod_migration_last_order_date_iter257.py` — 8/8 PASS; full Qoyod suite — 188/188 GREEN.


## Iter-258 — UI-driven Webhook Token (DB-stored, fingerprint-only) (2026-06-26)
  - **New module** `integrations/qoyod/webhook_token_store.py`:
    - `generate_token()` — `mzn_qoyod_prod_` + 48-byte urlsafe (79 chars)
    - `save_webhook_token()` / `get_webhook_token()` — Fernet-encrypted in `qoyod_webhook_tokens`
    - `get_webhook_token_meta()` — UI-safe (fingerprint, rotated_at, last_verified_at)
    - `verify_provided_token()` — **DB-first, env-fallback only when DB empty** (hmac.compare_digest)
    - `revoke_webhook_token()` — idempotent
  - **Refactored** `webhook.py` `_verify_token` into a `_make_verify_token(db)` factory so the dependency can access the DB. Legacy sync `_verify_token` kept for older test compat.
  - **New routes** (under `/api/integrations/qoyod/`):
    - `GET /webhook-token` — metadata (fingerprint only)
    - `POST /webhook-token/generate` — plaintext returned **EXACTLY ONCE**, then encrypted at rest
    - `DELETE /webhook-token` — revoke
  - **Frontend** (`QoyodSettings.jsx`): new `WebhookTokenSection` with one-time copy reveal, copy-to-clipboard, dismiss, regenerate (with confirm), and revoke. Plaintext disappears from DOM on dismiss; only fingerprint persists.
  - **Security guarantees** (locked by tests):
    - DB token takes EXCLUSIVE precedence — legacy env value cannot bypass after generation.
    - Plaintext NEVER returned by GET /webhook-token nor by /settings.
    - Revoked tokens fail verification.
  - **Tests**: `test_qoyod_webhook_token.py` — 19/19 PASS; HTTP smoke 5/5 PASS; full Qoyod suite — **222/222 GREEN**. Zero regression.
  - UX polish: first-time generate no longer triggers an unnecessary confirm; only regenerate (replacement) prompts.


## Iter-259 — Legacy Adapter for Make.com → Qoyod webhook (2026-06-26)
  - **Goal**: support Make.com's flat JSON without touching the legacy `/api/webhook/make` module, and without forcing Make.com to rebuild scenarios. Users will add a SECOND HTTP module in Make pointing at `/api/integrations/qoyod/webhook` with (almost) the same body shape.
  - **New module** `integrations/qoyod/legacy_adapter.py`:
    - `is_legacy_shape(raw)` — sniffs flat root + missing `data` envelope.
    - `adapt(raw) → (adapted, meta)` — pure transformer.
    - Items resolution priority: `items[]` → `packages[].items[]` → `"missing"`.
    - Each line item rebuilt to Salla's canonical `{sku, name, quantity, amounts: {price_without_tax, total}}` shape so the existing `normalizer.py` accepts it unchanged.
    - Flat amounts (subtotal/tax/shipping_cost/discount/total_amount) → nested `data.amounts.{sub_total, tax, shipping, discount, total}`.
    - Flat customer (customer_name/customer_mobile/customer_email) → nested `data.customer.{first_name, last_name, mobile, email}`.
    - Status node built from `order_status` + `order_status_slug` → `{name, slug, customized:{name}}`.
    - Unknown root fields preserved in `meta.legacy_extras` (utm_source, utm_campaign, device, shipping_company, received_from, etc.) for audit — never silently dropped.
  - **State machine** (`state_machine.py`):
    - Added `NEEDS_ENRICHMENT` (transient side-state).
    - Added `FAILED_ENRICHMENT` (failure stage) with `FAILURE_TO_RESUME["FAILED_ENRICHMENT"] = "RECEIVED"`.
    - Edges: `RECEIVED → NEEDS_ENRICHMENT`, `NEEDS_ENRICHMENT → {VALIDATED, FAILED_ENRICHMENT, DEAD_LETTER}`, `FAILED_ENRICHMENT → {RETRYING, DEAD_LETTER}`, `RETRYING → RECEIVED`.
  - **Webhook handler** (`webhook.py`):
    - `adapt_legacy()` called BEFORE idempotency derivation.
    - Inbox row now persists `adapted_payload`, `adapter_meta`, `enrichment_fallback_used` (bool).
    - New `_handle_missing_items()` branch:
      - Toggle OFF (default): `RECEIVED → FAILED_VALIDATION → DEAD_LETTER` with code `missing_items_no_enricher`. NO invoice created.
      - Toggle ON: `RECEIVED → NEEDS_ENRICHMENT → FAILED_ENRICHMENT → DEAD_LETTER` with code `enricher_not_implemented` (Salla-API enricher stub — actual implementation deferred).
  - **Settings** (`models.py` + `routes.py`):
    - `QoyodSettings.enrichment_fallback_enabled: bool = False` (opt-in, default OFF).
    - `SettingsPatch` (PUT route) accepts the new field. Round-trip tested.
  - **Bug found & fixed during testing** (iter-259 retest): `SettingsPatch` was missing the new field → `422 extra_forbidden`. Fixed by adding `enrichment_fallback_enabled: Optional[bool] = None` to the patch model.
  - **Tests** (NEW):
    - `tests/test_qoyod_legacy_adapter.py` — 40/40 PASS (detection, helpers, item adaptation, item collection priority, status node, public adapt(), downstream contract with `validate()`, state machine additions).
    - `tests/test_qoyod_legacy_adapter_http.py` — 5/5 PASS + 1 conditional skip (legacy-with-items progress, missing-items toggle OFF, missing-items toggle ON, legacy_extras audit, PUT settings round-trip).
  - **Full Qoyod regression**: **243/243 GREEN** + 1 skip (token gate). Zero regression.

## Pending (User-gated)
- ▶ User adds a SECOND HTTP module in Make.com → `/api/integrations/qoyod/webhook`.
- ▶ Smoke test on Preview with one real-shaped legacy payload that includes items[].
- ▶ If items[] consistently absent in real Make output, decide on enricher (Salla API or Make-side scenario edit).
- ▶ Then Dry Run → Go Live.


## Iter-260 — Integration Contract v1.0 + diagnostic capture + SKU/total guards (2026-06-26)

### Webhook parse-failure diagnostic capture
  - New `_capture_parse_failure(db, request, token, exc)` helper in `webhook_routes.py`.
  - Triggered at all 3 legacy webhook routes (`/make/{token}`, `/tiktok/{token}`, `/meta/{token}`) on body-parse failure.
  - Stores `{occurred_at, token_prefix (6 chars + …), content_type, content_length, body_preview ≤ 2 KB, parser_error, ip, route}`.
  - TTL index on `occurred_at` (30 days) created in `server.py` startup.
  - Behaviour UNCHANGED: still returns `400 {"detail":"Invalid JSON"}`.
  - Tests: `tests/test_webhook_parse_failure_capture.py` — 10/10 PASS.

### Contract v1.0 — invoice eligibility guards
  - New module `integrations/qoyod/eligibility.py` — `check_invoice_eligibility(payload)`.
  - Enforces 2 user-locked rules:
    - **`items_missing_sku`** — every dict item must have non-empty `sku`. Non-dict items are silently skipped (let `normalize()` raise `FAILED_NORMALIZATION` so Day-3 contract survives).
    - **`total_must_be_positive`** — order `data.amounts.total.amount` (or `data.total_amount` fallback) must be > 0. Also handles string totals (`"139.51"`) and rejects invalid types.
  - Wired into `_process_inbox_row` between the items-missing branch and `validate()`. Failure path: `RECEIVED → FAILED_VALIDATION → DEAD_LETTER` with the offending code. NEVER promotes to invoice creation.
  - Tests: `tests/test_qoyod_eligibility.py` — 16 unit + 4 HTTP integration = 20/20 PASS.

### Contract documentation
  - New artefact: `/app/docs/integrations/qoyod-webhook-contract-v1.md` (paste-ready for Make.com integrators).
    - Headers (X-Webhook-Token, X-Idempotency-Key), required/optional fields, full payload example (items + packages forms), responses (success, duplicate, business-rule fail, transport fail), failure-state map, Make HTTP module config, 7-step first-test checklist.
  - Status: v1.0 ACTIVE.

### Aggregate test status
  - **Full Qoyod regression**: **273/273 GREEN** + 1 skip. Zero regression.
  - New surfaces locked:
    * Adapter (40 unit + 5 HTTP)
    * Webhook parse-failure capture (10)
    * Eligibility (16 unit + 4 HTTP)

## Pending (User-gated, ordered)
- ▶ User adds the SECOND HTTP module in Make.com per contract §8 (using the generated `QOYOD_WEBHOOK_TOKEN` from Mezan UI).
- ▶ Run the 7-step first-test checklist on **Preview** before promoting to Production.
- ▶ Then Dry Run with real orders.
- ▶ Then Go Live (disable `dry_run_mode`).

