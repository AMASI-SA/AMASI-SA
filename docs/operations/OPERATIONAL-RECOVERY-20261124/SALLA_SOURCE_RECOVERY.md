# Salla source window and completeness repair

## Scope and source

This source-only change is based on production commit
`0ffa962d3cfda3eb49b5750c7bdc2f932a645d4d`. It is prepared on
`codex/salla-source-window-repair-20261124` for the main recovery coordinator to
integrate, checkpoint, test and release. This worktree made no production writes
or live provider calls. All new fixtures are synthetic.

The daily refresh was discarding known orders when the light listing omitted an
aware order-level update timestamp. The manual recent-orders action sent an
unsupported modified-date filter, and its terminal status could say completed
even when the bounded pagination was truncated. The UI did not render partial
status or explain incomplete coverage.

## Changes

- The 30-day action submits explicit inclusive creation-date bounds in
  `Asia/Riyadh`. Legacy `updated_since_hours` inputs up to 744 hours are normalized
  into the same supported calendar-window contract. No `updated_at_gt` filter is
  sent. Coverage is explicitly labelled `creation_window_only`.
- A separate manual recovery control accepts a creation window of one to 31
  days. The earlier July historical-import action remains available unchanged.
  The new mode cannot combine with attribution recovery or a scheduled window.
- Missing order clocks can be completed through native Order Details, with at
  most 500 detail targets per run and a 250 ms delay before each attempt.
  Provider ID and reference must match the admitted listing row exactly. An
  aware original order timestamp is mandatory; no customer, creation or local
  observation clock substitutes for it. Details with a future timestamp are
  rejected in manual recovery. The native creation day must also remain inside
  the selected manual window.
- The scheduled path remains strictly existing-only. Manual recovery retains
  the established import/upsert contract for new source orders. A known existing
  order uses the transactional existing-only fence so a concurrent deletion
  cannot recreate it. Every accepted detail payload fetches complete native
  items before reaching the existing owner transaction and component fence.
- Resource cancellation is checked before provider work and before persistence.
  Existing task cancellation and lease timeout propagation remain in force.
  Source recovery does not advance carrier handoff or invoke delivery accounting.
- Truncation, processing errors, incomplete evidence and deferred component
  acceptance produce a partial result. The log retains actual created, updated,
  unchanged and skipped counts alongside missing-clock, budget, creation-window
  and pending-component diagnostics. Historical completed-but-truncated log rows
  also render as partial in the UI.

## Acceptance and idempotence

The component acceptance policy is unchanged. An already accepted, unchanged
source reread remains a no-op. A newly imported order may still require normal
component reconciliation. Such a run is explicitly partial, with
`pending_component_count`, even though the source row was successfully saved.
Re-reading that pending order may advance existing observation/retry metadata;
it must not clear the component fence or duplicate an order, component, stock
movement, financial movement or journal entry.

Actual isolated Mongo tests exercised both accepted-order and new-order repeats.
They verified stable native dates, source identity and items, one stored order,
unchanged component records, and unchanged stock plus 11 financial/physical/event
collections. The pending source retained its acceptance fence. No production
financial evidence is represented by these synthetic acceptance tests.

## Verification

| Check | Result |
| --- | --- |
| Initial focused regression set against the previous implementation | 29 failed, 1 passed; failures included the absent new recovery contract |
| Final focused backend window, pagination, scheduler, manual lease and gateway suites | 147 passed, exit 0 |
| Exact 21-suite Salla acceptance command from the updated workflow | 472 passed and 43 subtests passed, 0 skipped/failures/errors, exit 0; 41.45 seconds |
| Salla source-window UI and existing background sync tests | 14 passed, exit 0; 3.226 seconds |
| Whitespace/patch validation | `git diff --check`, exit 0 |

The full acceptance run used an owned disposable loopback Mongo replica set,
`--nounixsocket`, a random port and isolated per-test databases. It was stopped
after the run. The command used `--noconftest`, `PYTHON_DOTENV_DISABLED=1` and an
explicit local `MZ2_TEST_MONGO_URI`; no production database fallback was used.
The existing workflow now includes `test_salla_source_window_repair.py`.

Focused backend invocation from the repository root:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHON_DOTENV_DISABLED=1 PYTHONPATH=backend:backend/tests \
  python -B -m pytest --noconftest -p no:cacheprovider -q --tb=short \
  backend/tests/test_salla_source_window_repair.py \
  backend/tests/test_salla_orders_sync_pagination_evidence.py \
  backend/tests/test_salla_scheduled_reconciliation.py \
  backend/tests/test_salla_manual_sync_serialization.py \
  backend/tests/test_salla_orders_v3_gateway.py

CI=true python scripts/frontend_release_toolchain.py exec -- \
  yarn --cwd frontend test --watchAll=false --runInBand --runTestsByPath \
  src/pages/SallaSourceWindow.test.jsx src/sallaOrderSyncBackground.test.js
```

Two tests in the separately explored legacy marketing-attribution suite fail
identically on the prior frozen source: `test_salla_live_source_details_shape_resolves_exact_snapchat_campaign`
expects an older projection shape, and `test_bounded_sync_recovery_loads_full_order_attribution`
uses a fake writer that does not exercise the current component transaction.
They were left unchanged and are not included in the passing acceptance claim.

## Remaining verification and operational use

The main coordinator owns combined-source review, GitHub checks, the new v5
source/intent pair, release rehearsal, merge, guarded deployment and live
verification. The new UI and manual source recovery have not yet been observed
on production from this worktree.

After deployment, choose a small date interval and inspect real created/updated,
unchanged, skipped, incomplete, pagination and component counts. A detail-budget
or pagination limit requires a narrower interval; rerunning the same large
interval does not establish the missing coverage. Creation-window completion
does not prove discovery of every older order modified during that interval.
Financial recovery and reconciliation must use the resulting native evidence
and retain their own original-date and duplicate checks.
