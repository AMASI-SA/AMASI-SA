# Financial center loading recovery — continuation

## Scope and authorization

Continue the owner's existing instruction to repair, merge and publish the
financial-center loading defect. The owner asked to resume from the prior
screenshots and inspect Mezan on 2026-11-14. No payment, settlement, delivery
confirmation, provider synchronization or other business-data write is a test.

## Recovered checkpoint

- Repository: AMASI-SA/AMASI-SA.
- Prior verified Production / source branch base:
  `23cc06da003c18c0f3fc6c2e749aad1326666166` (PR #1918).
- Prior handoff branch: `codex/financial-loading-handoff-20261114`, verified
  remote SHA `078e7fc8c3a146f0d94817fc2347bfcf33b24a17`.
- Prior source branch: `codex/financial-loading-recovery-20261114`; remote still
  points to the base above. The previous four frontend edits and backend
  regression were recovered without altering the original worktree.
- This continuation: `codex/financial-loading-resume-20261114`, isolated checkout.
- Issue #1006 has reached its comment limit. The task document and forthcoming
  PR remain the continuation record. Its last existing entries predate this task.

The prior verified live attempt remains historical evidence: a later refresh
returned HTTP 408 after 85,140.921 ms; reconciliation was cancelled after
34,098.679 ms. The saved-report GET then took 43,589.479 ms. This continuation
directly observed the financial report with snapshot time 14 November 2026,
12:51 Asia/Riyadh, and the explicit timeout/stale-report warning. That view does
not establish a newly successful calculation.

## Acceptance requirements

1. Remove measured repeated reconciliation work without changing financial
   rules, result values/order, nested provider rollback, whole-order rollback,
   exceptions, Decimal behavior, authorization or cancellation semantics.
2. One visible page opening or manual refresh is one bounded load attempt.
   Hiding/offline must end that attempt without automatically recalculating on
   return; an abort-insensitive pending request must retain ownership of its lane.
3. Pending or failed loads must show an explicit recoverable state and preserve
   saved report amounts and timestamps honestly.
4. Verify actual React tests, backend regression/parity and scaling, applicable
   integrated CI, then a fresh v5 source A / intent B / normal merge and governed
   publication. Finish with official verification and visible live acceptance.

## Current evidence and next action

Recovered standalone frontend regression passes; baseline starts 31 requests
across repeated interruptions and fails to terminate an unresponsive request.
The recovered backend regression fails on the baseline: 24 replayed orders copy
4,032 dictionary entries and 48 copy 16,128. This is synthetic operation-count
evidence, not a measured share of live delay.

Actual frontend verification passed under governed Node 22.23.2 / Yarn 1.22.22:
77 focused tests across three suites, and 733 operational workflow tests across
51 suites, both exit 0 with no failed, skipped or pending cases. Dependencies
were copied from an existing tree with identical package/lock bytes after one
offline cache miss; the pinned Yarn integrity check passed. No dependency
manifest changed. These checks mock networking and do not establish live speed.

Backend optimization and independent complete-state parity review are in
progress. Root owns integration, remote mutations and
publication. Implementation owners have disjoint frontend/backend paths.
Production changed by this follow-up: **no**. No lease or publication started.

Next safe action: complete and independently verify the candidate; preserve a
recoverable remote checkpoint before freezing a release. Do not deploy this
incomplete checkpoint or reuse PR #1918's frozen release intent.
