# Authorized late delivery evidence — implementation checkpoint

Implementation is in progress on a separate task branch; PR1237 and its
`48bb39d983fe7003bc972c624a1508655a16f570` reference remain unchanged.
Reviewed Production is `83363097d48e034dc7140a60c290efc684e1ffde`.
No re-foundation or Production operation has occurred at this checkpoint.

## Contract and permission

The user approved NEW-C-EVIDENCE-CONTRACT, then explicitly authorized reuse of
`accounting.shipping.contracts.review` for this evidence-only review. Existing
registry, explicit grants, fresh active-user checks and owner resolution are
reused; no role, permission key, implicit grant or posting authority is added.
Queue metadata uses `accounting.shipping.view`; original bytes and review need
the explicit review grant. Existing financial endpoints retain their separate
permissions and 423/503/SSOT/activation/period controls.

## Implemented source changes awaiting final verification

- Driver upload and accountant review routes retain a separate original image
  and append-only attachment/decision events in a narrow operational capability.
  That capability permits inserts into the proof/event collections only, never
  update/delete of original C3, assignments, collections or financial/control
  documents. Caught prohibited writes poison and abort the transaction.
- Every attachment binds exact owner, canonical order, driver, assignment,
  collection, original delivery time, original C3 seal or explicit absence,
  image content hash, uploader, reviewer and separate server audit timestamps.
  Historical actor proof comes from C3 or the existing original delivery workflow
  actor/time; current account linkage alone cannot prove a historical actor.
- The existing Track F writer can consume one approved binding when the original
  proof reference is absent. An invalid original proof cannot use this extension.
  Its financial evidence and journal metadata retain late-source provenance,
  while the effective time remains the original delivery time. No financial
  writer or recognition economics was added.
- Original C3 v1 bytes/reference/seal remain unchanged. Only NEW captures with
  an absent proof use an explicit v2 observation with `absent_at_delivery`.
  There is no migration or historic cash reconstruction. The original missing
  reference is never patched after attachment. Route composition with PR1238's
  optional-proof behavior is still pending the separate new Integration source.
- Identical request IDs/payloads replay the same event; conflicting requests,
  duplicate artifacts, competing approvals and changed sources fail closed.
  A new attachment cannot replace a sealed financial recognition source.
- Driver and accountant panels provide the existing-context upload/review path,
  distinguish original delivery and later attachment time, and never invoke a
  financial API on attachment or review.

## Evidence and remaining order

Read STATUS.json for current executed results. Proof is isolated only; neither
fixture writes nor UI mocks are Production acceptance. Native-client route
allowlisting must be reconciled with PR1238 before full-path acceptance.

Next: finish focused Real Mongo and UI acceptance; close the independent rejected
payment receipt-upload wiring bug; create a new Integration branch over reviewed
Production and compose all prior changes without force-pushing PR1237; then run
Full Regression/Build, SSOT, Security/CodeQL and remaining business acceptance on
that actual source. Do not reuse a stale source/intent pair.

Release Readiness NO. Production financial writes 0. Write-control UNCHANGED.
Merge to Production / Deploy / Opening Post / Activation NO.
