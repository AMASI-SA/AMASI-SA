# Final Production candidate V2: approved overlap integration

This record follows OVERLAP_RESOLUTION_AUTHORIZATION = GRANTED_ISOLATED.
It supersedes the earlier #1160 candidate and temporary Source A2 checkpoint
91c120424299a8642fef4f40105a0e1f5ecf9ccf on 096edc75. Neither is deployable as-is.

## Frozen inputs

- Production base / rollback target: 2e3b166d6713984ab8a63ae66fcecaef51e415c5.
- Base tree: 4feae7a7d0c543f930144653670451fa7751ad10.
- Accepted prior Source A: 5dd49ed8a41f33bb77d24c139d49db0cba8c5203.
- Prior Source A base: aa0eac73128dfcded4a1aaaa1217cb6c9d16810c.
- Frozen #1131: 6f777f22c06fc9a7a96f131b7a8b3fd0aaf67a9c.
- Frozen #1157 / G47: f2431282c3728a8d2fe618dbbe7a8616196f068d.
- Branch: codex/mz2-g47-final-candidate-v2-prod-20260927.

Import the accepted 214-path source delta excluding release-intent.
Its 211 nonoverlapping paths are byte-identical to accepted A.
Integrate only the three explicitly authorized overlapping paths from current
Production. All 2708 other existing Production paths remain byte/Git-mode
identical to base before B2; this record is the only extra path.
The historical input lineage remains in the earlier SOURCE-COMMITS.txt.

No new feature, ACCOUNTING_SETUP_CENTER_UI, G48/G49/G50, financial policy,
guard/adapter change, live DB write, pause change, financial activation,
Merge, Deploy, Publish or Preview switch is authorized or introduced.

## OVERLAP_RESOLUTION_EVIDENCE

### PROD_PRESERVED

Preparation retains MongoOrderRepository / Order Engine, current Salla status,
_current_assembly_order, _assembly_order_board, assembly/orders endpoint,
order_created_at/shipping_company/order_status/order_status_native, reopening
for current in_progress, and in_progress/ready_to_ship/completed stage rules.
The complete readiness body, including current-status checks, runs inside
the G47 owner transaction.

Supplier receiving retains request identity, scan/lost-response recovery,
closed-session retry, supplier identity/persisted invoice integrity, confirmed
totals, effective actor, Decimal/halala math and all close/finalize logic.
No Production runtime line is removed: preparation adds 50 lines and supplier
receiving adds eight.

All 28 Production test functions remain, including both newer live-Salla
tests. One source-inspection target follows the body into its transactional
helper without removing its assertions. The partial-assembly dict fixture
calls that body and asserts component consumption; explicit real-Mongo
verification exercises the public wrapper and actual transaction.

### G47_ADDED

Add the accepted atomic_owner wrapper, _consume_piece_components and
_assert_ready_piece_components. Physical/direct-assembly readiness consumes
the corresponding unit once; retry verifies consumed state. Operational
virtual annotations have no component demand. Component and readiness effects
share the same Mongo session/transaction.

### P01_WRITER_GUARD_ADDED

_post_supplier_invoice_ledger calls the unchanged assert_writer_allowed with
writer=legacy and the caller's mongo_session before amount calculation/GL.
Legacy mode stays allowed; transition_blocked/v2_active fail closed.
No fallback or transition-service change.

### OVERLAP_TEST_ADDED

Ordinary pytest executes 36 cases, including every prior case. New tests prove
live-status/component calls receive the same transaction-scoped DB, retry
does not repeat consumption, failure prevents ready mutation, operational
items do not access component stock, and real transition policy rejects
before GL access.

The permitted test file also has an explicit __main__ real-Mongo entry.
It requires an unauthenticated loopback replica-set URI and creates/drops
unique synthetic databases. No skip or application-database fallback.
Five scenario functions exercise actual Salla lookup, physical once/retry,
abort during consumption, abort after consumption at ready update,
direct versus operational virtual items, and actual supplier close/finalize
under v2_active. The supplier scenario observes cost=6 inside the real
transaction before the guard rejects, then proves cost=5/session/piece
rollback and zero invoice, GL, liability or audit remnants.

These five scenarios are not additional ordinary pytest cases. Fulfillment
CI runs the 36 ordinary cases; existing G47 CI runs real-Mongo lifecycle
coverage. Execute the explicit entry separately on final B2 and report it
as local real-Mongo evidence, not as a CI job.

From backend, with PYTHONPATH set to that backend directory and
MZ2_TEST_MONGO_URI pointing only to the disposable local test replica set:

    python -m pytest --noconftest -p no:cacheprovider tests/test_preparation_piece_operations.py -q --tb=short
    python tests/test_preparation_piece_operations.py
    python -m pytest --noconftest -p no:cacheprovider tests/test_g47_component_lifecycle_integration.py -q --tb=short

Build20 invoice/scan tests use their existing dedicated loopback test variables.

## Per-hunk classification against base

Unchanged context in every hunk is PROD_PRESERVED. Changed portions:

| File | Base/new hunk | Classification |
| --- | --- | --- |
| backend/preparation_piece_operations.py | @@ -24,6 +24,7 @@ from typing import Any, Callable, Iterable, Literal | G47_ADDED |
| backend/preparation_piece_operations.py | @@ -2490,6 +2491,7 @@ async def _mark_virtual_assembly_piece_ready( | G47_ADDED |
| backend/preparation_piece_operations.py | @@ -2581,6 +2583,7 @@ async def _mark_virtual_assembly_piece_ready( | G47_ADDED |
| backend/preparation_piece_operations.py | @@ -2653,7 +2656,52 @@ async def _mark_virtual_assembly_piece_ready( | G47_ADDED |
| backend/preparation_piece_operations.py | @@ -2719,6 +2767,7 @@ async def _mark_assembly_piece_ready( | G47_ADDED |
| backend/preparation_piece_operations.py | @@ -2745,6 +2794,7 @@ async def _mark_assembly_piece_ready( | G47_ADDED |
| backend/supplier_receiving_routes.py | @@ -1316,6 +1316,14 @@ async def _post_supplier_invoice_ledger( | P01_WRITER_GUARD_ADDED |
| backend/tests/test_preparation_piece_operations.py | @@ -390,7 +390,7 @@ def test_assembly_board_uses_live_salla_status_and_mezan_evidence(): | OVERLAP_TEST_ADDED |
| backend/tests/test_preparation_piece_operations.py | @@ -808,6 +808,10 @@ async def test_partial_order_can_mark_received_piece_ready(monkeypatch): | OVERLAP_TEST_ADDED |
| backend/tests/test_preparation_piece_operations.py | @@ -816,7 +820,7 @@ async def test_partial_order_can_mark_received_piece_ready(monkeypatch): | OVERLAP_TEST_ADDED |
| backend/tests/test_preparation_piece_operations.py | @@ -832,6 +836,9 @@ async def test_partial_order_can_mark_received_piece_ready(monkeypatch): | OVERLAP_TEST_ADDED |
| backend/tests/test_preparation_piece_operations.py | @@ -904,3 +911,313 @@ async def test_all_assembly_pieces_enable_shipment_after_full_preparation_receip | OVERLAP_TEST_ADDED |

## Protected bytes and intent

| Path | SHA-256 identical to base |
| --- | --- |
| backend/integrations/qoyod_manual/send.py | 8c8aaf2f6e5b3a47512d2a3ee812473254f9da94ce8ee1f0041aa0510e4d9c03 |
| backend/qoyod_auto_unified/queue_select.py | 5ffaea7a92a4f7d097740de41d6a4b058e9bd40f91018f6e3e8b03c5c55bb608 |
| backend/supplier_dispatch_waiting_policy.py | e946c9a93a8adbe06dc36d4cd3dd62729d71e32f722c1c6f8d25f6e20b483b9d |
| backend/tests/test_supplier_dispatch_waiting_current_status.py | 1b369f7bbee2071f4e7d95c66b712ac1359e8efd09699ac904eaccb78e1cbf00 |

A2 retains base intent bytes:
32ce3bb4120642fc6c562b0a9adb4f4b06fd35d6aebe1f5ff0647420924dd318.
B2 alone replaces release/release-intent-v5.json with the artifact generated
by the unchanged protocol-v5 governed build/validator for this A2/base.
No old-intent transplant, amendment or rebase after freezing the pair.

Require shared /app guard active=false before rehearsal; create no lease.
Final B2 requires executed PASS for MZ2 Accounting Module, G47 Focused
Integration, Fulfillment V2, Qoyod Payment Freshness, Security Gate,
CodeQL Python + JavaScript/TypeScript, frontend build, Release Readiness,
and Host Node 20 clean-clone adapter rehearsal. SKIPPED never means PASS.

Final exact-HEAD CI, HEAD/tree, runtime/intent identities, full diff and
final local Mongo evidence belong in Issue #1006 after freezing. Stop for
review before any deployment. Later code rollback requires the existing
release protocol and separate authorization; it is not a financial reversal.
