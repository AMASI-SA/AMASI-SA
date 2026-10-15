# Final Production candidate V4 — assembly provenance

## Fixed reviewed inputs

- Production base and planned rollback: a7bcb1626e2ad4defba2260d4a113417f91f3120.
- Frozen #1131: 6f777f22c06fc9a7a96f131b7a8b3fd0aaf67a9c.
- Frozen #1157/G47: f2431282c3728a8d2fe618dbbe7a8616196f068d.
- Accepted original source delta: aa0eac73128dfcded4a1aaaa1217cb6c9d16810c -> 5dd49ed8a41f33bb77d24c139d49db0cba8c5203 (214 paths).
- Approved previous overlap source: a78e27cd40af53a81c01f9d94d044adb8e88edc6.
- Task branch: codex/mz2-g47-final-candidate-v4-prod-20260928.
- Shared /app guard at 2026-09-28T00:46:00.697541+00:00: exit 0, active=false; subsequent fetch confirmed the exact base.
- Owner declared TEMPORARY_PRODUCTION_FREEZE for this assembly.

This pair is Source A4 -> intent-only Candidate B4. Earlier A2/B2 and #1170
remain historical checkpoints. No A3/B3 was created. A4 retains the current
base's release-intent bytes; B4 must use a newly generated protocol-v5 artifact
for A4 and this base, never an earlier intent. Final SHAs, build identities,
CI and final-head test evidence belong in Issue #1006 after freezing.

## OVERLAP_RESOLUTION_EVIDENCE

Start from the latest Production blobs, then apply only the accepted delta.
The only source intersections are the four explicitly authorized paths.
The 210 other accepted paths retain the original Source A blobs exactly.

- preparation_piece_operations.py: +50/-0 accepted G47 lines. All current Salla,
  Order Engine and Build20 behavior remains in the transactional body.
  Physical/direct readiness consumes once; ready replay checks consistency;
  operational virtual entries consume nothing.
- supplier_receiving_routes.py: +8/-0 only. All current Build23 lines remain.
  _post_supplier_invoice_ledger calls the existing legacy writer transition
  guard with the caller's Mongo session, before calculation/GL work. No fallback.
- test_preparation_piece_operations.py: retain all 28 Production test functions,
  with the previously approved transactional test seams and overlap coverage.
- order_review_routes.py: retain #1169 get_orders, /order-reviews-v1/pages,
  numbered_pending_review_order_numbers and page/limit/total_count behavior.
  All functions outside complete_review remain AST-identical; atomic_owner
  is the only added top-level import. Snapshot/refetch guard and strict
  component reconciliation precede provider mutation. Final assertion,
  workflow and event share one owner transaction. Stale snapshots fail closed.

#1169 repository and pagination tests, all ten non-intent #1174 paths other
than the authorized supplier file, and all four #1175 source/test files are
byte-identical to the current base. Old #1158/#1164 source protections remain.

## Focused verification before freezing A4

- Real isolated Mongo G47 component lifecycle: 19 executed PASS.
- Preparation overlap unit suite: 36 PASS.
- Stage-one/pagination suite: 16 PASS after invoking the unchanged Production
  install_order_review_forward_stage_guard startup function.
- The initial standalone collection omitted that startup and exposed one
  pagination stage-exclusion assertion failure (70 passed/1 failed overall).
  Production Order Engine installs the function; the existing full Fulfillment
  suite also installs it from test_reviewed_preparation_batches. No source,
  assertion or expected stage list was changed to make the focused test pass.
  Final B4 must rerun the full CI and focused suite with actual startup behavior.
- Independent read-only source audit found no preservation blocker.

## Final-head acceptance still required

All mandatory gates must execute on B4: MZ2, G47, Fulfillment, Qoyod freshness,
Security, both CodeQL languages, frontend build, Release Readiness and actual
Host Node20 clean-clone rehearsal. No A2/B2/A4 result or mandatory SKIPPED is
a substitute. Also execute local real-Mongo overlap rollback and provider
failure/retry checks on B4. CI/source-build phases create no release lease.

No new features, accounting policy, guard weakening, G48/G49/G50, setup UI,
live DB writes, pause changes, financial activation, Merge/Deploy/Publish,
Preview switch or #1132 are included. Runtime probes/deployment require a
later separate owner decision.

## Protected bytes against Production base

| Path | SHA-256 |
| --- | --- |
| backend/integrations/qoyod_manual/send.py | 8c8aaf2f6e5b3a47512d2a3ee812473254f9da94ce8ee1f0041aa0510e4d9c03 |
| backend/mobile_employee_monitoring_workspace_routes.py | 59db9e888c89baf446e772d21022993da372d160e3ef3b272a0de3cc0926998e |
| backend/mobile_operations_monitoring_routes.py | 0730e0db54de81f693925b40180beb66cf483ee6fc8b04864563d89272cb7903 |
| backend/mobile_reviewed_preparation_routes.py | 3f713ca16353c15bb5466aea6fd9f276ba97e23b25d1e4837df7e08a73c4eef1 |
| backend/order_engine/mapper.py | 9322510e73ff339e1a61760d520c5e5d81091fc754fce459c41809fb7cbbd503 |
| backend/order_engine/repository.py | cc464f469b7699cd5d4ada887bf6cbb15b6c5da81645b8d4bc4dc778a562c5dc |
| backend/order_engine/shipping_label_service.py | 9b0cd63eed61e24f65923c31df57cd0d1b5e0d7d244366f615ac49f5c23e0a02 |
| backend/preparation_supplier_dispatch.py | e53f498704c7b56fef0fc1821b841e95f7bef08b5ed08775596b872896cb6b1d |
| backend/qoyod_auto_unified/queue_select.py | 5ffaea7a92a4f7d097740de41d6a4b058e9bd40f91018f6e3e8b03c5c55bb608 |
| backend/supplier_dispatch_waiting_policy.py | e946c9a93a8adbe06dc36d4cd3dd62729d71e32f722c1c6f8d25f6e20b483b9d |
| backend/tests/test_fulfillment_carrier_label.py | b74d802de2adcba2fafa901e387d31a8fc4173cb52b5099dff714a0e3e8a5879 |
| backend/tests/test_mobile_employee_monitoring_workspace_owner.py | f5a4093456c7de001f35d53b792be07acb0f8ef1558c0f074cdeeb93a2cc32d1 |
| backend/tests/test_mobile_operations_monitoring.py | 76597fb902f237a045d59407067b2255f772e450e7c22c9172ebefadd6f3b910 |
| backend/tests/test_mobile_reviewed_preparation_piece_visibility.py | 379ea7b9482b4f6332b736de2e5f65935f4f59e3f02324fe3cf571a8d0f5800e |
| backend/tests/test_order_engine_mapper.py | 027359490c0cf51d37873cf5a6bc61bbdac2ff208e96f6cdd50987bdbf9f74aa |
| backend/tests/test_order_review_stage_one.py | 093618630a7825bb9f997018a13a87d57eef29ad37ea3110209278218ab5a7e1 |
| backend/tests/test_supplier_dispatch_waiting_current_status.py | 1b369f7bbee2071f4e7d95c66b712ac1359e8efd09699ac904eaccb78e1cbf00 |
| backend/tests/test_supplier_scan_recovery.py | ecce5005425d56f968e4fb733326a09f12241568cf5d55bca2b946c165bd9c2e |
| frontend/src/lib/storeCourierLabelPrint.js | 7e67561fc84bb0ff7265f37159dea6f69157ba3243a1a49c0b78a04c494c0e1f |
| frontend/src/lib/storeCourierLabelPrint.test.js | fe9d72545e8b50e577e0b89f1fd8f11665084c329823a701cca5663315d84f8e |

## Approved diff hunk classification

Unchanged context is PROD_PRESERVED. Changed hunks follow:

- backend/order_review_routes.py @@ -14,6 +14,7 @@ from typing import Any, Callable, Optional — G47_ADDED
- backend/order_review_routes.py @@ -1085,6 +1086,12 @@ def make_order_review_router(db: Any, current_user: Callable) -> APIRouter: — G47_ADDED
- backend/order_review_routes.py @@ -1217,6 +1224,26 @@ def make_order_review_router(db: Any, current_user: Callable) -> APIRouter: — G47_ADDED
- backend/order_review_routes.py @@ -1238,42 +1265,6 @@ def make_order_review_router(db: Any, current_user: Callable) -> APIRouter: — G47_ADDED
- backend/order_review_routes.py @@ -1293,23 +1284,26 @@ def make_order_review_router(db: Any, current_user: Callable) -> APIRouter: — G47_ADDED
- backend/preparation_piece_operations.py @@ -24,6 +24,7 @@ from typing import Any, Callable, Iterable, Literal — G47_ADDED
- backend/preparation_piece_operations.py @@ -2490,6 +2491,7 @@ async def _mark_virtual_assembly_piece_ready( — G47_ADDED
- backend/preparation_piece_operations.py @@ -2581,6 +2583,7 @@ async def _mark_virtual_assembly_piece_ready( — G47_ADDED
- backend/preparation_piece_operations.py @@ -2653,7 +2656,52 @@ async def _mark_virtual_assembly_piece_ready( — G47_ADDED
- backend/preparation_piece_operations.py @@ -2719,6 +2767,7 @@ async def _mark_assembly_piece_ready( — G47_ADDED
- backend/preparation_piece_operations.py @@ -2745,6 +2794,7 @@ async def _mark_assembly_piece_ready( — G47_ADDED
- backend/supplier_receiving_routes.py @@ -1328,6 +1328,14 @@ async def _post_supplier_invoice_ledger( — P01_WRITER_GUARD_ADDED
- backend/tests/test_preparation_piece_operations.py @@ -390,7 +390,7 @@ def test_assembly_board_uses_live_salla_status_and_mezan_evidence(): — OVERLAP_TEST_ADDED
- backend/tests/test_preparation_piece_operations.py @@ -808,6 +808,10 @@ async def test_partial_order_can_mark_received_piece_ready(monkeypatch): — OVERLAP_TEST_ADDED
- backend/tests/test_preparation_piece_operations.py @@ -816,7 +820,7 @@ async def test_partial_order_can_mark_received_piece_ready(monkeypatch): — OVERLAP_TEST_ADDED
- backend/tests/test_preparation_piece_operations.py @@ -832,6 +836,9 @@ async def test_partial_order_can_mark_received_piece_ready(monkeypatch): — OVERLAP_TEST_ADDED
- backend/tests/test_preparation_piece_operations.py @@ -904,3 +911,313 @@ async def test_all_assembly_pieces_enable_shipment_after_full_preparation_receip — OVERLAP_TEST_ADDED
