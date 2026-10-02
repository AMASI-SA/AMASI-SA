# C3 pre-Smoke independent review — 2026-10-02

Read-only working-tree review of store_delivery_cash_evidence.py, store_delivery_delivery_commit.py, operational_atomic.py and driver/H2 UI. No product edits, services, Smoke B, or tests executed.

## Material findings

1. **Native same-day handover rejected by invented timestamp precision.** Native handover source occurred_at is journal group.effective_at (cash_evidence.py167). Existing writer constructs that timestamp from bank movement_date at Riyadh00:00 (accounting_shipping_native.py298–301). save_reconciliation297 compares it to exact physical confirmed_at; any capture later that day is rejected even if handover occurred afterward. Preserve existing day precision for native sources, and exact created_at semantics for operational source. Add same-Riyadh-day and previous-day tests; never manufacture exact handover time.

2. **Driver UI retry generates different evidence identity.** DeliveryPaymentModal always uploads a new delivery-proof reference before POST. If first cash POST commits but response is lost, second submit carries new reference, while cash_delivery_retry37–41 correctly rejects changed proof identity. Preserve exact pending payload/upload references after uncertain outcomes or verify actual server result first. Add lost-response UI test asserting no extra upload, identical POST and successful backend replay. Do not loosen backend identity guard.

## Protections observed

- Prior reversed-source completeness defect fixed: changed_financial_proof_source_ids now prevents coverage.complete, retains historic allocation without inventing cash return.
- Exact immutable collection-key evidence; amount/identity/actor/proof sealed; no overwritten evidence on replay.
- Current capture transaction rechecks fresh actor/profile, active assignment, exact order identity, expected amount and proof. Local insert/assignment/proof/order/workflow/event updates share operational_owner transaction.
- Cash capture profile restricts insert/update collections, exact fields and transition methods; journals, write-control, activation and raw session access are outside capability. Reconciliation profile only permits sealed evidence inserts. Owner revision is serialization metadata, not financial pause mutation.
- Duplicate allocations rejected at DTO and persisted-link validation; source cannot be matched twice; remaining actual amount bounds each explicit allocation; total must equal existing source amount. Owner transaction serializes competing matching calls.
- Owner/cross-driver source and collection selection are explicit. Native source validates sealed existing journal and exact bank/driver legs. Operational COD remittance remains labelled no-native-financial-proof, net/fee/ambiguous rows rejected.
- Cancellation/source changes affect reconciliation eligibility without deleting the captured observation. Reversed source cannot be newly matched. H2 carries stable pending request payload through uncertain matching outcomes; driver switch remounts bykey.
- Existing native observer stays after operational capture and consumes expected COD, not physical cash. Thus active-owner existing recognition may still post under original guards; new evidence/matching itself does not add financial behavior.

## Limits

No fresh Mongo or browser execution by this reviewer. Other agents own public real-Mongo capture/matching/rollback/security tests and browser acceptance. Existing test names cover those protections, but source review alone is not a runtime PASS. Smoke B remains unexecuted.

## Follow-up disposition

Chronology finding withdrawn as an independent C3 regression: the existing native settlement writer325–330 already enforces exact cash delivery_event_at at/before movement-date Riyadh-midnight. Newly captured postmidnight cash cannot legitimately produce that same-day source. Reassigning a source funded by older cash to newer capture would require an explicit precision policy; no chronology product change was made.

Driver retry gap fixed within assigned two frontend files. Pending delivery payload includes original proof/receipt references and exact physical amount; uncertain transport/5xx retain and resend it without new upload. Inputs/close lock while pending, beforeunload warns; definitive4xx clears pending. Fresh23/23 frontend tests PASS (including lost transport response and503); log c3-driver-frontend-retry.log. No backend guard changes.
