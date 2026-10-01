# MZ2 Production rebase audit — no release claim

This audit preserves Integration PR1237 at its existing HEAD. The separate
`codex/mz2-rebase-audit-20261002` branch contains audit tests/evidence/documents
only. No financial product code, C3 implementation, guard or writer was changed.
C3 and the existing Acceptance Smoke B were not reimplemented or re-executed.

## 1. Current Production

GitHub Production branch `hotfix/prod-snap-meta-final`:
`83363097d48e034dc7140a60c290efc684e1ffde`, merged PR1238. This is Git source
identity, not a claim about a deployed runtime. Production was not changed by
this task. The old 901568 base is historical and cannot certify a new candidate.

## 2. Integration identity

PR1237 remains OPEN / Draft / unmerged:

- HEAD `48bb39d983fe7003bc972c624a1508655a16f570`
- TREE `5a898a13ade16093e55198fcd6c96027078a2eff`
- Reviewed original base `901568ccaaf510dc1f84d9c28f38d368d07dc64d`

The main Integration checkout and remote branch are kept at that exact HEAD.
This audit is not a Release Candidate and does not retarget or force-push PR1237.

## 3. Divergence and textual conflict

Fresh fetch, GitHub compare and `git rev-list --left-right --count
48bb39d...83363097` agree: **18 Integration-only commits / 13 Production-only
commits**. Merge base is 901568. The Production delta contains 11 changed files.

Read-only `git merge-tree --write-tree --name-only 48bb39d 83363097` exits 1:

- `backend/store_delivery_driver_app_routes.py`
- `backend/tests/test_store_delivery_accounting.py`

No rebase, cherry-pick or merge was performed. Git can represent a new reconciled
source without force-pushing the old branch, but the candidate is not currently
safe to accept: textual resolution alone cannot settle the financial contract
below. New source/intent identities and fresh acceptance of affected behavior
would be required; the current reviewed v5 intent must not be reused as-is.

## 4. Track F / PR1238 contract conflict

### Optional operational facts

PR1238 makes `delivery_proof_reference` optional in the existing delivered
action. If present, the original owner/driver/assignment validation and proof
binding still apply. If absent, it stores a null reference/URL and permits
operational delivery. Customer-conversation evidence remains optional. The
separate card/POS and bank-transfer receipt conditions remain mandatory under
their existing payment-method rules; optional delivery proof does not waive them.

The same Production PR adds the purpose-bound evidence-route allowlist, raw
payment projections/COD recovery and removes a separate operational print
confirmation prerequisite. These affect the C3 delivery producer and must be
retained/reconciled, not discarded by selecting the whole old route file.

There is a direct C3 compatibility boundary as well: the current cash delivery
transaction validates and binds the proof unconditionally, and
`store_delivery_cash_evidence.build_cash_evidence` requires a nonempty proof
reference. That reference is sealed and checked against the original collection.
Making only the route-level field optional would therefore not make photo-less
cash capture compatible with C3. C3 remains completed at its reviewed source;
its compatibility with the new optional-proof behavior has not been established.
The eleven tests below seed the Production-shaped delivered row to test Native
consumption; they do not execute a composed C3 delivery producer.

### Mandatory financial facts

The delivered Track F contract explicitly requires a delivered assignment,
outstanding collection snapshot, exact owner/driver/order identity **and bound
delivery proof**. `accounting_shipping_native_evidence.driver_facts` requires
the matching `store_delivery_delivery_proofs` row in `bound` state with the exact
assignment, and pins it into the source transaction. Its missing-source error is
`shipping_driver_bound_delivery_proof_required`.

The existing observer retains that result in `mz2_shipping_inbox_v2` as pending;
it does not post or invent a zero financial responsibility. The original full
expected collection remains 500 in the audit fixture while verified Native AR
is 0 because recognition was refused. These are different facts, not netting or
an instruction to reduce the driver's operational responsibility.

Track F is delivered in the unmerged Integration branch; PR1238 did not deliver
or change that native financial evidence module. It therefore cannot implicitly
amend its financial proof contract. The original Production pause and release
guard files have zero diff across 901568→83363097.

### Existing sources checked for reuse

| Existing source | Can satisfy current financial contract? | Reason / executed proof |
|---|---|---|
| Exact already-bound delivery proof with matching reference/owner/driver/assignment | Yes, existing writer directly | Positive Real Mongo case posts 500 once; identical retry returns already_posted and no second journal |
| Canonical Salla delivered snapshot alone | No for store driver | Negative actual writer/observer case rejects missing bound proof; courier-only sufficiency cannot be borrowed for driver identity |
| Driver physical-cash observation | No | Evidence/reconciliation has no financial authority; new audit fixture confirms writer still rejects absent bound proof; no C3 capture rerun |
| Bound payment receipt or customer-conversation image | No automatic equivalence | Their purposes/collections differ; both real writer cases remain pending without financial delta |
| Uploaded but unbound delivery proof | No | Does not meet the current bound-assignment contract |
| Foreign or other-assignment bound proof | No | Owner/assignment isolation retained |
| Valid bound proof row but omitted collection reference | No guessing | Writer refuses to choose an unreferenced source automatically |
| Late upload through existing delivery-proof endpoint | Not available | Actual HTTP request for a delivered active assignment returns 404 `driver_assignment_not_found`; no source is stored |

The existing payment review and its rejected-payment resubmission operate on
payment receipts and an already-recognized sealed driver responsibility. They
are not a delivery-proof producer or a substitute for initial responsibility.
C3 replay also cannot amend a historical sealed proof reference. An adapter
cannot manufacture the missing source or promote another evidence purpose.
Simply enabling a late upload only produces an `uploaded` proof. Existing
binding belongs to original delivery completion, while retry rejects a changed
proof and C3 integrity validation rejects a changed collection reference. A new
attachment/version relationship must therefore be specified before reuse.

## 5. Classification and exact C boundary

**A/B portions:** composition of the purpose-bound route permission, original
proof-present path, correct operational COD projections, and conflicting tests
is integration work. No new journal writer is required. Existing current-source
Accounting/Track F CI and the positive bound-proof audit case validate reuse.

**C remains real for photo-less operational delivery → complete Native driver
recognition.** No equivalent approved financial source or existing late-proof
binding lifecycle is delivered. The writer/core exists; the missing piece is
the source/authorization lifecycle, not a request for a second financial engine.

- Source: PR1238's delivered collection with a null delivery-proof reference.
- Existing contract: Track F exact bound driver delivery proof and current
  financial recognition writer; all 423/503/SSOT controls remain.
- Missing contract: how a photo-less completed delivery obtains the mandatory
  owner/driver/order-bound financial delivery proof afterwards, or which exact
  verified source is formally authorized as equivalent. Neither source may be
  assumed from cash amount, Salla status or a payment receipt.
- Reuse limit: current upload route rejects delivered assignments, financial
  retry does not create proof, and C3 immutable replay rejects changed proof.
- Executed gap proof: eight alternative-source negatives, actual late-upload 404,
  correct existing writer/idempotency positive, and unchanged paused 423:
  **11/11 tests PASS as boundary tests**, while the attempted unsupported Native
  recognition remains FAIL CLOSED. Passing a negative test is not C closure.
- Required independent authorization: a specified auditable post-delivery proof
  capture/binding contract using the existing proof and journal contracts, or an
  explicitly reviewed equivalent financial source. It must preserve actor and
  owner authority, original history, atomicity and idempotency. Until then do
  not make proof optional financially, remove an assertion, or invent a writer.

The operational COD fallback also expands facts consumed by C3: source currency
and paid-amount ambiguity need regression checks when the actual merge is later
authorized. They are not evidence that current 48bb financial assertions failed.
No speculative amount or default financial identity was implemented in this audit.

## 6. Fresh CI, not historical green

Six existing non-deployment workflows were actually dispatched on the unchanged
Integration branch at exact 48bb. GitHub accepted all six requests with 204. Their
run IDs, checked-out head, status, job steps and read time are retained in
`evidence/ci-status.json`; original dispatch is `evidence/ci-dispatch.json`.

Final readback: **6/6 workflows and 12/12 jobs completed SUCCESS**, all at
`48bb39d983fe7003bc972c624a1508655a16f570`. This is the scoped fresh audit CI,
not the historical 39-check matrix or a test of reconciled Production source.

| Workflow | Fresh run |
|---|---|
| MZ2 Track F native shipping | https://github.com/AMASI-SA/AMASI-SA/actions/runs/36940571533 |
| Store Delivery V1 | https://github.com/AMASI-SA/AMASI-SA/actions/runs/36940574951 |
| MZ2 Accounting Module | https://github.com/AMASI-SA/AMASI-SA/actions/runs/36940578007 |
| G47 Focused Integration | https://github.com/AMASI-SA/AMASI-SA/actions/runs/36940580824 |
| Security Gate | https://github.com/AMASI-SA/AMASI-SA/actions/runs/36940584000 |
| CodeQL | https://github.com/AMASI-SA/AMASI-SA/actions/runs/36940586575 |

These are workflow_dispatch runs: the connector's PR-only workflow listing does
not return them. Direct GitHub API run records prove the exact 48bb source. No
old 33fd result is counted, and no claim is made that these runs tested a merged
83363097 candidate. The local new audit ran on real isolated Mongo 8 replica 27134
using unchanged 48bb backend/workflow source, 11 cases, 0 failures/skips, 14.50s.
Only synthetic fixtures and the test observer changed; the replica/database
cleanup proof is retained. Reproduce with the command in `test-started.json`
against a newly created loopback replica; never a Production database.

## 7. Remaining path to a Release Candidate

1. Resolve the specified financial delivery-proof lifecycle/source C decision.
2. Create a separate reviewed source incorporating actual Production 83363097,
   reconcile both conflicting files and preserve all delivered C closures.
   No force push or mutation of the Production branch.
3. Run fresh affected/full CI, Native/SSOT/owner/atomic/idempotency regressions
   on that new immutable source. Existing C3 and Acceptance Smoke proof remain
   attributed to their original source and are not automatically Production proof.
4. Complete outstanding business acceptance honestly: sixteen-stage **setup**
   acceptance is PASS; final business UAT is **not final PASS**. Opening,
   Activation and physical-stock approval are NOT EXECUTED. No forbidden action
   is authorized by this report or by green tests.
5. Build/review a fresh governed v5 source-A/intent-B pair only after its source
   acceptance is actually satisfied. The old or Production PR1238 intent cannot
   certify the reconciled Integration source.

### Adjacent A/B finding, not a substitute for the C source

The read-only review found an existing rejected-payment resubmission mismatch:
`frontend/src/pages/AmasiDeliveryApp.jsx:322` first uploads a replacement receipt,
but `backend/store_delivery_payment_evidence_routes.py:278` excludes delivered
assignments while `backend/store_delivery_payment_resubmission_routes.py:51`
requires delivered assignments. This restriction also exists at Production
83363097. The current history test seeds the replacement receipt directly; it
does not cover actual upload. This is a concrete static wiring finding, not a
newly executed end-to-end failure. A focused upload/resubmit reproduction and
an eligibility fix belong to the existing A/B workflow when work resumes.
No adjacent product change was made during this stop-at-C audit, and changing
receipt upload would not create the missing financial delivery proof.

Release Readiness=NO. Production financial writes=0. Write-control=UNCHANGED.
Merge=NO; Deploy=NO; Opening Post=NO; Activation=NO. No Production mutation,
lease, schedule or live financial probe was performed.
