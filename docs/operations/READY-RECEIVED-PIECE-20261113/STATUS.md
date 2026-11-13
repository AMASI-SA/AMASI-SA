# Ready server release — current production integration, 2026-11-14

## Authority and publication order

The owner authorized publishing the Ready server repair, verifying the live
service, then publishing the separate Android Ready-ordering repair. No live
Ready, stock, shipping, accounting, supplier or advertising mutation is
authorized merely for acceptance inspection. Do not repeat Ready on a completed
piece for testing.

## Current source and preserved work

- Original repair: AMASI-SA/AMASI-SA PR #1904, source
  `9fee416d40e2b10031fb3d80c532d94e7593f5c9`.
- Current reviewed production base J:
  `5d158f6006b63ac7663c293696d501f595ce8754`.
- Current integration branch: `codex/ready-server-current-20261114`.
- J is PR #1911's campaign-card release: source
  `9fc331b8cd6b3c77129090c9661af5fa9d0ca186`, intent B
  `4e31be12c3f6053d07a946f0cfd7f2639e1d75d9`. Its campaign backend/frontend,
  tests, workflow, matching dnspython 2.9.2 pins and tracked intent are retained.
- The new source copies only the four reviewed Ready runtime/test blobs from
  `8518af15b3f49c179f37782805eb6eb09a552f11` and updates this checkpoint.
  The current production release changed none of those four files.
- Issue #1006 has reached its comment limit. This task document and its current
  release PR are the continuation record; exact source/intent/merge SHAs and
  live evidence must be recorded in that PR.

## Superseded release pair

PR #1910 source A `8518af15b3f49c179f37782805eb6eb09a552f11` and intent B
`3f8fb6cdf0d8eb2c4f39d4fbc9a8ae882438fbd5` passed all 19 applicable workflows.
B also passed both Host Node 20.20.2/24.19.0 clean-clone adapter rehearsals and
an isolated rehearsal in the Emergent environment. They were not merged or
published by this task.

Before merging, a fresh production ref read found concurrent PR #1911 had
advanced production from `53acc03a178362530f2cb45206fe11a4fb724d88` to J.
The PR base.sha snapshot was stale. The old frozen pair is preserved on
`codex/ready-server-intent-20261114`; it is superseded and must not be deployed,
merged, rebased, or reused as the new recovery identity. The current source
starts directly from J and retains J's intent unchanged.

## Ready behavior and independent correction

Reviewed physical supplier pieces may become Ready only after supplier and
employee receipt, completed services, current accepted component reservation,
matching numeric source revision, eligible canonical Salla status, and no hold.
The owner transaction records Ready, consumes one unit and advances the workflow
to in_progress atomically; retries are idempotent. Shipment completion requires
all current source units and assembly work to be complete.

Independent review fixed an additional regression in the original historical
search: its new physical-only snapshot fence was blocking otherwise eligible
direct and operational cards. The correction scopes this extra fence to physical
rows, while cancelled/stale current-plan failures remain shared. Search remains
observational and creates no legacy marker or stock effect.

## Verification evidence and limits

- Two real-Mongo regressions failed on the intended virtual-card eligibility
  assertion before correction, then passed after the correction.
- The affected suite passed 75 tests and 38 subtests with no failure/error/skip.
- Old exact A and B each passed all 19 applicable CI workflows, including strict
  security audit and the no-skips real-Mongo review/fulfillment gate.
- The scoped previous-behavior suite passed 64 tests and 38 subtests with no
  failure/error/skip. Its four restored Ready blobs equal both old and current
  production. Its old full-tree/intent proof is not a publishable new rollback.
- These results certify the unchanged Ready behavior. Fresh combined-source
  CI, a fresh A/B build/intent, and new final release checks are still required
  for this current-base integration.
- Live baseline for the reported order was one of three pieces Ready, with two
  blocked cards. No operational action was executed.

## Required publication sequence

1. Confirm the actual production ref and shared Release Guard, not only the
   PR's cached base.sha. Respect any other owner's active lease.
2. Complete current exact-source CI. Build twice using Node 22.23.2 and Yarn
   1.22.22 with https://mezansalla.com as the client backend origin.
3. Freeze a new external protocol-v5 intent; commit only
   `release/release-intent-v5.json` as B. Validate ancestry, source manifests,
   clean-clone adapter/package/runtime checks and the local rehearsal.
4. Re-read the actual production ref immediately before normal merge. Preserve
   A/B without squash/rebase; require final merge M's tree to equal B.
5. Prepare and rehearse the current-base scoped rollback below before Publish.
6. Only with an inactive shared guard, preserve unrelated /app work, align
   with reviewed production, and run the governed adapter. Run prepare, then
   prepublish immediately before one authorized Emergent Publish action.
7. Require a newer explicit Deployment Succeeded, the guard's three consecutive
   live verification probes, exact JSON build metadata, matching identity/hash/
   frontend flags, and a final inactive guard. Read back the reported order's
   eligibility and representative unaffected behavior before Android publication.

## Scoped recovery

From final merge M, restore only these four files from J:
`backend/preparation_piece_operations.py`,
`backend/review_local_policy.py`,
`backend/tests/test_g47_component_lifecycle_integration.py`,
`backend/tests/test_review_local_assembly.py`.

Retain all campaign changes, both dnspython 2.9.2 pins, all other production
work, and M's intent while constructing rollback source A_R. Freeze a new
intent-only B_R with M as its source base and rehearse the adapter/package/
runtime contract. Preserve exact branch, SHAs, identity and checks in the PR;
do not activate it by default.

Recovery restores code behavior only. It must not reverse stock consumption,
delete events, reset order/shipping states, or replay provider operations.
Reconcile in-flight outcomes if recovery becomes necessary, then use a fresh
official guard and the reviewed rollback release.

## State at this source checkpoint

Production changed by this task: **no**. No Ready merge, deployment lease,
Publish, or Android build/publication has occurred. The concurrent campaign
release belongs to its own owner; its live completion must be observed before
the shared environment is used for this release.
