# COMPONENT_SOURCE_EVENT_STALE — root cause and displayed approval candidate

Review only. No merge, prepare, prepublish, deploy, restart, OTA, Production database test, real Salla test or Production business write was performed.

## Evidence classification

**PROVEN — implementation defect:** `fulfillment_v2_routes.persist_component_source_snapshot` increments `g47_salla_snapshot.revision` for every accepted persistence, even if approval facts are unchanged. Completion previously compared a source revision read before `operational_owner` against the source read inside the transaction. A same-facts webhook in that window therefore caused HTTP 409 `component_source_event_stale`. Isolated MongoDB 8.0.12 reproduced both revision-only and real synthetic webhook false rejections before the fix; both pass afterward. This is a TOCTOU false conflict, not proof of corrupt product facts.

**PROVEN — approval contract gap:** previous clients submitted only workflow revision. Fetching a new detail silently at tap did not establish what the employee had actually seen. The user authorized extending the existing GET/POST contract and both clients. The existing canonical snapshot/delivery-scope fixes (`54900616a6c52bc1cb0eba0ef4a60969b31076ae`, `10573da6daa1193b36daa5656d27630b2fe19d36`) are ancestors of the fresh Production GitHub base but did not remove the early revision mismatch.

**LIKELY — incident mechanism:** a concurrent accepted source persistence is consistent with the reported error. Webhook or refresh is plausible, but neither is identified as the actual writer.

**UNKNOWN — specific incident:** before/after source documents, business hashes, request IDs, HTTP timings and writer logs were not available. The owner supplied 01:08 Riyadh; the incident date is not independently established. We did not replay the real order, contact Salla or change Production to obtain evidence. Do not label this exact incident a proven same-facts race.

## Writer and read path inventory

Paths/lines refer to the candidate unless labelled base. The final diff provides changed-line evidence.

| Path | Role |
|---|---|
| `backend/fulfillment_v2_routes.py:986` | Owner-scoped persistence entry point. |
| `backend/fulfillment_v2_routes.py:1012` | Undated cancellation/authoritative-refresh branch replaces watermark and increments revision. |
| `backend/fulfillment_v2_routes.py:1027` | Every accepted persistence increments revision, without business-equality comparison. |
| `backend/salla_integration/webhook_order_sync.py:428` | Webhook persistence delegates to that writer; order created/updated/status/cancel events. |
| `backend/order_engine/salla_refresh.py:542` | Authoritative refresh delegates to that writer. |
| `backend/order_review_routes.py:107` | Exact search can request refresh, freshness 30 seconds. |
| `backend/order_review_routes.py:807` | Detail GET can refresh before rendering; local-only bypasses external refresh. |
| `backend/order_engine/routes.py:394` | Explicit order refresh entry. |
| `backend/reviewed_preparation_batches.py:818` | Forced refresh caller. |
| `backend/orders_db.py:732` | Copies existing canonical document into merge; whole-document `$set` at 1027 can carry the retained watermark. Its shipping CAS does not specifically protect snapshot revision. This is a possible stale overwrite path, not a proven incident writer or an intentional increment. |
| `backend/salla_integration/auto_sync.py:156`, `backend/salla_integration/order_commerce_enrichment.py:154`, `backend/salla_integration/sync.py:976` and `:1736` | Indirect upsert paths using that generic merge. |
| `backend/fulfillment_v2_routes.py:1237` | Changes component_pending only; does not increment revision. |

POST path: route authorization/instruction checks → external-to-transaction source/order/catalog/workflow/acceptance reads → frozen execution items → `complete_local_review_operation` → `operational_owner` callback `finish_local` → `claim` → displayed signature/route-basis/transaction-basis comparisons → existing source/config fences and guards → local execution/workflow/event commit → HTTP response. Rejection aborts the transaction without new approval/workflow/inventory effects. Existing completed evidence is returned for lost-response retries.

## Minimal contract extension

`backend/order_review_approval.py` owns stateless HMAC-SHA256 approval evidence. The existing JWT secret is domain-separated; no new credential, collection, endpoint or synchronization mechanism is introduced. The token lasts 30 minutes and binds merchant, order, workflow revision and fingerprint. It is signed server-side, verified in the owner transaction, length-bounded and never accepted as client-authored facts. Authentication/authorization remains required. There is no claim that a cryptographic token proves human attention; it proves the exact server-issued data the client presents and submits.

The fingerprint covers the existing schema-2 canonical business facts and unknown-field fingerprints, component acceptance/configuration, workflow and catalog-resolved product identities. Product IDs, quantities, options, variants and recipes therefore participate. Gallery URLs are excluded; documented transport timestamps and snapshot revision are not approval facts. Unknown non-transport fields remain conservative. Existing config-version changes still invalidate approval, even when conservative rather than product-specific.

GET detail renders source/order/catalog/workflow/acceptance from a single read-only Mongo snapshot transaction. Existing explicit gallery refresh remains outside it. POST sends the previously displayed token, compares route-prepared execution inputs against its digest (including A/B/A races), then compares transactional inputs against the same digest. Metadata-only revision changes may proceed; material changes or invalid/expired evidence return 409 `component_source_event_stale` with refresh_required. Missing evidence returns 409 `review_approval_required`. No blind retry or automatic adoption of new facts occurs.

### Compatibility

- Web `completeOrderReview` uses the actual rendered detail; no hidden fresh GET at tap. Waiting-list summaries open the full review view before approval.
- Android companion Draft AMASI-SA/amasi-mobile#268 sends the displayed token, guards changed detail/order during confirmation and ignores late results for another page. No scanner or Android permission changes.
- Old clients against the new backend cannot create new approvals: explicit 409. New clients against the old backend stop locally because the token is missing. This is an intentional fail-closed contract break requiring coordinated client availability before any separately authorized rollout.
- Existing completed-operation reads/retries remain idempotent. Existing pending operations must satisfy the original stored approval as well as the displayed contract; refresh does not silently reapprove changed original facts.

## Isolated evidence

MongoDB 8.0.12 replica set, loopback-only port 27963, disposable synthetic per-test databases. External provider calls fail assertions. No Production credentials/database or real-order payload is used.

| Check | Result |
|---|---|
| Baseline race suite | 4 PASS / 2 expected FAIL / 0 SKIP; the two failures are false revision-only/same-facts webhook conflicts. |
| Fixed source race suite | 6 PASS / 0 FAIL / 0 SKIP. Includes material source changes, duplicate approval and existing leased operation. |
| Displayed approval suite | 11 PASS / 0 FAIL / 0 SKIP. Includes changed options/quantity/product/recipe, missing/forged/expired/cross-context tokens, source-between-read-and-claim, lost-response retry, consistent GET snapshot, enriched-identity ABA, guarded recipe contention. |
| Web focused Jest suites | 5 suites / 35 PASS / 0 FAIL; Node24.19 Windows, not the pinned Linux production build toolchain. |
| Android | TypeScript noEmit + displayed approval repository contract PASS. Physical-device interaction/network E2E not performed. |
| Initial broader backend run | 55 PASS / 26 FAIL: assembly fixtures lacked the newly required token. Updated fixtures use GET then the displayed token; full rerun is tracked in STATUS. |

### Timing (current candidate only)

Two warmups, 20 independently initialized synthetic first approvals, in-process ASGI with real isolated Mongo. Setup/assertions excluded. Windows Python3.13; another test suite could contend. All 20 GET/POST pairs returned 200 with exactly one operation/workflow/event and no provider calls.

| Measurement | Median ms | p95 ms |
|---|---:|---:|
| GET detail | 95.116 | 154.677 |
| First POST approval | 338.275 | 425.392 |
| Fingerprint (60 calls) | 1.368 | 2.807 |
| Sign token | 0.064 | 0.076 |
| Verify token | 0.116 | 0.172 |

No paired baseline was measured; incremental API latency and Production performance remain unproven. GET adds snapshot/source/acceptance reads. POST adds transactional order/catalog/acceptance/workflow reads and two digest computations. No Salla request or background polling was added. The full metrics JSON is retained with the task evidence.

## Remaining boundaries / risk

1. Catalog-only writers outside existing acceptance fencing may commit after the transaction snapshot starts. The transaction uses approved snapshot facts; this does not guarantee that every catalog collection remains unchanged until commit. Source-document and guarded recipe/config contention are exercised independently.
2. A concurrent first creation of the previously absent config-fence collection produced Mongo112/HTTP500 in an exploratory test. Existing-row conflict/retry passes. This first-use DDL edge is not fixed or claimed to be a new regression here; it warrants separate diagnosis before claiming exhaustive concurrency coverage.
3. Read-only snapshot GET requires replica-set transaction support and a configured existing JWT secret. Standalone Mongo is not a supported approval environment; missing signing configuration fails closed.
4. A stale token can increase refresh requests; TTL is 30 minutes, with explicit review required afterward. No logging of tokens or customer contents was added.

Rollback is a separately approved coordinated client/backend revert to the recorded bases. No schema/data migration is necessary. Backend-only rollback would leave the new clients blocked for approvals until client compatibility is restored; do not claim a server-only rollback is operationally transparent.

Final exact identities, CI outcomes and decision are recorded in the final handoff/STATUS. This candidate is for review, not authorization to merge or deploy.
