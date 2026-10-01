# New C gate: post-delivery financial delivery evidence

Status: **STOP AT NEW C / proposed contract, NOT approved for implementation**.
This document records the user's stop instruction. It neither changes a runtime
contract nor authorizes a financial writer, mutation, rebase, merge or release.

## 1. Frozen identities and final blocker

- Production Git source: `83363097d48e034dc7140a60c290efc684e1ffde`.
- Integration: `48bb39d983fe7003bc972c624a1508655a16f570`.
- Integration tree: `5a898a13ade16093e55198fcd6c96027078a2eff`.
- PR1237: OPEN / Draft. Divergence remains 18 Integration-only / 13 Production-only.
- C3 completed at its reviewed source; existing Acceptance Smoke B PASS only;
  16/16 setup acceptance PASS only; final Business UAT NO.
- Existing fresh exact-48bb CI: 6/6 workflows, 12/12 jobs PASS. Real Mongo
  boundary evidence: 11/11 PASS. These are retained evidence, not a new run.

Final C blocker: an operationally Delivered assignment with no original bound
delivery proof has no delivered contract for obtaining and authorizing that
mandatory financial source afterwards. PR1238's optional operational photo does
not waive Track F's owner/driver/order/delivery-bound financial evidence.
Payment receipts, Salla status and physical-cash observations are not equivalent.

## 2. Existing contract versus missing contract

Source references are to Integration 48bb unless explicitly stated otherwise.

| Existing contract / implementation | Missing part |
|---|---|
| `store_delivery_payment_evidence_routes.py`: driver image validation, SHA256, immutable image bytes, token, owner/driver/assignment, server created_at and actor; upload excludes Delivered | Authorized post-delivery upload eligibility and reason; an upload alone is not bound financial proof |
| Original delivery binds proof to assignment; `accounting_shipping_native_evidence.driver_facts` requires exact bound proof referenced by the collection | A versioned late attachment authority the consumer can verify without editing the original collection reference |
| `store_delivery_delivery_commit.cash_delivery_retry` accepts only the same original proof; `store_delivery_cash_evidence` seals that reference | Append-only provenance connecting a later artifact to the original delivery/C3 snapshot without changing its seal or pretending it existed earlier |
| Existing financial writer seals recognition once with the original delivery event time and existing owner/cutover/pause gates | Eligibility for a late evidence source; atomic selection/pinning of one approved attachment; no alternative recognition time |
| Existing owner/actor checks, Mongo transactions and restricted operational profiles | Explicit late-attachment capabilities and audit schema; existing profiles do not authorize an arbitrary new collection or operation |

The gap is a **source/authority/lifecycle contract**, not a missing journal engine.
Existing infrastructure can be reused in parts. No end-to-end reusable late
attachment implementation exists; merely allowing upload after Delivered or
editing a collection token would not close it.

## 3. Proposed identity contract — subject to separate approval

All identity comes from authenticated, owner-scoped persisted records. The client
does not choose a tenant, financial account, driver identity or recognition date.

| Identity / fact | Required binding |
|---|---|
| Owner | Existing merchant `user_id`; driver `created_by`, assignment, collection, canonical order and proof must agree |
| Order | Existing canonical Salla source order ID plus order_number; verify exact assignment/collection/source agreement; ambiguous or missing identity fails closed |
| Driver | Existing `store_drivers.id` plus the linked account_user_id and original delivery actor; no transfer to another driver through attachment |
| Delivery | Exact assignment_id, collection_id, original Delivered event/delivered_at and original reference (including explicit absence) |
| Original evidence | Preserve the C3 ID/seal and original reference if present; otherwise record proven absence. Do not manufacture a historic C3 record |
| New evidence | Existing delivery-proof token, evidence_kind=delivery_proof, byte SHA256, stored image reference, size/type, uploader identity; no receipt/conversation token promotion |
| Attachment | New nonfinancial attachment/event ID, schema version, idempotency key, original-source fingerprint, reason and review decision; this is not a new financial account/party identity |
| Time | Keep delivered_at, claimed capture time (if supplied), server uploaded_at, server attached_at and server reviewed_at separate, timezone-aware. A claimed capture time is not independently verified and must not be substituted for server history |

The precise authorized actors remain part of the decision. Proposed minimum:
the original linked driver submits actual delivery evidence; an accountant with
the owner's existing review authority verifies it for late use. Driver upload
alone cannot grant financial authority. Disabled, reassigned, foreign or ambiguous
actors fail closed; no automatic delegation/default approver is proposed.
This new review purpose is not implicitly authorized by existing POS review.

## 4. Proposed append-only attachment design

1. Read the original delivered assignment, collection, canonical identity and
   any C3 seal. Validate their current consistency. Preserve original bytes,
   reference, amount, delivery time and seal. Do not replay delivery completion.
2. Accept a separate delivery-proof artifact using the existing validated image
   storage primitives after a specifically authorized post-delivery eligibility
   check. Store it as an unapproved candidate, with actual server upload time.
3. Append a nonfinancial attachment event referencing the exact original delivery
   and C3 seal/absence plus the new artifact/hash. Original C3 and collection
   records are not patched, re-sealed, deleted or backfilled. Audit records and
   later decisions remain append-only; any operational status projection must
   be derivable from those records and must not overwrite the original capture.
4. Append APPROVED or REJECTED after the approved actor/evidence checks. Rejection
   has no financial effect. Approval authorizes only this artifact for the same
   owner/driver/order/assignment; it does not alter COD, cash variance, fees or
   recognition timing and does not itself post a journal.
5. Enforce one unambiguous approved binding for the source under owner-scoped
   atomic control, expected revision and a uniqueness constraint. Identical
   requests return the same event; conflicting payloads/revisions fail closed.
   Binding a new proof and recording approval must be atomic; abandoned upload
   candidates confer no authority. No transaction spans a user's review wait.
6. A separately authorized, versioned evidence resolver may use the original
   valid binding, or the unique approved extension where the original reference
   is absent. It must not override a mismatched/invalid original proof or an
   already sealed financial source. Pin and verify original snapshot, attachment
   revision and new artifact together in the existing financial transaction.
   Current `driver_facts` reads the collection token directly, so this resolver
   does not exist today and cannot be claimed as configuration-only wiring.
7. After explicit future authorization, the existing recognition writer may be
   retried normally. Keep its existing order-based idempotency, economic hash,
   delivery-event effective time, source/cutover/period/423/503 checks and journal
   semantics. A blocked period or changed source remains blocked; never choose
   upload/review time as a replacement accounting date or rewrite a posted entry.

Attachment and approval are proposed **evidence-only** actions. They cannot
update Bank, receivables/payables, journals or write-control. They must not be
used to bypass a pause, an existing operational restriction or the Release Guard.
Reuse of transaction machinery requires an explicit narrow capability, not
broadening the existing C3 profile silently.

Corrections must append a new version and retain old evidence. Selecting another
version after financial recognition is outside this minimal proposal; do not
change sealed financial evidence or manufacture a reversal. Cancellation, driver
reassignment, duplicate delivery, competing approvals and source changes must
retain history and recheck eligibility rather than silently transferring proof.

### Important C3 compatibility limit

Current C3 cash capture requires a nonempty proof during original completion.
An append-only late attachment does **not** by itself enable future photo-less
C3 completion and does not repair absent historical physical-cash observations.
The contract decision must separately specify how future original captures
represent a missing proof, with an explicit source version if required, while
financial recognition stays blocked. No existing sealed record may be migrated
or re-sealed to achieve that. C3 completion at 48bb is preserved; optional-proof
producer compatibility is not claimed PASS or authorized here.

### Evidence required before closing this C

Use real Mongo and actual upload/review/retry routes: valid late attachment;
wrong owner/order/driver/assignment; invalid bytes/hash; upload without approval;
rejection; identical and conflicting retries; simultaneous approvals; transaction
rollback; cancel/reassign after capture; missing/changed original snapshot;
unchanged original C3 bytes/seal and cash totals; approved attachment creates
zero journals; normal existing writer retry creates at most one recognition at
the existing economic time; 423/503 and period/cutover guards; already-posted
source cannot be replaced; Legacy financial reads/writes zero. Browser coverage
must exercise the full user path. These are planned criteria, **not executed**.

## 5. Separate A/B receipt bug

The driver replacement-payment-receipt UI first calls uploadReceipt
(`frontend/src/pages/AmasiDeliveryApp.jsx:322`). The receipt producer excludes
Delivered (`backend/store_delivery_payment_evidence_routes.py:278`), while
resubmission requires Delivered plus a rejected review
(`backend/store_delivery_payment_resubmission_routes.py:49-59`). The current
history test directly seeds the replacement receipt. Production 83363097 has
the same restriction; it is not introduced by the proposed rebase.

Classification: existing workflow A/B; confirmed static path mismatch, no new
end-to-end reproduction in this documentation turn. Future narrow remediation:
permit replacement **receipt** upload only for the exact active owner/driver
assignment and rejected noncash review, then test actual upload -> resubmit ->
review with identity/isolation/race/no-financial-effect checks. This does not
authorize or substitute late **delivery proof**. No A/B fix is implemented now.

## 6. Future re-foundation plan — do not execute now

1. Keep PR1237/48bb and this audit branch recoverable. After the C decision,
   re-read Production and the canonical handoff; if Production moved beyond
   83363097, report that drift rather than silently selecting a new base.
2. Use a new integration branch/worktree based on reviewed Production83363097.
   Map all 18 Integration-only commits from common base901568, including merge
   ancestry and equivalent changes. Replay the reviewed source changes with an
   explicit mapping; do not reset or force-push the existing Integration branch.
   Do not replay an old release-intent as if it certified the new source.
3. Resolve `backend/store_delivery_driver_app_routes.py` and
   `backend/tests/test_store_delivery_accounting.py` with both accepted contracts:
   optional operational photo, mandatory financial proof, unchanged C3 historical
   integrity. Preserve PR1238 permissions/projections and all delivered C closures.
   Never choose a whole conflict side or delete an accounting assertion as a fix.
4. Review resulting tree, commit correspondence, behavioral tests and differences
   against both parents; ancestry alone does not prove behavior preservation.
   Publish only the new task branch for review, without changing Production.
5. Freeze a new source A only after source verification; generate/review a fresh
   v5 intent-only B. Never rebase or reuse an already frozen A/B release pair.

## 7. Remaining acceptance and stop status

After authorized C/A-B work and source reconciliation: full backend/frontend
regression and build; real Mongo rollback/isolation/idempotency; affected Track F,
C3, shipping/COD/POS/payment review, G47 and remaining accounting path suites;
Security Gate/CodeQL; MZ2_ONLY_SSOT_AUDIT; business UAT evidence per all stages;
release readiness and source/intent verification for that exact new source.

Existing Smoke B is Acceptance-only evidence and is not rerun by this document.
Existing setup acceptance is 16/16 PASS only. Full Business UAT is NO; physical
stock approval, Opening and Activation remain NOT EXECUTED. Any necessary new
source acceptance must use its approved environment; Production actions still
require separate explicit authorization. Green CI does not supply missing UAT.

**MZ2_RELEASE_READINESS=NO; PRODUCTION_WRITES=0; STOP AT NEW C.**
Write-control UNCHANGED. Rebase/Merge/Deploy/Opening Post/Activation=NO.
This continuation changes documentation only; no code, tests, fixtures, financial
source, CI workflows, release intent, database, deployment or guard is changed.
