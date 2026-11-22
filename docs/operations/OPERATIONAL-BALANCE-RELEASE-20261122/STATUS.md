# Operational balance combined release — 2026-11-22

## Historical source checkpoint — superseded for publication

The owner has since published Android30 and requested manual server publication.
Production has advanced to PR2074 at 24270ebdfdebb83282389308a2349cdb27e2b8e4.
The old frozen PR2072 pair is preserved but must not be deployed. Current
integration and release handoff are recorded in
`docs/operations/OPERATIONAL-BALANCE-SERVER-R2-20261122/STATUS.md` and its successor
PR. Android-deferred instructions below describe the earlier checkpoint only.

## Scope and owner decisions

Continue the authorized web release of manual movement reversals, mobile account
running balances and optional external bank references. Android distribution is
deferred by the owner: mobile PR AMASI-SA/amasi-mobile#376 remains Draft; do not
merge it, build an APK or distribute it as part of this web release.

The original PRs are AMASI-SA/AMASI-SA#2064 and #2070. The latter explicitly permits
a governed combined successor. This task prepares that successor to require one
web publication. Do not repeat previously recorded financial corrections or use
real receipts, balances, reversals or provider mutations for acceptance testing.

## Reviewed source inputs

- Reviewed Production base J: `9b4fb716b430c32073b5ea7abaeaff62646d9633`.
- Reversal source from #2064: `b281dd676a47106857fd3a1775c86ab8e6e153b8`.
- Optional-reference/account-history source from #2070:
  `fae04c0242a43924fe1f681c378b0c855ac81e17`.
- Task branch: `codex/operational-balance-release-combined-20261122`.

The integration retains J and both changes, excluding #2064's frozen intent-only
commit. Before the additional CI/status changes, its full tree exactly matched
#2070 outside `release/release-intent-v5.json`; that file still matched J. No
intent-file history occurs between J and the combined source. Carrier #2062 is
preserved. Original source/intent pairs are not rewritten or reused.

Only the release coverage is additionally changed: collect the existing reversal
effects/routes, account-history and scoped latency tests, require their presence,
require the existing ApprovedLedger UI suite, and expect 17 driver-review cases
because #2070 parametrizes the web/mobile settlement path. No assertion is removed
and the existing rejection of missing/skipped/failed cases remains.

## Acceptance criteria

1. A permitted recorder reverses an eligible manual movement once, with a reason;
   the original and one linked inverse remain, including under retries/concurrency.
2. Resource and page authorization hold for both affected accounts and are checked
   again at commit; hidden/unreconciled balances never become misleading zeroes.
3. Running balances are calculated before pagination and remain consistent across
   pages and cached reads. Legacy clients retain their original API contract.
4. External bank references may be blank for the authorized mobile flows while
   request replay, supplied-reference validation and independent evidence persist.
5. Release A/B, exact-source tests, clean-clone adapter and official live verify3
   pass before declaring publication complete. No live financial writes for tests.

## Checkpoint state

Static integration/source comparison passed. Fresh focused frontend verification
passed 76 tests across five suites (ApprovedLedger, SimpleBalanceAccess,
SimpleBalanceStatement, simpleBalancesApi and simpleBalancesModel), exit 0, with
the governed Node 22.23.2/Yarn 1.22.22 and locked dependencies. The frozen #2064
source was previously green; this combined candidate still requires fresh backend
tests, exact-source full CI and a new governed v5 build/intent. Those are pending
at this source checkpoint. Record their fresh results and full canonical
source/intent SHAs in the successor PR rather than editing a frozen pair.

Public health observed the already published carrier release source
`23e9514b9e8a33bffb2363bb1d3afd792355ab2c`, runtime
`rg5-3ec8529aa0398fbe08b591ca47b175f1fd41af183c6fdae0fc21da4c223c618c`,
with verified identity, critical source hashes and frontend proof. This observation
does not establish completion of that owner's original verify3 or lease release.

The shared Emergent browser timed out before exposing the management page/terminal.
The last relevant historical ledger entry reported the carrier owner's verify
attempt running. Current shared guard state is unverified; do not infer inactivity
from elapsed time. No Production merge, shared workspace change, lease, publication
or real financial change has been performed by this task.

## Next safe actions

1. Complete focused tests and all required exact-source CI; independently inspect
   the combined diff and generate/validate a fresh A/B release contract.
2. Restore actual Emergent management access and read the original verification
   result before invoking status if that verifier still holds its operation lock.
   Require the actual guard to be inactive before shared changes or merging.
3. Re-read Production/task refs, ledger and AGENTS.md. If Production advanced,
   port only the scoped source changes and generate a new contract; do not publish
   a stale source or clear another owner's lease.
4. After the exact candidate is ready, preserve its A/B ancestry/tree through a
   normal merge, perform the unchanged configured adapter rehearsal, then own
   prepare/prepublish, one authorized publication and original verify3.
5. Confirm intended source/runtime/build-meta JSON/MIME and final `active:false`.
   Perform read-only UI acceptance and record a durable final handoff. Android
   remains deferred until the owner explicitly resumes its release.

Issue #1006 is at its comment limit. The successor PR description/comments are the
current task continuation record; do not overwrite unrelated ledger sections.
