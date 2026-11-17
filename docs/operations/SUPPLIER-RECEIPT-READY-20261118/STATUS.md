# Supplier receipt Ready policy

## Authorized release preparation — 2026-11-18 Riyadh

The owner explicitly instructed: publish this update. The source fix was
previously verified at `627cd57eea162c32d475fe99a33ffb11d84e4574`, tree
`cb6c215e8faa67d3ef75612b79780fd896f4324f`, with all 20 workflows successful.
Its exact verification handoff remains at documentation commit
`2d8b2bca9507f8abd121d569fd49aeb5c045ab3e` and PR #1981.

Before release, the production branch advanced independently through balances
latency PR #1980 to `f2d806efc18964a2cc7c3a475346590cbe8b1262`. This is a
governed source and reviewed-intent change. The next source candidate starts
directly from that reviewed base and carries the unchanged supplier source/test
delta. It preserves the complete balances improvement and current intent; no
old source/intent merge, reset or force push is used.

- Release task branch: `codex/ready-supplier-receipt-release-20261118`.
- The active release PR records exact new A/tree and subsequent intent-only B.
- Source/test integration has no file conflicts with the balances change.
- Earlier candidate artifact `14377200290` remains evidence only for its old
  source/base; it must not be frozen as this new source's intent.
- No new Android APK is needed; Ready eligibility is supplied by the server.
- Release work is in progress. No lease, merge, host mutation or publication
  has occurred in this release continuation yet.
- Next: preserve new A remotely, verify exact-source CI and its new candidate,
  create intent-only B, complete the governed clean-clone rehearsal, inspect
  the shared host guard, then prepare/prepublish/publish and verify three
  consecutive live identity probes. Never reuse another conversation's lease.

Production changed by this task: **no** at this checkpoint. The active PR and
subsequent release handoff will record the final publication evidence.

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
- Source task branch: `codex/ready-supplier-receipt-release-20261118`.
- Prior verified source retained on
  `codex/ready-supplier-receipt-current-20261118` at
  `627cd57eea162c32d475fe99a33ffb11d84e4574` (PR #1981).
- Previous source checkpoint retained on
  `codex/ready-supplier-receipt-source-20261118` at
  `67487226d664140ff6ea2ea1bc64fc5b0c8a4605` (PR #1978).
- Original WIP branch retained: `codex/ready-supplier-receipt-policy-20261118`.
- Initial production source: `d7c9ed77137bed0948f4d32a34c6bf571d75e6f8`.
- Current integrated production base: `f2d806efc18964a2cc7c3a475346590cbe8b1262`.
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
- Exact checkpoint `67487226d664140ff6ea2ea1bc64fc5b0c8a4605`, tree
  `39a09f6ccde614c71dc14c7d3baa89e88727baba`, passed 17 of 19 workflows.
  Operational Stock Eligibility run 44185118022 executed **4,833 tests plus
  7 subtests**, zero skips/failures/errors. Release Readiness 44185117920
  passed candidate classification and reproducible builds. Shipping Print
  Boundary 44185117827, Security Gate and CodeQL also passed.
- Review Completion Resumable run 44185117929 passed 35 refresh and 329
  Android recovery cases, then executed all 1,029 main cases with 16 failures,
  zero skips/errors. All **184 new supplier cases** passed: Ready 82,
  inventory deferral 35, bundle coverage 39, delivery/fingerprint 28.
  G47 run 44185117739 had five matching legacy fixture/expectation failures;
  Review also inherits those five cases in the zero-price suite.
- The followup changes tests only: five G47 cases now seed real canonical
  items before reservation and full receipt evidence; missing-address/unit,
  stock totals, event idempotency and cancelled virtual-work assertions remain.
  Two LocalAssembly fixtures add the required employee receipt timestamp/actor.
  Instruction fixtures use real current item quantities, canonical line IDs
  and actual custody without relaxing batching/instruction/actor assertions.
  Currency reconciliation now asserts that already received supplier products
  are eligible both before and after repair while an unreceived product stays
  blocked; dry-run and financial proof assertions remain unchanged.
- The Review gate's delivery minimum remains 28, applied to the whole module
  (25 delivery tests plus three fingerprint tests). Zero-skip/failure/error
  gates and disposable real-Mongo execution remain unchanged.
- The fixture checkpoint passed **205 local tests plus 24 subtests**, exit 0;
  four instruction cases require Mongo and were explicitly skipped locally.
  Collection found 89 cases across the four changed integration modules.
  Independent read-only review found no remaining fixture/gate blocker.
- Production source advanced independently to b009ee50 through Android
  shipping-label parity PR #1974. This source checkpoint carries that complete
  reviewed base, including its mobile read behavior/tests and release intent,
  then applies the supplier policy and fixture changes. Earlier task branches
  and PRs are retained; no release-intent history is rewritten or bypassed.
- Next: verify the new source branch's exact SHA/tree and retained intent,
  complete real-Mongo Review/G47 and release-candidate CI, and record final
  exact-head evidence in the active PR. Source readiness and production
  deployment remain separate. Production changed by this task: **no**.

Issue #1006 is full. This task document and its draft PR are the continuation
record. Keep any source checkpoint explicitly WIP until the required evidence
is complete. No customer payload or credentials belong in this record.
