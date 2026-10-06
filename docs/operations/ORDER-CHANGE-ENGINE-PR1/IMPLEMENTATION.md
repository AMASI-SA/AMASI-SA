# ORDER_CHANGE_ENGINE_PR1_READY

PR1 only: lifecycle policy, capabilities, shared operational hold/resume, immutable Order Change Event, idempotency, audit, execution fences. Add/edit/cancel/replace **commercial writes are not implemented or enabled**. Web UI/notification delivery and Android UI parity remain PR7/PR8; this PR establishes their shared Backend contract and protects their existing execution entry points.

## State and capability matrix

| Authoritative workflow stage | Order/item/piece operational hold | Resume v2 hold | Add | Edit options | Commercial cancel | Replace |
|---|---|---|---|---|---|---|
| reviewed | Yes, permission + flag + fences | Yes, permission + fresh fences | No | No | No | No |
| in_progress | Yes | Yes | No | No | No | No |
| preparation | Yes | Yes | No | No | No | No |
| assembly (including addressing work) | Yes | Yes | No | No | No | No |
| ready_to_ship | Operational safety hold only | Yes | No | No | No | No |
| completed | No new hold | Existing overlay only | No | No | No | No |
| courier_dispatch / delivering / delivered | No new hold | Existing overlay only | No | No | No | No |
| pending_review / cancelled / unknown | No new hold | Existing overlay only | No | No | No | No |

Preparation and assembly can be piece stages under an in_progress workflow; this PR does not rewrite the existing workflow state machine. Addressing shares assembly execution. Every Yes is additionally denied while an execution claim is active/uncertain. Employee self-stop is restricted to their assigned piece and existing self-stop permission. Resume removes only the overlay, never restores old piece state or reopens a completed order. Review Completion/pending_review is not changed.

## Exact contract

- `GET /api/order-change-controls-v1/orders/{number}/capabilities`: current stage, order revision/generation, action eligibility, active holds, current piece fences (manager or own assigned pieces), execution-in-flight. Commercial capabilities always false.
- `POST /api/order-change-controls-v1/orders/{number}/holds`: scope order/item/piece, target, operational stop type, reason, idempotency key, expected revision/generation.
- `POST /api/order-change-controls-v1/holds/{id}/resume`: same required fences/key/reason.
- `GET /api/order-change-controls-v1/orders/{number}/audit`: tenant-scoped manager audit, newest 100 events.
- Existing fulfillment-experiment hold/release and blocking tracking-instruction adapters delegate to the service when governed. Existing tracking managers obtain fences through `GET /api/order-tracking-notes/orders/{number}/control-capabilities`; this does not grant general stop permission.
- Governed receive/assembly write APIs require piece fences. Consumers must use the current Backend fences; there is no independent Android eligibility policy. Existing clients remain unchanged with the rollout switch off and no activation marker. Client UI rollout is deliberately later.
- The event includes order/change/key/type, actor/reason/UTC timestamp, old/new units/options, previous stage, affected employees, revision/generation, required action, financial impact `pending_contract`, Salla mutation false. Resume snapshots current units/options/assignees while preserving hold-creation history. Operational projections sharing a commercial unit are deduplicated in the commercial identity array, while detailed before-state piece history remains distinct.

## Atomicity and hard guards

One existing Mongo owner transaction commits hold/resume, workflow revision, append-only audit, idempotency result and activation marker. The generation token binds component lifecycle and canonical source watermark plus experiment generation. Same-key/same-actor/payload retries return the committed result before stale-fence checks; changed payload gets a conflict. Audit failure rolls back all control writes.

Order holds are durable order-level overlays. Future pieces remain blocked even if they did not exist when the hold was created. Item/piece holds preserve unrelated pieces. No piece, component, allocation, inventory consumption or audit is deleted/reverted. Virtual operational/direct-assembly units preserve source cancellation and generation metadata.

Durable per-order execution claims exclude hold/resume for the entire existing worker/service callback, including external IO. Nested callbacks reuse the claim and recheck scope. Cancelled/replaced/obsolete/non-current pieces and stale fences fail before execution. New execution also rejects canonical cancellation, pending component reconciliation, authoritative-refresh requirements, and blocked/cancelled/reconciliation-required lifecycle state. Backend preparation start/materialization/receive, supplier dispatch/receipt, assembly/addressing, ready/pack, AWB/label, handoff and driver delivery paths are guarded. Existing provider contracts are unchanged.

Canonical webhook capture remains available during a hold, retains the overlay, and advances the source generation fence. Auto-routing cannot bypass the hold. This PR does not serialize incoming canonical source capture behind an external provider request: in-flight provider outcomes and source changes retain the existing component/generation checks. It does not claim distributed atomicity with Salla. Unknown worker/provider failures (including HTTP500/503) retain an `uncertain` claim; no TTL silently releases it. Known HTTP4xx rejects end that attempt.

Notifications are not a safety dependency. Existing read/acknowledgement markers do not permit completing cancelled/replaced work. New notification delivery/read APIs are deferred to PR7/PR8.

## Verification

All testing uses synthetic data. Local MongoDB8.0.12 is a real isolated loopback replica set `lifecyclepr1`, port27441; each suite owns a UUID database and drops only that database. No application .env or production database fallback; pytest runs `--noconftest -p no:cacheprovider`.

- Integrated acceptance + affected regressions: **305 passed, 77 subtests passed**, zero skips/errors/failures; `evidence/pr1-final.xml`.
- Focused suites after the last capability addition: **61 passed, 77 subtests**, zero failures/skips; see `evidence/pr1-focused-final.xml`.
- Coverage: hold order/item/piece/resume; mixed stages; employee permissions/tenant isolation; two independent Mongo clients racing a revision; concurrent hold/resume; Web adapter/shared mobile contract retries; employee vs worker exclusion; webhook source update while held; timeout-after-commit replay; duplicate key conflicts; stale order/piece generation and revision; real audit validator failure with rollback on hold and resume; cancelled/replaced/obsolete work; actual receive, assembly and shipping functions blocked before physical/provider effects; future pieces; sticky flag-off enforcement; virtual pieces; tracking-only permissions; unknown failure exclusion.
- Existing component lifecycle integration, supplier preparation, carrier/driver handoff, and tracking suites cover the default-off compatibility path. No emulator/browser UI test is claimed: client UI changes are outside PR1.
- Added `.github/workflows/order-change-engine-pr1.yml`: exact PR-head checkout, isolated Mongo8.0.12, same focused/regression suites, minimum case count and zero-skip gate, retained JUnit artifact. CI status is reported separately from local evidence.
- Fresh independent read-only review completed; findings in permissions, virtual identity, current audit snapshots and uncertain failures were corrected and tested.

## Rollback and activation restrictions

`ORDER_FULFILLMENT_LIFECYCLE_CONTROLS_ENABLED` is absent/default false; only exact `true` enables new controls. No configuration enables it in this PR. Keep it off until later PRs implement client fence consumption and the rollout is separately approved.

For never-activated owners, off preserves the legacy execution path. For activated owners, a persisted marker intentionally retains enforcement after the switch is off: new holds stop, idempotent replay and audited fenced resume remain available, existing holds/uncertain claims cannot be bypassed. Experiment reset is denied for governed owners.

Do not delete owner markers, holds, audit or execution claims. Do not blindly roll back code that enforces existing holds. Stop new controls, drain/reconcile existing controls with the current guarded version, verify no active/uncertain claims or unresolved holds, then review any code rollback. Unknown external outcomes need operator investigation; no automatic repair/unlock or new recovery mutation is introduced. Legacy pre-v2 holds require their existing contract and are not silently migrated; activation must account for outstanding legacy holds. No destructive migration.

## Boundaries

No Salla mutation added; no invoice rewrite, journal/refund/accounting writer; no Review Completion edits. No work on Operational Balance, #1264/#1266/#1267/#1270 or P1253. Existing Salla/accounting calls remain governed by their original contracts and are only prevented from starting when held. PR2-PR9 remain unimplemented. No merge, deployment, release lease, or production write was performed.

## Files

- `.github/workflows/order-change-engine-pr1.yml`
- `backend/carrier_handoff.py`
- `backend/fulfillment_carrier_label.py`
- `backend/fulfillment_experiment_routes.py`
- `backend/fulfillment_lifecycle.py`
- `backend/fulfillment_lifecycle_execution.py`
- `backend/fulfillment_lifecycle_routes.py`
- `backend/fulfillment_v2_routes.py`
- `backend/mobile_reviewed_preparation_routes.py`
- `backend/operational_atomic.py`
- `backend/order_change_event.py`
- `backend/order_engine/__init__.py`
- `backend/order_engine/shipping_label_service.py`
- `backend/order_tracking_notes.py`
- `backend/order_tracking_notes_routes.py`
- `backend/preparation_file_registry.py`
- `backend/preparation_piece_operations.py`
- `backend/preparation_supplier_dispatch.py`
- `backend/reviewed_preparation_batches.py`
- `backend/store_courier_dispatch_routes.py`
- `backend/store_delivery_driver_app_routes.py`
- `backend/store_delivery_handover_routes.py`
- `backend/supplier_receiving_routes.py`
- `backend/tests/test_carrier_handoff.py`
- `backend/tests/test_fulfillment_carrier_label.py`
- `backend/tests/test_fulfillment_lifecycle_execution.py`
- `backend/tests/test_fulfillment_lifecycle_mongo.py`
- `backend/tests/test_mobile_reviewed_preparation_piece_visibility.py`
- `backend/tests/test_order_change_controls_routes.py`
- `backend/tests/test_order_change_event.py`
- `backend/tests/test_preparation_piece_operations.py`
- `backend/tests/test_preparation_piece_stage_semantics.py`
- `backend/tests/test_reviewed_preparation_batches.py`
- `docs/operations/ORDER-CHANGE-ENGINE-PR1/STATUS.json`

## GitHub verification and financial freeze proof

PR: https://github.com/AMASI-SA/AMASI-SA/pull/1272. Dedicated exact-head CI succeeded at implementation HEAD `5f03e9cac8b723f7ec87e43af3906835845295b7`, TREE `ef230af6b05f71552b1efbb54864b126bf62b83b`: **305 passed +77 subtests**, zero skips, artifact11424513413. The connector-created implementation commit has the same tree as local tested commit `ce76da9aee7310f50d4975f127c9e314815f8852`; git HTTPS was unavailable. Final documentation/test-proof updates preserve implementation files.

The supplier-display financial freeze initially rejected the new close_session source hash because the operational execution guard surrounds that function. The financial statements are preserved; the renewed freeze records the guard and independently verifies the original protected body. All other approved financial function/file hashes remain unchanged. No accounting behavior was repaired or extended.

Security Gate remains red on unchanged dependencies (frontend postcss-selector-parser/source-map-js; backend multidict/fsspec/pymongo). Frontend regression before its audit passed246 suites/1491 tests. These dependency upgrades belong to the separate security work and were not made. PR1 readiness is not merge/deployment authorization or a claim that the global security gate is green.

The financial-boundary test retains original close_session SHA256 `3c67e5d94ef560b4791c54cbe805d288cc18637993d1c6b7850b3dfed3ddda40` and additionally pins guarded SHA256 `3b176089ef34493764d96698e2359afa69d5bd2a04aac72cf01c79165e03b3a7`. Removing only the approved preflight/scope/revalidation lines restores the exact original source hash. This is a test-proof update, not a financial implementation change.
