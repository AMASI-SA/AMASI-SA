# Build44 backend source A preparation

Task: BUILD44-QUEUE-RELEASE-20261017. Publication is owner-authorized, but this
checkpoint is a source-only candidate, not a deployment or an intent-only B.
Repository: AMASI-SA/AMASI-SA.
Candidate branch: `codex/build44-backend-source-a-20261017`.

## Source and continuity

- Fresh Production baseline: `196adb36d64f5bb595a52a4371efd38034941173`.
- Reviewed implementation checkpoint: `456ab00684bc504101c6d0404b46ee271aed5c9e`
  on `codex/build44-queue-backend`, preserved with PR #1409.
- New candidate applies the exact 17-file tree diff from that baseline to the
  reviewed checkpoint. Before the two metadata changes below, Git confirmed
  complete staged-tree equality to the reviewed checkpoint.
- The only differences from the reviewed implementation are this continuity
  document and an additional exact-branch trigger in the existing Build44 CI.
  Backend implementation and tests are byte-identical Git blobs.
- The baseline's `release/release-intent-v5.json` is unchanged. Earlier intent
  commits are not ancestors of this new source-only branch after the baseline.
  Existing source/intent history was not reset, rebased, or rewritten.

## Behavior included

Pending-review search spans all pages; employee monitoring and My Products use
the same live operational queues; courier manual assignment retains idempotency.
An explicit reviewer-authorized Salla refresh recovers options and addresses
without approving, auto-fulfilling, or writing financial eligibility. It preserves
review notes and image choices and invalidates old consent transactionally.

Hide/unhide and direct-routing changes advance the workflow revision. An explicit
closure of an unresolved local review receipt uses exact binding, a durable event,
session/owner serialization and a revision barrier before reporting non-commit.
Missing operations alone remain unknown. A new approval requires refreshed detail
and new consent; delayed old consent cannot commit after closure.

## Verification evidence

Exact reviewed checkpoint CI [38994556332](https://github.com/AMASI-SA/AMASI-SA/actions/runs/38994556332)
passed: 96 replica-set review/refresh/readback/export tests plus 27 subtests,
33 queue/courier contracts, and 1 employee-monitoring parity test. The retained
review XML contains all 14 closure tests, zero failures, errors or skips.
New candidate CI must independently pass before an intent is frozen.

Release readiness [38994559980](https://github.com/AMASI-SA/AMASI-SA/actions/runs/38994559980)
rejected the old branch because its full post-baseline history contains intent
edits (`source A must change governed source without changing intent`). This
clean source-only branch corrects that history boundary without weakening guards.

Dashboard adoption [38994559916](https://github.com/AMASI-SA/AMASI-SA/actions/runs/38994559916)
fails at `test_dashboard_mixed_permissions_and_tenants[current_month_mixed]`,
request 0: `find: 43` versus frozen-reference `find: 41`. JSON, documents read,
and per-collection document counts match. The exact same failure already exists
on Production ancestor `c03c9df22d07a9a99984b02ab134763b0ae600a8` in
[38982527420](https://github.com/AMASI-SA/AMASI-SA/actions/runs/38982527420).
This pre-existing dashboard command-count failure is disclosed, not waived or
changed by this task; the early failure prevents later dashboard cases from being
evidence for this candidate.

## Release state and next action

Production changed by this task: **no**. No production merge, lease, prepare,
prepublish, deployment, financial/shipment test write, or Android change occurred.
No current intent B is claimed valid for this candidate. The old PR remains a
recoverable implementation checkpoint and will be linked to the replacement PR.

Next: inspect exact candidate CI and independent review, perform the governed
Linux source build, freeze a new intent-only B, and verify exact-B clean-clone
readiness. The parent coordinates the replacement PR, Issue #1006 identity,
concurrency checks and any separately authorized protocol-v5 publication.
