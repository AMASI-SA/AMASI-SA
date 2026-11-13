# Ready server release — 2026-11-14

## Authority and sequence

The owner authorized deployment of the Ready server repair, verification on
the live service, and only then publication of the employee Android sorting
repair. Server and application publication are separate gates. No live order,
stock, supplier, accounting, or shipping mutation is needed merely to inspect
the deployment. Do not reapply Ready to a completed piece for testing.

## Source and continuity

- Repository: `AMASI-SA/AMASI-SA`.
- Original repair: PR #1904, source
  `9fee416d40e2b10031fb3d80c532d94e7593f5c9`.
- Fresh production base J:
  `53acc03a178362530f2cb45206fe11a4fb724d88`.
- Integration branch: `codex/ready-server-release-20261114`.
- The original repair's complete patch applied cleanly to J. Newer production
  changes, including the accepted Salla items projection repair, are retained.
- Issue #1006 has reached GitHub's 2500-comment limit. This task document and
  the release PR provide the continuation record. The exact new source SHA is
  recorded in the PR after this source checkpoint is committed.

## Intended behavior

Physical supplier pieces in a reviewed mixed order may become Ready only
after supplier and employee receipt, completed services, current accepted
component reservation, matching numeric source revision, eligible canonical
Salla status, and no hold. The same owner transaction records Ready, consumes
one unit, and advances the workflow to in_progress. Repeated Ready remains
idempotent. Shipment completion still requires all current source units and
all assembly work to be complete.

Direct and operational items retain their existing rules. Search remains
observational and does not create a legacy execution marker or consume stock.

## Pre-release finding corrected

Independent review reproduced a regression in the original historical-plan
search: the extra snapshot fence for received physical pieces was placed in
the shared component blocker, disabling otherwise eligible direct and
operational cards when the historical lifecycle revision was missing or the
canonical snapshot advanced. The virtual write path did not apply that new
fence, creating a search/write discrepancy.

The correction keeps current-plan failures shared but scopes the additional
reviewed physical snapshot failure only to the new physical exception. Two
real-Mongo HTTP regressions cover the missing and newer revision cases, with
positive mixed-row controls, physical write refusal, no material/event/shipment
change, and a shared cancelled-plan negative control.

Verification captured before this checkpoint:

- Original exact source had 18 unique successful CI workflows. These results
  do not certify the integrated source.
- New regression before correction: two tests failed on direct-card eligibility
  for the intended reason; no setup failures or skips.
- Same regression after correction: two tests passed, 35 deselected, exit 0.
- Full affected-file and final exact-source CI results must be recorded in the
  release PR before declaring release readiness.
- Shared `/app` Release Guard was read and reported `active:false` before any
  release rehearsal. This is an observation, not a lease reservation.

## Publication gates

1. Complete fresh affected tests and exact integrated-source CI.
2. Preserve source A; build twice using Node 22.23.2/Yarn 1.22.22 with the
   production backend origin; freeze a fresh external protocol-v5 intent.
3. Commit only `release/release-intent-v5.json` as B. Verify A-to-B intent-only
   relation and clean-clone adapter/package/runtime checks.
4. Merge without squash/rebase only after the required checks pass; confirm
   the resulting production merge M is tree-equivalent to B.
5. Prepare and rehearse the scoped rollback below before publication.
6. Re-read the shared guard and preserve all unrelated `/app` files. Align
   only to reviewed production, run the governed adapter, then prepare and
   prepublish. Publish once through the existing Emergent administration UI.
7. Require a new explicit Deployment Succeeded, all three guard verification
   probes, matching source/identity, exact JSON build metadata, and inactive
   final guard. Inspect the reported live order's eligibility and representative
   unaffected cases before advancing to the mobile publication gate.

## Scoped rollback preparation

Create a contingency branch from final merge M, reverse only this release's
J-to-A source changes, and retain M's intent during source construction. Restore
the two runtime modules and affected tests to J; preserve all other production
changes and all live data. Commit rollback source A_R, build it with M as its
source base, freeze a new intent-only B_R, and rehearse it through the same
protocol-v5 adapter and package/runtime checks. Record branch, exact SHAs,
test results, and identity in the release PR. Do not activate it by default.

Rollback restores the previous restriction for reviewed physical Ready. It
does not reverse recorded stock consumption, Ready events, order stages,
shipments, or provider effects. If recovery becomes necessary, pause the
affected operational path, reconcile in-flight outcomes, and deploy the
reviewed rollback through a fresh guard. Never erase records, reset business
states, reuse an old runtime identity, or replay a Ready operation to undo it.

## State at source checkpoint

Production changed by this task: **no**. No merge, frozen release intent,
lease, deployment, or Android publication has occurred at this checkpoint.
Live-order eligibility after deployment remains unverified.
