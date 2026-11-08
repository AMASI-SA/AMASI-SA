# Courier label refresh after Android76

Owner reports the latest deployment works well except intermittent store-courier
label printing. Scope is only that printing failure; retain Android76/PR1838 and
all unrelated production changes.

## Verified starting point

- Production deployment commit: `10b25d2ad75fb3f1d82fe43ecb64b34f9a410a01`.
- Task branch: `codex/courier-label-post76-20261109`.
- New source checkpoint is the commit containing this document; exact SHA is
  recorded in PR/Issue1006 after creation (no self-referential SHA).
- Latest observed shared guard: `active:false`, matching production HEAD.
- No live business mutations used for testing. No new lease or publication.

## Root cause and narrow change

The explicit completion-and-label action captures shipping before its completed
POST. A same-courier order webhook advances `carrier_updated_at`, causing the
internal document fence to emit `shipping_snapshot_changed`; the outer full
snapshot comparison can reject the same update after document creation.

Only the explicit store-courier action permits observation clocks/provenance to
change. Its outer material fence still compares carrier identity, shipment,
address, items, custody, holds, workflow assignment and pending refresh flags.
Ordinary printing and external-carrier fences retain their current behavior.
One initial order GET and one explicit completed POST remain; no polling,
additional status GET or automatic status POST retry. No Android change.

Changed code: `assembly_manual_label_refresh.py`,
`order_engine/amasi_shipping_compat.py`,
`order_engine/shipping_label_service.py`.
Regression: `tests/test_manual_courier_label_refresh_race.py`, included in the
existing Review Completion Resumable CI workflow.

## Verification

- Exact pre-fix regression: 4 failed / 11 passed. Both in-progress and already
  completed orders reproduce the exact stale-label failure, during status POST
  and immediately after document creation.
- Post-fix combined focused local run: 115 passed, 23 skipped (real Mongo absent),
  64 subtests passed. Command:
  `PYTHONPATH=backend:backend/tests:<isolated-test-deps> python -m pytest -q --asyncio-mode=auto backend/tests/test_manual_courier_label_refresh_race.py backend/tests/test_mobile_assembly_refresh_actions.py backend/tests/test_amasi_shipping_compat.py backend/tests/test_unified_completed_carrier_label.py backend/tests/test_shipping_saved_label_print.py backend/tests/test_assembly_print_completion.py --disable-warnings`.
- Includes 40 sequential explicit refreshes with no retained lease, material
  mutation rejection, and unchanged external observation fence.
- Independent read-only review found no concrete blocker; independent focused
  run passed 78 tests / 59 subtests before final local run.
- Live exact-order diagnostics not obtained: shared terminal dependency/runtime
  unavailable followed by editor disconnection. Screenshots and deterministic
  code reproduction prove the defect, not the private live order contents.

## Remaining / next safe action

Create Draft PR and run exact-source CI (including real Mongo), release readiness
and governed source A / intent-only B. Inspect fresh shared guard and HEAD before
any `/app` mutation/rehearsal. Preserve other tasks' leases. Normal merge only,
then owned V5 prepare/prepublish, authorized targeted publication and verify3.
Production changed by this follow-up: **NO**. No need for a new APK.
