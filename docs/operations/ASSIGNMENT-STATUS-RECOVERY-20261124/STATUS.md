# Saved preparation file status check — 2026-11-24

## Scope

Add an explicit action in the saved preparation-file registry, next to the
existing PDF download. The owner requires the current file creation, printing,
assignment and automatic flows to remain unchanged. A user can print the saved
file first, then check only that file's orders and move eligible, fully assigned
orders to Salla in progress. Incomplete or unproven orders remain visible with
a safe explanation. A failed status check cannot block PDF download.

Repository: `AMASI-SA/AMASI-SA`.
Task branch: `codex/assignment-status-recovery-20261124`.
Reviewed source base: `c8f3a3c84f026e9202d01b1adc3cdfa9d8e092dc`.

## WIP checkpoint: isolated UI and endpoint orchestration

- Added an independent registry action with explicit click handling, a
  synchronous duplicate-click lock, cumulative counts and safe Arabic reasons.
- The client reads a bounded saved-file plan and sends one awaited request per
  order. Opening the page, refreshing the registry and file-created events do
  not initiate the new action. The bound matches the existing 1,500-unit batch
  contract; a 51-order file is covered by a regression.
- Added tenant-scoped plan and continuation endpoints under the existing batch
  router. The POST rejects read-only workspace access and rechecks the current
  account before continuation. Each order has a durable status result, including
  previously silent unresolved results. Batch and registry status metadata are
  written separately; cross-document atomic persistence is not claimed.
- Existing PDF controls, allocation receipts, printing code, ordinary assignment
  reconciliation and mobile assignment contracts are unchanged.
- The provider continuation seam is a separate module under implementation.
  This WIP checkpoint is not ready to merge or deploy until that seam and its
  current-source, full-coverage, authorization and permanent-dispatch tests are
  integrated and reviewed.

## Executed verification

Backend focused command, from `backend` with `PYTHONPATH=.:tests`,
`PYTHONDONTWRITEBYTECODE=1`, `PYTHON_DOTENV_DISABLED=1`:

```bash
python -m pytest -q --tb=short \
  tests/test_preparation_status_recovery.py \
  tests/test_preparation_file_registry.py \
  tests/test_mobile_reviewed_preparation_routes.py \
  tests/test_mobile_reviewed_preparation_piece_visibility.py \
  tests/test_preparation_piece_stage_semantics.py \
  tests/test_reviewed_preparation_batches.py
```

Result: **65 passed, exit 0**. These include actual existing PDF-generation
contracts and tests that the new orchestration leaves saved file identity and
committed allocations unchanged. New orchestration tests replace only the
provider-continuation seam; they do not establish provider or transaction safety.

Frontend, using governed Node 22.23.2 / Yarn 1.22.22:

```bash
CI=true python scripts/frontend_release_toolchain.py exec -- \
  yarn --cwd frontend test --watchAll=false --runInBand --runTestsByPath \
  src/pages/FulfillmentV2.test.jsx \
  src/components/MezanV2NavigationShell.fulfillment.test.jsx \
  src/preparationFileRegistryUi.test.js \
  src/components/fulfillment/PreparationFilesRegistry.test.jsx \
  src/services/orderReviewEngine.statusSync.test.js
```

Result: **5 suites, 65 tests passed, exit 0**. Pending and failed status actions
leave the PDF link available. The actual shared session interceptor may repeat
an HTTP request after session refresh; the server's permanent dispatch claim,
not a client click lock, must prevent duplicate provider writes.

`git diff --check`: passed at the verified UI/orchestration milestone.

## Remaining work and exact next safe action

1. Finish `backend/preparation_status_continuation.py` and
   `backend/tests/test_assignment_status_continuation.py` without changing
   existing automatic assignment or printing behavior.
2. Run focused core and neighbor regressions and independent review. The
   existing real-Mongo CI gate is extended to include the new modules.
3. Create a source-only Draft PR and run real MongoDB 8.0.12 replica-set checks.
   Local MongoDB could not start in this execution environment; no real-Mongo
   transaction or race test is reported as passed locally.
4. Record the exact remote checkpoint SHA and subsequent CI state in the
   canonical continuation ledger, Issue #1006.

## Release state

**Production changed: no.** No live status POST, reapproval, allocation change,
backfill, merge, release intent, release lease or deployment was performed by
this task. Shared release work and unrelated inventory changes are preserved.
This branch is a review candidate only; production publication is not included.
