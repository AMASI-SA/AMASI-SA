# Order299956656 — confirmed Ready diagnosis and proposed guarded recovery

## Observed result

The user explicitly authorized read-only production inspection of this order and future order numbers they provide for the same Ready issue. No production mutation occurred.

Salla reference **299956656** (native ID **658497035**) is **in_progress**, currency SAR. Canonical source snapshot is revision **6**, source clock **2026-11-14T01:25:18Z**, component_pending **false**. The owner/order/native-ID binding was checked before extracting technical fields.

Local workflow is **mezan_local_v1 / reviewed**, revision **3**. Its preparation_progress still records **required2 / allocated1 / remaining1**, updated **2026-11-14T01:18:05.717Z**.

Both required physical units are actually committed in the allocation ledger:
- AMS10253 item144248550, unit1, committed01:16:53.116UTC.
- AMS13282 item876825824, unit1, committed01:24:53.312UTC.
- The other workflow line1516645863 is direct_assembly with supplier_export=false, so it is excluded from the physical allocation requirement.

The second batch **d8f2cd4e96bd44a4930861108b73eb86** explicitly lists this order in **reconciliation_required** after the last allocation. The first batch had the order in remaining_review_order_numbers. This establishes failed post-allocation reconciliation and stale progress; the historical exception detail was not persisted.

## Piece-specific effects

| Piece | Stored custody/service state | Consequence |
|---|---|---|
| AMS13282 | received / received_from_supplier; supplier status received; timestamp2026-11-14T23:42:39.183Z; service completed1/1; no employee-receipt fields or active hold | Meets the existing supplier/service part of optional employee-receipt shortcut, but reviewed-stage eligibility fails before that shortcut |
| AMS10253 | assigned / not_started; no supplier receipt; service pending0/1 | Has independent unfinished prerequisites and must remain blocked even after workflow recovery |
| Three operational rows | Existing operational workflow route | Ready enabled in assembly UI |

Effective settings were inspected only in the operational_flow UI subtree:
- supplier_assignment_mode=optional
- preparation_employee_receipt_mode=optional

AMS13282 has **supplier_assignment_mode=direct_at_partial_receipt** and supplier_assigned_at_receipt=true. Do not call this specifically an invoice-close or cash-payment path; no financial record was read.

The Ready guard first checks the local reviewed-stage restriction. Its physical exception requires actual supplier and employee custody; therefore the absent employee receipt blocks these physical rows before the optional shortcut is considered. This accounts for the two physical blockers and three enabled operational rows.

## Concrete proposed recovery — NOT INVOKED

`GET /api/order-reviews-v1/299956656/preparation-status`

This existing authenticated route invokes `reconcile_direct_preparation_after_review(...allow_dispatch=False)`. Although it is an HTTP GET, it can write local state and is **outside the current read-only grant**.

It rechecks the completed unsuperseded approval, exact routes/quantities/direct identities, source/component authority, committed allocation coverage, provider identity/status, and concurrent source/workflow changes under the owner transaction. If Salla confirms in_progress, it can repair local workflow stage/progress and status-confirmation metadata, and record the transition.

It cannot send a Salla status POST with allow_dispatch=false. It does not mark pieces Ready, complete services, invent employee receipts, change allocations, consume stock, or write a financial document. Batch.reconciliation_required remains historical evidence.

Success requires preparation_status_sync.state=sent and current_stage=in_progress, followed by fresh assembly readback. HTTP200/ok:true alone is insufficient. A pending result must be reported with its current reason and must not be bypassed.

No web UI caller was found in frontend/src. Source authentication supports the existing access_token cookie without an Authorization header, so a direct same-origin navigation can in principle reuse the authenticated browser session without extracting credentials. That live invocation has not been attempted.

## Verification and limits

Root and independent reviewers inspected the route and guarded continuation. Existing tests cover confirmation of already-in-progress Salla status without POST, no repeated uncertain POST, reviewer/tenant boundaries, and the last-allocation transition for mixed orders. A targeted local test run could not start because the current scratch Python has no pytest module. No new test pass is claimed and no source code was changed.

The historical reconciliation failure is proved; its exact exception is not. This is distinct from the previous currency/source-representation diagnosis and from order299781816's completed-status blocker.

## Next action

Obtain explicit authorization for the **guarded local workflow repair for this order**, retaining Salla in_progress and leaving piece/service/receipt/financial state untouched. Then invoke the narrow route once, inspect its exact outcome, and re-read assembly eligibility. Do not issue an unconditional database stage update or replay the broader batch.

Current production base from the latest independent handoff remains236a428364bc8163a684795ed1be87e237476f18; PR1950 publication finished with inactive guard. No release operation is required or authorized by this read-only investigation.
