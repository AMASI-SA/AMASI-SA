# Salla ingestion / financial barrier hotfix — source review

Status: SALLA_INGESTION_FINANCIAL_BARRIER_FIX_READY_FOR_REVIEW.
Source hotfix only. No release rehearsal, reviewed intent, merge, deployment, replay, or financial activation is claimed.

## Frozen base and boundaries

- Production branch: `hotfix/prod-snap-meta-final`.
- Freshly fetched base/rollback: `3368b48ab4f30cc217dbae43bd96e720f49519dd`.
- Base tree: `3421a89a70e9544f327faddcc1756f518763b970`.
- That is the tree-equivalent merge of PR #1192/B11.
- Observed live health: source `ea3e87c07da6ace73c1e845441276d118ca72a15`, release `rg5-302495695d48d8eb9b97952b81aca76f2051c4d4abfab93491cfece378fc973e`, verified identity and critical hashes true.
- Dedicated branch: `codex/salla-ingestion-operational-hotfix-20260929`.
- Separate P01 work is preserved at `codex/p01-cutover-minimal-20260929`, checkpoint `31b5cfc34d21c6b18bf653a40593bcf9725288e7`; its UI/opening inventory/financial metadata changes are NOT included.
- No changes to write-control, financial transition, cutover, P02 activation, Qoyod, frontend, opening inventory, release intent, or startup migrations.

## RCA: source mechanism and live evidence

The deployed source calls `sync_order_from_verified_webhook` → `persist_component_source_snapshot` → financial `atomic_owner` before the canonical `upsert_order` callback. The financial guard rejects when `writes_paused` is anything other than explicit false. Thus the transaction rejects before `unified_orders` is saved.

Source: `backend/salla_integration/webhook_order_sync.py:449-456,549-556`; deployed `backend/fulfillment_v2_routes.py:944-947`; `backend/accounting_atomic.py:111-117`.
The HMAC-verified capture path records this failure and returns through an HTTP 200 route: `backend/salla_integration/webhook_event_capture.py:181-193,304-348` and `backend/salla_integration/routes.py:357-365`.

Live Production evidence was read only through the owner-opened, Production-selected internal Database Manager. No connection credential, external Mongo client, Preview data, Edit mode, or database mutation was used.

| Live observation, 2026-09-29 | Evidence and limit |
|---|---|
| Paused canonical persistence failures | Filter: `order_sync.reason equals order_webhook_persist_failed` AND `order_sync.error contains mz2_writes_paused`. 103 distinct capture documents inspected across three pages, 104 delivery attempts. All have `verified_before_capture=true`. |
| Failure interval observed | Earliest retained matching capture: **04:46:43.880 UTC / 07:46:43.880 Riyadh**. Latest in this census: **17:42:35.062 UTC / 20:42:35.062 Riyadh**. The incident remained unresolved during this read; this is not its end time. |
| Event breakdown | 48 `order.status.updated`, 39 `order.updated`, 14 `order.created`, 2 `order.cancelled`. |
| New-order exposure | The 14 created captures contain 14 distinct references. Earliest created capture: 04:46:46.642 UTC. These are failed canonical ingestion attempts, not a proven count of currently absent orders. |
| Tenant consistency | All 103 captures carry one merchant identity; SHA-256 fingerprint `1f36d31d91039a3aabfe21aa9f2fef661c563e33069247257424c60c6a2737e6`. Raw merchant identity and order references are omitted. |
| Most recent success observed | A capture marked `full_order_from_webhook` / successful order sync at **02:59:46.037 UTC / 05:59:46.037 Riyadh**. This is the latest in the inspected success page, not a globally proven final success. |
| Exact onset / missing-order count | **UNVERIFIED_DUE_TO_MANAGED_DB_TOOLING.** Numeric-reference filters also returned empty for a known existing control order. Earlier provisional assertions that all 14 are absent are withdrawn. Do not use these empty-query results as absence evidence. |
| Production ACK 200 | Failed verified capture documents prove the handler continued through capture. Current platform logs expose only the last 100 lines and did not contain the affected requests. Actual historical HTTP status readback remains unverified. The source contract and isolated real HTTP reproduction both prove the ACK200-with-failed-persistence mechanism. |

Capture rows are deduplicated by merchant/event/hash; their latest sync result may be overwritten by a retry. Therefore this census is of retained matching failures, not an immutable audit of every historical failed delivery. Pagination results were accepted only after verifying 103 distinct document IDs and no load error; an earlier stale/duplicated page was discarded. Counts may grow while Production remains unfixed.

## Implementation

1. New `operational_atomic.py`: owner-serialized snapshot/majority Mongo transaction restricted to audited fulfillment collections and fields. It shares only the existing serialization revision, preserving ordering with financial transactions.
2. Canonical order + source watermark commit in that operational transaction. G47 routing/reconciliation runs afterward. A later G47 error leaves the canonical order present and records blocked/retry state.
3. The financial writer rejects entry from an active operational transaction; a caught denial poisons and aborts the whole operational transaction. No GL/journal/payable/cost/control capability is granted.
4. Missing financial controls remain missing and financially fail closed. Only serialization identity/revision may be initialized by an operational transaction; no pause/transition/cutover values are initialized or changed.
5. The already owner-approved temporary legacy operational cohort uses canonical creation evidence and audits `legacy_operational_pre_g47`. No component reservation/plan/consumption is created for that cohort.
6. After explicit G47 start, new/equal-date orders require full successful G47. Invalid/missing creation evidence fails closed; timezone aliases are handled explicitly; stale DTO dates cannot override canonical evidence. Existing plans never silently become legacy.
7. Review, physical preparation, packing/handoff, and component lifecycle callbacks use the same restricted operational boundary, preserving owner isolation and transactions. Manufacturing-to-stock still requires its existing strict G47/provenance path.
8. The exception permitting legacy operation does not alter financial balances, component costs, valuation, or opening inventory. Configured G47 physical reservations/consumption retain their existing checks; this patch does not configure or activate G47.

### Changed source files

- `backend/accounting_atomic.py` — reject financial entry from a restricted operational transaction.
- `backend/operational_atomic.py` — new restricted transaction capability.
- `backend/fulfillment_v2_routes.py` — persistence/lifecycle boundary and approved cohort checks.
- `backend/order_review_routes.py` — final operational acceptance transaction.
- `backend/preparation_piece_operations.py` — physical completion boundary/cohort enforcement.
- `backend/stock_component_consumption_service.py` — restricted physical stock transactions.
- `backend/stock_preparation_order_routes.py` — audited preparation/receipt transaction boundary.

### Changed tests

- `backend/tests/test_g47_operational_boundary.py` — new pause/capability/cohort integration suite.
- `backend/tests/test_salla_paused_webhook_http.py` — new real HMAC/HTTP/capture/persistence reproduction.
- `backend/tests/test_g47_component_lifecycle_integration.py` — canonical evidence in fixtures; approved legacy policy expectations.
- `backend/tests/test_preparation_piece_operations.py` — transaction seam follows the operational entry point.

## Acceptance evidence

All database integration tests use disposable loopback Mongo replica-set/standalone fixtures. No live credentials or production payloads are used.

| Requirement | Evidence |
|---|---|
| A: paused order.created persists | Real HMAC → HTTP route → dispatch → capture → tenant resolution → canonical upsert test; old financial binding reproduces failed persistence and ACK200, fixed binding persists. |
| B: no GL/receivable/payable | Zero legacy and V2 ledger/group/audit/sequence rows, recognition registry, supplier liabilities, purchase operations and cost state/events; control values unchanged except serialization revision. |
| C: unconfigured/failed G47 does not erase order | Legacy operational marker, no component plan/consumption; forced post-commit G47 exception retains canonical order plus durable blocked/retry marker. |
| D: replay is idempotent | Same HTTP event replay retains one canonical order and one capture, delivery count increments; G47 recovery retry does not duplicate reservation. |
| E: paused order.updated | Canonical status/raw snapshot/source watermark update safely with stale/version fences retained. |
| F: finance remains 423 | Direct financial writer callback remains unentered and raises `mz2_writes_paused`; nested/captured-DB financial entry cannot escalate operational capability. |
| Authentication/tenant | Invalid HMAC401 before capture; cross-owner mutations/profile escalation rejected; exact owner-bound queries retained. |
| Legacy/G47 split | Historical passes operationally; new/equal cutoff needs stock; missing/invalid creation blocks; settings changes invalidate legacy ticket; no DTO fallback. |

Broad isolated integration run: **221 passed + 98 subtests passed, 1 failed, 0 skipped**, exit 1. The single failure is `test_numbered_review_route_precedes_order_detail_and_counts_full_tenant_queue` in `test_order_review_stage_one.py`: expected exclusion stages differ from the existing Production implementation. It was reproduced unchanged on a detached exact Production worktree (same assertion, exit 1). It is NOT silently treated as PASS and is not fixed in this patch.

Coordinator independently reran the new HTTP tests: **2 passed, 0 failures/skips**, exit 0, 11.18s. Final strengthened financial-collection assertion run is recorded in STATUS.json and TEST_RESULTS.json.

The pre-cutover financial fence, bank/COD gates, shipping/P02 gate, immutable posted shipping-payment policy and V2 ledger regression tests were included in the broad run and passed. Protected-source diff confirms no changes to financial write-control, accounting ledger/cutover, Salla transport/dispatcher/capture, frontend or release intent.

An independent source review found no remaining blocking issue. Two earlier issues (timezone aliases and stale DTO historical fallback) were fixed and tested before this isolated source snapshot. A failure while saving the G47 failure marker itself can still produce a failed-sync response after canonical commit; it does not roll the order back.

## Separate replay/backfill plan — NOT EXECUTED

1. After separately reviewed/deployed hotfix, capture a fresh read-only manifest from verified Production captures, scoped to the exact merchant and incident window. Retain event hash, reference, source version, event type and delivery timestamps; keep payload/PII out of public reports.
2. Reconcile this manifest against canonical orders using a proven typed lookup in a supported trusted runtime or a validated viewer control. Resolve the exact missing/stale order count first. Current viewer empty results do not satisfy that prerequisite.
3. Obtain the missing historical HTTP/log evidence if available, and distinguish failed creates from failed updates/cancellations. Do not blindly replay the 103 records.
4. Use a dedicated, separately approved operational-only replay path restricted to canonical persistence/watermarks. Do not run the full webhook dispatcher: it has other enrichment, refund, shadow/outbox and fulfillment consumers.
5. Preserve merchant+order identity, provider version, cancellation precedence and existing stable event identity. An old/unversioned payload cannot overwrite newer canonical state. Retry must return the same canonical outcome.
6. Keep financial pause, pre-cutover original-creation fence and P01/P02 gates intact. No recognition, GL, payable, receivable, component consumption or financial replay is authorized by this recovery.
7. Require dry-run counts, isolated idempotency/zero-financial-side-effect proof, and explicit owner authorization for the exact manifest before any live write. Start with a bounded batch only after approval.

## Rollback / stop boundary

Source-only rollback is to discard this isolated branch from consideration; Production remains unchanged. If a future separately authorized deployment requires rollback, target `3368b48ab4f30cc217dbae43bd96e720f49519dd`. Restoring that code also restores the ingestion coupling while finance is paused; it does not authorize unpausing finance. Do not delete successfully captured/canonical orders to roll back code.

Production reads: read-only UI census and public health/log evidence. Production writes: **0**.
Preview data was not used as RCA evidence; no Preview document queries/writes. Only initial viewer metadata was visible before selecting Production.
No merge, deploy, publish, restart, release lease, replay/backfill, write-control change, G47/P01/P02 activation or financial activation.
