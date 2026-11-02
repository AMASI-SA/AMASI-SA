# Supplier invoice consistent-snapshot preview — WIP, 2026-11-02

Application source446404698c14b260900550bac3f63e5dbdcaf5b2, full Backend7dadd09d09caf9f1bde0c39cdcbe187bf82a2064 independently matched to the exact locally tested tree. Base6e7aca914d2cc230b4a95146fb48f46f15c59b3b preserves published dashboardf8235d57. Router source transfer is now applied/read back; temporary patch is removed and the following docs checkpoint removes its workflow. No production merge,/app,lease,financial activation or ledger write.

## Behavior
GET /api/operational-balances/supplier-invoice-review reads saved operational state and native invoice evidence in ONE Mongo snapshot transaction. Existing supplier-only pure reducer/comparator; no refresh/mutate or persisted proposed facts. Snapshot failure returns an error, not a weaker non-snapshot fallback. Authentication requires active actual actor, operational reports access and suppliers.read. Actor/owner/grants/native-accounting activation are rechecked before disclosure.

Each eligible approved preparation invoice shows original gross, saved confirmed/settled/outstanding, proposed values, difference, component coverage, reason and action. Cutoff is the saved instant. Old customer-order creation does not suppress new approved invoices. Openings/movements/returns/provider/payroll facts remain unchanged. Missing source proof blocks completeness.

Native purchase invoices are SEPARATE UNVERIFIED candidates with source-declared amount, liability/journal references and possible exact supplier-document collisions against preparation, operational inventory or exchange purchases. Collision is not matching proof or posting authority. No name/amount/SKU join. Internal Mezan sequence cannot substitute for supplier reference. Native verified payable/delta remain null. This does NOT close native posting/payment/opening matching or pre-invoice receiving.

## Verification
Local exact Backend:1536 passed,0 failures/errors/skips,156.72s.37 new preview cases are included,NOT extra. Focused37 passed7.02s. Real local Mongo8.0.12 replica,UUID synthetic databases,no live credentials. Snapshot consistency while invoice changes, no writes,80gross/40saved/25paid=>40difference and15→55outstanding, reprice conflicts, supplier grants and mid-request revocation/activation were verified. No new UI screen,visual acceptance,live comparison or new remote full-suite success is asserted until read separately.

## Continuation
Canonical STATUS.md preserves exact hashes, previous full handoff, all provider/payroll/customer/loan/shipping/warning/profit gates and release boundary. Next read current source/CI, complete native invoice/journal/liability/payment/opening reconciliation, then actual owner read-only differences and approval. Keep provider_postsettlement_refund_review_required and statement posting_allowed=false. Publishing code never authorizes financial correction; one final operational release only after all gates. Restricted COD archives remain unaccessed.
