# Supplier receipt Ready policy

## Final handoff — 2026-11-17

**Source implementation verified; all 20 triggered workflows passed.**
Production changed by this task: **no**.

- Canonical source A: `627cd57eea162c32d475fe99a33ffb11d84e4574`.
- Verified source tree: `cb6c215e8faa67d3ef75612b79780fd896f4324f`.
- Reviewed base J: `b009ee50a6b932368be8ad104c8c1b75e584b4e2`.
- Active Draft PR: https://github.com/AMASI-SA/AMASI-SA/pull/1981 .
- Source branch: `codex/ready-supplier-receipt-current-20261118`.
- This final status is retained separately on documentation branch
  `codex/ready-supplier-receipt-handoff-20261118` so the tested source A and
  candidate artifact remain unchanged. This documentation commit is not a
  deployment source or an intent-only B. The active PR links its exact SHA.
- Earlier PRs #1975 and #1978 are closed as superseded; their remote branches
  and checkpoints remain recoverable. The canonical checkout was verified
  clean at source A. Unrelated mobile printing work was not modified.
- No release intent B, release lease, merge, rehearsal, publication, provider
  write, or live order/receipt/status mutation occurred.

### Exact-source acceptance

| Verification | Passed evidence |
| --- | --- |
| Review Completion Resumable 44190015862 | Main **1,029 passed + 596 subtests**; Android **348 passed**; refresh **35 passed**; source snapshot **124 passed**; acceptance guard **1 passed**. Final XML and minimum-count gates passed with zero skips, failures or errors. |
| Required Review coverage | LocalAssembly **41**; supplier Ready **82**; supplier delivery/fingerprint **28**; stock deferral **35**; bundle coverage **39**; instruction search **4**. The repaired inherited zero-price scenarios and currency expectation pass in the successful main command. |
| Operational Stock Eligibility 44190015755 | **4,833 passed + 7 subtests**, 338.75 seconds; XML confirms 4,833 executed cases and zero skips, failures or errors. |
| G47 Focused Integration 44190015571 | Projection **228 + 88 subtests**; acceptance **331 + 127 subtests**; order cost **436**. All real-Mongo execution gates have zero skips, failures or errors. UI **140 tests / 10 suites**, zero skips, build passed. |
| Shipping Print Boundary 44190015483 | Groups **26**, **231**, **597** passed. One pre-existing skip remains in the last shipping group; it is not counted as supplier-policy acceptance. |
| Release Readiness 44190015655 | Source/base classification, backend preflight, Node contracts, both frontend builds, reproducibility, candidate retention, and clean-worktree checks passed. |
| Other workflows | Production Preparation, Fulfillment V2, performance, supplier invoice, both Snapchat suites, Qoyod freshness, mobile settings, direct receipt, Build20 components, native shipping, accounting, CodeQL, Security Gate and operational balances all passed. |

Primary evidence:

- https://github.com/AMASI-SA/AMASI-SA/actions/runs/44190015862
- https://github.com/AMASI-SA/AMASI-SA/actions/runs/44190015755
- https://github.com/AMASI-SA/AMASI-SA/actions/runs/44190015571
- https://github.com/AMASI-SA/AMASI-SA/actions/runs/44190015483
- https://github.com/AMASI-SA/AMASI-SA/actions/runs/44190015655

Review artifact: `14376233835`,
`review-completion-627cd57eea162c32d475fe99a33ffb11d84e4574`, ZIP SHA-256
`6191ce07fe2c1cc97c94f86b2885a8b1a0201713e486ec8492818686d5c7121b`,
expires `2027-02-15T22:26:06Z`.

Release candidate artifact: `14377200290`,
`release-v5-reviewed-intent-candidate`, ZIP SHA-256
`f7cdbda569c44a7196c1375b8fb9da681b71a9daa8042265592b401baf2e7513`,
expires `2026-11-19T22:27:46Z`. Both frontend builds produced artifact tree
`feca50cf723178a51482b5552d3b5d994ad735e3a946ca067f0256c2814bfae5`.
This is source-candidate evidence only: deployment rehearsal and redeploy jobs
were skipped, and no B was frozen.

### Changed scope and next action

Runtime changes are limited to `supplier_receipt_ready_policy.py`,
`preparation_piece_operations.py`, `stock_component_consumption_service.py`,
`assembly_completion_delivery.py`, `assembly_status_policy.py`,
`fulfillment_carrier_label.py` and `order_engine/amasi_shipping_compat.py`.
The source PR also changes eleven related test modules, the Review CI selectors
and minimum gates, and this task record. Android shipping-label parity from J
is carried unchanged.

Implementation and verification are complete. The active PR remains a draft
source candidate, with no merge or publication authorized by this task. The
next safe continuation is to review source A and its retained evidence. A
separately authorized release must check the current reviewed base and guard
state, obtain the candidate while retained or rebuild it, and follow protocol
v5 to produce an intent-only B and the required rehearsal/publication/probes.
Do not use this documentation branch as source A or treat a candidate build as
a deployment. No additional source changes or test reruns are needed for this
handoff.

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
- Source task branch: `codex/ready-supplier-receipt-current-20261118`.
- Previous source checkpoint retained on
  `codex/ready-supplier-receipt-source-20261118` at
  `67487226d664140ff6ea2ea1bc64fc5b0c8a4605` (PR #1978).
- Original WIP branch retained: `codex/ready-supplier-receipt-policy-20261118`.
- Initial production source: `d7c9ed77137bed0948f4d32a34c6bf571d75e6f8`.
- Current integrated production base: `b009ee50a6b932368be8ad104c8c1b75e584b4e2`.
- First remote stock checkpoint: `2a1fb9db5633f9d3a5aa6c14bb9ae389de96063d`.
- Original WIP PR: https://github.com/AMASI-SA/AMASI-SA/pull/1975 . Its body
  links the active source PR, whose body records the latest exact source SHA
  and CI evidence. Both branches remain recoverable.
- The local checkout is isolated from concurrent mobile printing work.
- Source verification is complete. No release intent, merge, live data change,
  release lease or production publication has occurred; the final handoff above
  supersedes the earlier WIP evidence below.

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

## Verification history

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
- The planned exact-source Review/G47, stock and release-candidate checks are
  now complete; final evidence and the remaining release boundary are recorded
  above and in the active PR. Production changed by this task: **no**.

Issue #1006 is full. This task document and its draft PR are the continuation
record. Keep any source checkpoint explicitly WIP until the required evidence
is complete. No customer payload or credentials belong in this record.
