# MZ2_OPERATIONAL_STOCK_ELIGIBILITY_FIX_FINAL

Scope: receipt-backed stock eligibility, reservation and consumption only.
The four-file owner approval supersedes the earlier evidence-loading blocker.
No physical receipt writer or 110-piece lifecycle is included.

## Functional changes

1. backend/fulfillment_v2_routes.py: shared evidence loader and eligibility rule,
   product availability, reservation revalidation, identity snapshot and consumption
   revalidation. Unscoped helper mutations enter the existing owner transaction.
2. backend/stock_component_consumption_service.py: the same rule for availability,
   reservation location fencing, batched consumption evidence and final deduction.
3. backend/product_inventory_receipt_routes.py: evidence loading at the existing
   catalogue reader only. Receipt operations are unchanged.
4. backend/salla_inventory_sync_routes.py: evidence loading in _inventory_facts
   only. No external sync or quantity update was executed.

No fifth functional file, new dependency, accounting writer, approved cost change,
opening/cutover change, order/Ready/shipping/preparation transition, API endpoint,
or expansion of operational_atomic.

## Eligibility and quantities

warehouse_locations.occupancy.items remains the only physical quantity source.
Receipt quantity is never summed into stock. A matching owner-scoped posted receipt
from the known purchase, stock-preparation or opening source is required. Source,
location/warehouse, item identity and configuration must agree. Explicit bad or
unknown condition/inspection states and unconfirmed/pending receipts fail closed.
Missing or ambiguous evidence has a reconciliation reason; a client flag is not
accepted as provenance.

Physical on_hand remains visible when eligible remaining/available is zero.
Reservation quantities remain separate. No item is removed to hide ineligibility.
Proof queries are batched by at most 100 locations, capped at 20,000 receipt rows
per batch with explicit rejection on overflow, and always include user_id.
The real-Mongo batching case proves 201 locations use three proof queries.

Historical lots with missing new status/source fields are accepted only through a
matching posted receipt or posted g47-opening-inventory-v1 adoption evidence with
opening transaction, evidence hash, cutover and matching item/location identity.
A direct current receipt overrides an older adoption witness; a newly pending
direct receipt cannot be hidden by adoption. Unknown history remains unavailable.
Historical reservations require a stable receipt plus product identity; old
index-only holds without identity evidence require reconciliation.
This is contract/fixture validation, not an audit of Production inventory records.

## Concurrency and retry contract

Receipt changes and mutations serialize through the existing shared owner lock.
Both race orderings are tested: a receipt change that wins causes the consumer to
reject; a consumer that wins commits once while eligible and the later pending
transition leaves the remaining physical units unavailable. No claim is made that
Mongo snapshot reads alone prevent write skew. Direct administrator writes that
bypass the established owner transaction are outside this supported contract;
future receipt writers must use the same boundary.

Location CAS fencing prevents a concurrent physical-location edit from escaping
reservation validation. Consumption validates current evidence, quantity and item
identity in the transaction. A later ineligible lot aborts the whole deduction;
reservation/unit states do not partially advance. Existing unit/consumption keys,
owner serialization and duplicate replay behavior are retained.

## Verification

Final integrated result: **136 PASS, 0 FAIL, 0 SKIP, 1 explicitly deselected;
7 additional subtests PASS**, exit 0, 140.26 seconds. Confirmed for the approved
eligibility/reservation/consumption scope and established owner-transaction contract.

New acceptance suite: 37 real-Mongo cases, including posted-to-pending with 10
physical units preserved; product/component damaged, quarantine and inspection
states; historical receipts/adoption; foreign-owner and missing evidence; state
change after reservation; both receipt/consumer race orderings; competing holds;
duplicate product/component consumption; identity replacement; rollback across
lots; read-only Salla compatibility; batched proof loading; and unchanged seeded
approved-cost/ledger documents.

Additional regression suites cover existing component lifecycle, process death
before commit, committed response loss, cancellation, cutoff, fulfillment
contracts/rules, purchase-reader contracts and Salla inventory rules.
The optional standalone-Mongo rejection case is explicitly deselected because
this verification uses the dedicated replica set; it is not reported as a PASS.

The original 12 diagnostic sources are preserved in diagnostic-baseline/*.py.txt
and in Git at 3c960511857f742943aafbc7be20f3ffbad7d70a. Their original PASS meant the
bug was reproduced, not acceptance. Active regression versions now require the
corrected result. Component integration fixtures formerly had occupancy without
receipt evidence: their initial two failures were corrected by adding matching
posted receipt fixtures, not by relaxing the eligibility rule.

Command (existing Python environment, backend;backend/tests on PYTHONPATH and
existing tzdata zoneinfo on PYTHONTZPATH):

```text
MZ2_TEST_MONGO_URI=mongodb://127.0.0.1:27462/?replicaSet=operationalphysical
python -m pytest --noconftest backend/tests/test_stock_eligibility_acceptance.py backend/tests/test_operational_physical_stock_prerequisites.py backend/tests/test_stock_eligibility_receipt_evidence_gap.py backend/tests/test_stock_component_consumption.py backend/tests/test_fulfillment_v2_contract.py backend/tests/test_product_inventory_receipt_routes.py backend/tests/test_product_fulfillment_rules.py backend/tests/test_salla_inventory_sync_rules.py -k "not standalone_refuses" -q --disable-warnings
```

Evidence: D:/codex-test-data/operational-physical-stock-20261010/eligibility-final.xml.
All test databases are unique local temporary databases and are removed by teardown.
py_compile on the four functional files and git diff --check passed.
These are local isolated checks, not a claim of GitHub CI or Android/Web device UAT.

## Review handoff and remaining work

Draft PR: https://github.com/AMASI-SA/AMASI-SA/pull/1317
Base: b93ec3e53d883a69b18941d53dc507656f6158dd,
codex/operational-app-20261006. Base unchanged; PR remains Draft.
Tested implementation commit: e20d5b60d3b583f0205cddb246b9f19bd7d434b4.
The final documentation-only HEAD/TREE are recorded in the PR and Issue #1006.

Remaining: separately review and authorize physical receipt writer integration,
then implement and test the 10 gold + 100 silver / return 2 + 1 / 110 total cycle.
Unknown historical stock requires reconciliation rather than fabricated approval.
No release readiness or completion of the entire inventory system is claimed.

Production unchanged by this task. Production business writes = 0.
No merge, deploy, prepare, prepublish, Salla sync, shared Preview restart or lease action.
