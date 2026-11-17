# Supplier receipt Ready policy

## Contract

The owner explicitly removed component/source-approval prerequisites from Ready
for a physical product already received from its supplier while the current
Salla order is in progress. This supersedes the former business requirement
that matching component revisions and approved source hashes gate this path.

Current status evidence, tenant/actor authorization, actual receipt, required
services, configured employee custody, explicit holds/instructions and current
whole-order product/unit coverage remain independent requirements. Other
direct/virtual Ready paths retain their existing policy.

## Source checkpoint

- Repository: `AMASI-SA/AMASI-SA`.
- Source task branch: `codex/ready-supplier-receipt-source-20261118`.
- Original WIP branch retained: `codex/ready-supplier-receipt-policy-20261118`.
- Initial production source: `d7c9ed77137bed0948f4d32a34c6bf571d75e6f8`.
- Current integrated production base: `b92d3eb8d12920663a052744c9c0d2773a785e1a`.
- First remote stock checkpoint: `2a1fb9db5633f9d3a5aa6c14bb9ae389de96063d`.
- Original WIP PR: https://github.com/AMASI-SA/AMASI-SA/pull/1975 . Its body
  links the active source PR, whose body records the latest exact source SHA
  and CI evidence. Both branches remain recoverable.
- The local checkout is isolated from concurrent mobile printing work.
- **WIP — not release-ready.** No release intent, merge, live data change,
  release lease or production publication has occurred.

## Implementation

- One supplier receipt predicate drives search and the actual Ready transaction.
- A saved receipt basis supports complete current product coverage without
  depending on approval/component acceptance. Missing/extra/duplicate units and
  incomplete mixed-order work cannot complete an order.
- The same proven basis is retained through the existing delivery outbox,
  status display and shipping invalidation. Provider identity, status,
  shipping/material evidence, claim ownership and effect journaling remain.
- Healthy component consumption must remain correct. Unavailable component
  evidence may defer only during strictly read-only validation, before any
  inventory write; the Ready record/event records that inventory outcome.
  Mutation failures cannot be swallowed. No new background worker is added.
- Source flags, supplier receipts and approved snapshots are not rewritten to
  manufacture eligibility.
- Explicit Ready retries can resume an interrupted reviewed workflow without
  repeating Ready events or consumption. The card exposes the same retry.
- Existing non-piece bundle parents retain their read-only bundle proof. An
  unavailable bundle leaves the supplier piece Ready and the order incomplete;
  it does not manufacture a physical bundle piece or ignore missing products.

## Verification and remaining work

- Initial remote checkpoint preserves the independently reviewed inventory
  preflight/deferral service and its regression tests. The integrated source
  checkpoint adds the shared Ready predicate, completion/printing consumers,
  current-unit coverage and guarded retries.
- Stock-focused verification: 27 tests and 7 subtests passed, zero skips. Tests
  include all-location validation before writes, healthy consumption/retry,
  and propagation of mutation failures after the first write. They replace
  only the owner boundary and do not claim real-Mongo rollback evidence.

- The first search regression was RED on the prior code: four failures for
  received supplier pieces under local source flags, eight retained negatives
  passed. The revised search suite then passed twelve tests.
- The isolated test interpreter uses the exact direct pins from the existing
  Review Completion Resumable CI workflow. No application dependencies were
  changed.
- Local MongoDB 8.0.12 cannot start: its allocator fails before server startup
  because `/sys/devices/system/cpu/possible` is unavailable; Docker is absent.
  Real transaction and API race acceptance must execute in the existing CI
  disposable replica set. Skips are not acceptance.
- Independent review corrected healthy-stock preservation, legacy read-only
  outbox compatibility, claimed-versus-current source fingerprints, current
  unit membership, the scan overflow sentinel, durable post-handoff printing,
  real inner courier generation and reachable reviewed-workflow retries.
- An affected local run passed 283 tests plus 90 subtests; 160 real-Mongo cases
  were skipped and are not acceptance. Later targeted Ready/search/bundle tests
  passed 142 cases without skips. The inventory snapshot preflight correction
  first reproduced ten unsafe-consumption failures, then passed 117 targeted
  Ready/stock cases. Missing or stale approved snapshot revisions now defer
  inventory consumption before writes and do not block the supplier Ready fact.
- Final integrated local run: **339 passed, 161 skipped, 90 subtests passed**,
  exit 0. Skipped replica-set cases remain explicitly unverified locally.
  The command selected the four supplier policy suites, source-pending search,
  preparation/search/custody, local assembly, delivery/status/print/legacy/v2
  readback, courier compatibility and stock consumption. It used the pinned
  test interpreter with `--noconftest -p no:cacheprovider --asyncio-mode=auto`.
  The CI gate requires all 41 local assembly integration tests and explicit
  minimums for each new supplier suite, with zero skips/failures/errors.
- The first WIP candidate run 44182130589 failed after production advanced.
  Integrated checkpoint `0d1a18d0fcd3da185ae6d68fc7b49b40bbfc4cd7` preserved
  the new base but run 44183973577 rejected its full intent-file merge history.
  The final source branch starts directly from the reviewed b92d3eb8 base and
  carries the integrated source tree, preserving the WIP separately. No reset,
  force push or release-guard change is used. No intent is frozen yet.
- At checkpoint 0d1a18d0, real-Mongo Review run 44183973477 passed 35 refresh
  cases but stopped in Android recovery: one optional-Ready unit fixture had
  no current canonical source. The fixture now contains a real mapped source
  and unit and retains real missing-plan inventory deferral; its focused local
  module passed 16 tests with one explicitly Mongo-only skip.
- Other 0d1a18d0 compatibility evidence: preparation backend 168 passed,
  frontend 64 passed with build; assembly settings 15 passed; performance
  backend 33/frontend 49 passed; shipping groups 26, 231 and 597 passed (one
  existing skip in the last group). These do not replace exact-source main
  replica-set acceptance. The active PR records the final run conclusions.
- Next: verify the clean source branch's exact SHA, finish its real-Mongo and
  release-candidate CI, record final evidence in the active PR, and report
  source readiness separately from production deployment.

Issue #1006 is full. This task document and its draft PR are the continuation
record. Keep any source checkpoint explicitly WIP until the required evidence
is complete. No customer payload or credentials belong in this record.
