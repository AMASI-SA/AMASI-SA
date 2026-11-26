# Operational refresh recovery — 2026-11-26

## Scope and authorization

The merchant authorized fixing the recurring accounting refresh failure,
merging, publishing directly to Production and verifying the actual recovery.
The controlling constraint is to reduce unnecessary work and preserve the
existing advertising, driver, shipping and collection records. No synthetic
financial transaction, invented bank receipt, new polling job or source-proof
shortcut is authorized by this implementation.

Task branch: `codex/operational-refresh-root-fix-20261126`.
PR: https://github.com/AMASI-SA/AMASI-SA/pull/2132.
Verified initial implementation checkpoint:
`ccaa58e425c62ad2c3089ae7ca430b0d251a52cf`, tree
`66e4ed09f57a31a31c516e7adee7a4367f1762eb`.

Initial production deployment:
`37fd7851bb76b39b74e434da19183262e4d42f26` (PR #2131), reviewed source
`164466b6d1caacb956e23b9fe1a8b2cf3c65a9aa`.

## Observed mechanism

A saved automatic attempt at 01:51 Asia/Riyadh on 26 November succeeded in
committing an aggregate, but its product/supplier profit stages were cancelled
at their shared deadline. Earlier attempts had failed during primary order
reads. A successful aggregate receipt therefore does not establish complete
profit-source coverage.

The saved trace reported 8.829 seconds for native evidence and 76.016 seconds
for the aggregate, leaving very little margin against the 85-second outer
budget. Snapshot loading included 10.953 seconds preparing a full copy;
the pure reconciliation engine subsequently makes a second full copy.

The profit branch has a separate 25-second shared budget. Its context took
13.811 seconds: normalized orders 2.482 seconds, native evidence 6.198 seconds,
and context preparation 5.040 seconds. Product supplier evidence was cancelled
after 4.259 seconds of the residual budget. This does not establish that the
supplier query itself needs more than 25 seconds.

Primary order first-row latency was 8.521 milliseconds and complete cursor
reading took 9.519 seconds. This is not evidence of a missing index or an
initial query stall. The production projection and financial cohort remain
unchanged in the first patch.

## Work in progress

1. An opt-in replacement-state operation lets automatic refresh use the pure
   reducer's independent successor without the redundant preceding store copy.
   Ordinary manual writers retain their current isolated mutation and response
   semantics. The original before-state, CAS retries, archive-prefix validation,
   bank-claim identities and final authorization remain mandatory.
2. The independent context investigation found no demonstrated end-to-end gain
   from replacing its per-order copy. A 6,000-order fixture blend ran the actual
   selection, history, option and currency helpers: median full preparation was
   604.581 ms with the current copy and 642.253 ms with the existing alternative.
   A single 19,135-order sample also showed no complete-stage improvement. These
   are synthetic local observations, not production timing estimates. Keep this
   path unchanged and measure the actual post-release trace after reducing
   full-aggregate allocation. Supplier validity must still be re-read across
   batches; source projections, concurrency and deadlines stay unchanged.
3. Run focused regressions and exact final-source CI; freeze a fresh source A /
   intent-only B pair under the unchanged Release Guard v5 protocol.
4. Publish only after clean-clone and original-host rehearsals pass, and verify
   the intended production identity with all three original guard probes.
5. Verify a new live refresh after deployment, source completeness, original
   dates, preservation and duplicate prevention. Pre-patch success is not
   post-patch evidence.

## Verification and continuity

The initial focused regression reproduced two whole-aggregate copies before
the patch. Its parity and input-immutability assertions passed; the explicit
single-copy assertion failed as intended (1 failed, 1 passed).

The replacement-state patch then passed 24 focused cases (20 new cases and
four worker-responsiveness cases) in 2.99 seconds, with no skips. The existing
affected offline suites passed 383 cases in 17.98 seconds; ten existing actual
Mongo archive-prefetch cases were skipped because no owned Mongo fixture was
configured. Those remain real-Mongo CI gates, not local integration evidence.
An independent reviewer passed 159 targeted cases in 4.86 seconds, covering
replacement, bank claims, original bank dates, archived CAS reload, advertising
approval, recovery proof and staged-payment replay. Source review and
`git diff --check` found no remaining blocker in this slice.

Root independently reran all 20 new cases: 20 passed in 2.36 seconds. The new
module was then renamed to
`backend/tests/test_operational_balance_refresh_state_replacement.py` so the
existing CI `test_operational_balance*.py` selection executes it. This is a
coverage correction, with no changed test logic. A fresh exact source commit
and candidate intent are required after this rename; the earlier checkpoint's
candidate must not be reused.

The profit snapshot is presentation data assigned after canonical financial
reconciliation. A product-source timeout remains explicitly incomplete/unknown
and cannot be treated as zero. It does not itself prove a missing financial
obligation or collection. Post-release checks must inspect both the committed
financial delta and individual profit-source outcomes, without claiming that
an aggregate success proves complete economic coverage.

GitHub Issue #1006 has reached the comment cap. Use this task status and its
dedicated PR for the durable handoff; do not attempt further Issue #1006 comments.

Production changed by this task: **no**. No release lease has been opened.
The original Emergent project was reopened through its management UI. Its
original guard reported `active: false`, HEAD matched the initial production
deployment above, and its tracked worktree was clean. Its 432 untracked files
remain untouched. Guard status and shared source must be checked again before
any later host source change or release rehearsal. Paid agent chat is not used.

Next safe action: verify the CI-selected test path, publish the final source A,
require exact-source CI and its new candidate intent, then create intent-only B
and complete both reviewed-B clean-clone rehearsals before the original-host
release protocol. Retain live evidence privately; do not include customer or
employee records in this repository. This document is not a release-success
claim.
