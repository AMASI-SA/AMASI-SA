# Independent rebase proof-boundary review — 2026-10-02

## Verdict

The 11-test evidence supports the narrow conclusion that the existing Track F driver writer requires its exact bound delivery-proof source and remains fail-closed without it. It does **not** prove a completed rebase onto Production, a new photo-less financial recognition contract, or an existing late delivery-proof attachment workflow.

One adjacent existing A/B defect was found by source tracing: the driver replacement-payment-receipt UI uploads a new receipt after delivery, but that upload route rejects delivered assignments. This is separate from the missing delivery-proof lifecycle and does not make payment receipts valid delivery proof.

Specification: **PASS for the tested existing Track F boundary; PARTIAL for the overall Production rebase.** Engineering: the audit tests are meaningful and preserve financial assertions; the adjacent receipt-upload integration defect remains actionable. No product change or test/process execution was performed by this reviewer.

## Exact target and evidence

- Integration HEAD: `48bb39d983fe7003bc972c624a1508655a16f570`.
- Integration TREE: `5a898a13ade16093e55198fcd6c96027078a2eff`.
- Production comparison: `83363097d48e034dc7140a60c290efc684e1ffde`, TREE `1053db45d38aac9092a2cf55989cb6349fceffcd` (merged PR #1238).
- Worktree: `C:/Users/amasi/mz2-rebase-audit-20261002`.
- Read-only `git diff --name-only HEAD` found no tracked changes. Status showed only the audit script directory and parent-owned audit documentation directory as untracked. No merged runtime is present.
- Reviewed `scripts/testing/mz2_rebase_audit/test_track_f_proof_boundary.py`, `proof-boundary.log`, XML, runner, started/finished metadata and cleanup proof.
- XML independently parsed: **11 tests, 0 failures, 0 errors, 0 skipped; 14.495 seconds**. Log: 11 passed in 14.50s. The recorded runner exit code is 0.
- Runner uses an allowlisted environment, disabled dotenv, `MZ2_TEST_MONGO_URI=mongodb://127.0.0.1:27134/?replicaSet=mz2audit`, explicit synthetic authentication, and the existing real Motor/transaction fixture. The fixture creates a unique disposable database and synthetic opening. This is not a Production Opening Post.
- Runner records backend/workflow source unchanged; current Git diff independently confirms no tracked runtime modifications. Cleanup records no test databases remaining and port 27134 closed. These execution/cleanup facts are artifact evidence, not an independent rerun.

SHA-256:

| Artifact | Hash |
|---|---|
| New test file | `d2a58108cd18e0219b1509bf59dd260a4ecfc7c39a1ffb786f204f8dde13ed47` |
| Raw log | `3a18c99debf70d16f95e331a43e414d27bd23f7895218f993770bb85dee1fd0d` |
| JUnit XML | `22c25d81b19e86427b5149cba3bd43d73ba169017cac03035e8ff335caee4da3` |

## Requirement-to-evidence matrix

| Requirement | Observed evidence | Result and scope |
|---|---|---|
| No substitute delivery authority | Eight parameterized cases: canonical Salla delivery only, physical-cash-shaped observation, payment receipt, customer conversation, merely uploaded delivery proof, foreign owner, wrong bound assignment, omitted collection reference | All produce `shipping_driver_bound_delivery_proof_required`, twice through the existing writer and once through its observer. |
| No financial delta on missing proof | Exact before/after rows for V2 journal groups, legs, audit, sequences, shipping evidence/events and atomic owner | PASS in tested cases. Observer inbox changes intentionally remain permitted; this is not a whole-database no-write claim. |
| Pending state is visible | Real observer returns pending with exact missing-proof code and persists a pending inbox item | PASS; no automatic resolution or new source is implied. |
| Existing writer remains usable and idempotent | Exact bound-source row, two calls to `recognize_cod`, same evidence identity, only one added journal, verified driver AR 500 | PASS for sequential recognition. No new concurrency/fee/settlement claim. |
| No existing late proof upload | Actual evidence FastAPI router receives post-delivery upload; returns 404 `driver_assignment_not_found`; no proof inserted and financial snapshot unchanged | PASS at that registered route with synthetic auth. Not full-app JWT/native-client-access acceptance. |
| Pause remains authoritative | Explicit isolated fixture pause followed by recognition | 423 `mz2_writes_paused`, unchanged financial snapshot. |
| No Legacy authority in exercised native path | Existing fixture CommandListener rejects access to `general_ledger`, `accounts`, `counterparties`, `shipping_company_settings` | PASS for these monitored collections; not a new repository-wide SSOT audit. |

Two material coverage limits must stay explicit: the physical-cash alternative is a directly seeded, unsealed observation-shaped object, not a fresh valid C3 capture; source inspection establishes that `driver_facts` never consumes that field. The positive bound-proof fixture contains metadata, not original image bytes; it proves the current financial eligibility predicate, not upload-content validation. Neither limitation invalidates the tested missing-proof gate, but neither should be described as a new C3/Smoke end-to-end run.

## Existing callers and why alternatives do not close the gap

Line references below are to the exact integration HEAD above.

1. `backend/accounting_shipping_native_evidence.py:130`: `driver_facts` requires a delivered assignment and operational collection. Lines 144–148 require `store_delivery_delivery_proofs` with exact owner, driver, collection token, `status=bound`, and `bound_assignment_id`. Lines 166–179 pin the source and retain proof identity in financial facts. Canonical Salla delivery does not skip this requirement.
2. `backend/accounting_shipping_native.py:133`: both direct COD recognition and `recognize_fee_delivery` enter `_seal_delivery`; line 152 calls the same `driver_facts`. Existing journal replay occurs only after obtaining these facts (lines 162–169). Native driver-recognition, driver-fee, and retry endpoints (`accounting_shipping_native_routes.py:180`, `:195`, `:216`) provide no different producer.
3. `backend/accounting_shipping_native_observer.py:22`: the observer invokes this same service and retains failures as pending. It does not manufacture evidence. Actual delivery callers are `store_delivery_driver_app_routes.py:1943` and `:2123`.
4. `backend/accounting_driver_payment_review.py:49` and `:112`: before a new APPROVE **or REJECT**, the service requires an already sealed driver-responsibility record with a journal ID. Its later bound payment receipt (lines 141–151) authorizes settlement of that responsibility, not initial delivery recognition. POS-to-bank also requires the prior sealed approved review and journal (lines 237–241). Manual POS review remains valid under its existing contract; it is not an alternative delivery-proof producer.
5. `backend/store_delivery_payment_resubmission_routes.py:49`: resubmission requires delivered assignment plus rejected review. Lines 61–67 validate **RECEIPTS**; lines 83–132 update receipt/review/payment projections only. They neither bind `DELIVERY_PROOFS` nor set the collection's delivery-proof reference.
6. `backend/accounting_shipping_native.py:306`: generic cash settlement is limited by existing sealed cash-responsibility evidence and its dated journal balance. A handover is not a replacement delivery-recognition source.
7. `backend/store_delivery_cash_evidence.py:41`: C3 observation is explicitly nonfinancial, sealed, and bound to its original collection/proof reference. Eligibility at line 96 reads canonical facts but never calls or replaces financial `driver_facts`.
8. External courier recognition (`accounting_shipping_native_evidence.py:106`) resolves an explicitly confirmed courier and produces `party_type=courier`; using it for the driver would change financial identity and the responsible party. It is not wiring to the same driver contract.

The existing Track F specification makes this separation explicit: `docs/operations/MZ2-TRACK-F-20260930/CONTRACT-GAPS.md:100` requires driver assignment, outstanding snapshot, exact identity **and bound proof**. The source-sufficiency paragraph at line 86 applies to external couriers. The review contract at line 148 requires the existing sealed delivery responsibility.

## Why late upload plus binding is not already authorized wiring

Adding an endpoint is not, by itself, evidence of new business scope. The decisive issue here is the missing **post-completion evidence mutation contract**:

- Current upload only produces an `uploaded` row; it excludes delivered assignments (`store_delivery_payment_evidence_routes.py:229–240`, `:322`). Allowing that upload would still not satisfy the financial `bound` predicate.
- Existing binding happens within delivery completion. For cash, `store_delivery_delivery_commit.py:94–116` validates and binds the proof in the same transaction that creates the captured collection and observation. It does not provide an attach-after-completion operation.
- Replaying delivered cash is specifically read-back of the identical original payload. `store_delivery_delivery_commit.py:34–41` rejects a changed proof reference, and `store_delivery_driver_app_routes.py:1703–1712` invokes that retry or rejects a repeat status transition. Reusing completion to attach new proof would violate that replay contract.
- The C3 observation seal contains the original proof reference, and validation requires equality with the collection (`store_delivery_cash_evidence.py:62`, `:72–84`). Mutating a completed collection to a new token invalidates the original evidence unless the seal is rewritten or a new provenance/version relationship is designed. Silently rewriting the old observation would contradict immutable history.
- Financial facts also retain proof identity and hash collection source fields. Existing sealed-source conflict/reconciliation behavior must not be bypassed for an already-recognized assignment.

Therefore **no existing delivered-proof adapter can simply be connected** for a photo-less completed delivery. A late attachment could reuse the financial writer, but it still requires an explicit authority/lifecycle decision: who may attach, what original capture remains immutable, how a new reference is related to the completed observation, and when retry may use it. Those rules are absent from the delivered contracts. Alternatively accepting photo-less observations as financial authority changes Track F's existing evidence contract. Neither can be claimed as completed A/B wiring.

The decision is narrow: retain photo-less operational completion with Native responsibility/fee pending and release blocked for that path; or independently authorize a precise late-evidence/alternative-authority contract. No new writer is inherently required. Merely relaxing the upload status predicate, automatically copying receipt/conversation data, changing cash replay, or deleting the Track F guard is not a valid closure.

## Separate actionable A/B finding

**P1, high-confidence static source finding: rejected noncash replacement receipt cannot reach its existing resubmission route through the driver UI.**

- UI: `frontend/src/pages/AmasiDeliveryApp.jsx:322` calls `uploadReceipt` before `/payment-review/{assignment}/resubmit`.
- Upload: `backend/store_delivery_payment_evidence_routes.py:272–283` requires assignment `status != delivered`, returning 404 otherwise.
- Consumer: `backend/store_delivery_payment_resubmission_routes.py:49–59` requires `status=delivered` and review `status=rejected`.
- Existing history test bypasses that upload by directly inserting an uploaded replacement receipt (`backend/tests/test_mz2_driver_review_history.py:147–150`). Its real resubmit request therefore does not prove the complete replacement-upload UI flow.
- The same upload restriction exists at Production `83363097`; this is not caused by the proposed rebase.

Consequence: a driver following “upload replacement receipt” after rejection cannot submit a newly captured correction, even though the existing review/resubmission contract supports revisions. Smallest remediation direction: allow the **receipt** producer for the exact active owner/driver/delivered assignment with a current rejected noncash review, preserving permission/identity and no-financial-write rules; verify actual upload → resubmit → review. This is correction of an existing workflow (A/B), not authorization for late **delivery-proof** attachment. No remediation was implemented here, and no new execution of this adjacent finding is claimed.

## Non-actions and release implication

This reviewer inspected source, Git state and saved artifacts only, and wrote this external report. No repository edits, DB access, test reruns, services, Git mutations or deployment occurred. The recorded 11-test run is isolated local Mongo evidence. Production financial writes remain **0**. Merge, Deploy, Opening Post and Activation remain **NO**. Controls, 423/503, SSOT and Release Guard were not modified.

Existing 48bb39d acceptance remains evidence for that exact source; these tests do not certify a composed 83363097 rebase. Release Ready remains **NO** while the separate A/B defect and the photo-less financial-provenance decision are unresolved.
