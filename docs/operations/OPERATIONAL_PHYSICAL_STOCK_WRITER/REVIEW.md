# MZ2_OPERATIONAL_PHYSICAL_STOCK_WRITER_FINAL

**BLOCKED before writer implementation. Not FINAL PASS.**

## What was done

- Created separate Draft PR #1317, based on operational branch commit
  `b93ec3e53d883a69b18941d53dc507656f6158dd`. This is a stacked review so its diff
  contains only this safety-gate work, not all of #1271. #1271 itself is unchanged.
- Created a dedicated loopback Mongo replica set `operationalphysical` on27462,
  using the already installed Mongo8.0.12 executable. No shared server stopped.
- Added ten executable diagnostics against temporary databases on that replica
  set, with real transactions and existing application readers/boundaries.
- No physical receipt endpoint, source writer or financial code was changed.

The intended sole quantity authority remains `warehouse_locations.occupancy.items`.
Receipts/events must be evidence in existing collections, not an independent
quantity ledger. No historical-invoice-to-G47 coordinator is being implemented.

## Fresh replica-set results

Command: `python -m pytest --noconftest backend/tests/test_operational_physical_stock_prerequisites.py -q`.
Runtime uses the existing tzdata directory via PYTHONTZPATH. Final: **10 PASS,
0 FAIL, 0 SKIP in3.56s**. These are diagnostic results, NOT writer acceptance.

| Test | Observed result | Meaning |
|---|---|---|
| Product damaged/quarantine/pending_inspection, quantity10 each | Existing reader reports remaining10 in all3 cases | Eligibility requirement FAIL |
| Component same3 conditions, quantity10 each | Existing reader reports available10 in all3 cases | Eligibility requirement FAIL |
| Mid-transaction exception after two allowed event inserts | Both events and owner revision roll back | Existing transaction foundation PASS; not receipt/quantity writer proof |
| Attempt new operational receipt source through fulfillment profile | Rejected; prior event rolled back | Existing source allowlist preserved |
| Attempt authoritative cost write through operational boundary | Rejected; prior event rolled back | Financial-write boundary PASS |
| Financial transaction while explicit writes_paused=true | 423; callback not entered | Existing paused financial guard PASS |

Test databases are named `operational_physical_test_<uuid>` and removed on teardown.
Every test asserts no general_ledger entries and no mz2_inventory_cost_states.
No network request to Production, no accounting post and no approved-cost change.

Initial test collection failed because PYTHONTZPATH was omitted; rerun supplied
the existing approved local tzdata. The first rollback fixture attempted a receipt
type not permitted by the fulfillment profile and was rejected earlier than the
injected failure. The fixture was corrected to two permitted event inserts; the
receipt rejection was retained as a separate explicit test. No product code was
changed to make tests pass.

## Exact stopping dependency

The user required stopping if safe stock eligibility needs changes to prohibited
fulfillment/preparation/order paths. That condition is now reproduced:

1. `fulfillment_v2_routes.py:158` `_inventory_rows` admits every positive product
   quantity in a non-disabled location, without checking item condition or receipt
   confirmation. `_apply_inventory_reservations` consumes these rows. The stock
   deduction path around392-466 checks identity/quantity but not current eligibility.
2. `stock_component_consumption_service.py:309` `_available` subtracts reservations
   from typed component quantities without checking condition/confirmation.
   Reservation at402 and consumption at525 rely on this contract.
3. `salla_inventory_sync_routes.py` reads the same product stock projection for
   availability. It must be included in regression verification; no Salla code was
   changed or external synchronization called.
4. `warehouse_location_v2_routes.py:255` existing scan placement combines by product
   identity and updates occupancy outside this proposed source/lot contract. Shared
   stock writers must not be assumed to preserve a new lot lifecycle without review.

**Minimum next authorized patch:** a shared inventory eligibility rule applied at
product/component availability, reservation and consumption revalidation (including
CAS/current-state checks). New operational lots must fail closed unless confirmed,
accepted and usable. Quarantine/damaged/unconfirmed lots cannot supply quantity.
Existing historical lots need an explicit compatibility rule, not silent relabeling.
This requires narrowly scoped edits in the two fulfillment/consumption modules
above; that permission is currently withheld by the user. This is an inventory
consumer integration, not a request to build accounting.

Separately, within the authorized inventory scope, the eventual writer requires a
new restricted `operational_atomic` profile: current `_document` at299 only permits
`source_type=stock_preparation_order` for receipt inserts. Do NOT impersonate that
source or widen the existing fulfillment profile. An inventory-specific profile
must validate receipt/event/occupancy mutations and reject financial/control writes.

The existing owner lock and writes_paused guard are a promising mutual-exclusion
foundation. They are not yet proof that a new writer is safe. The writer must check
explicit financial-disabled controls and cutover state inside the same transaction,
fail on unknown controls, and recheck after retry. Concurrent activation races have
NOT been tested for a writer that does not exist. No writer was partially exposed.

## Future accounting opening: final snapshot, not replay

`opening_inventory_service.compile_plan` at205-226 can adopt existing items only
when quantity/identity/configuration exactly match the reviewed opening evidence.
It preserves receipt/lot history. Approval at323 uses adopted_items instead of
adding another batch; this mechanism can avoid duplication in its supported case.

It is NOT universal migration readiness:

- `ready` at146-155 rejects existing operational ledger activity, cost states,
  component consumption plans/units/lifecycle and specified receipt sources.
- `compile_plan` at217-221 rejects stock-preparation/manufacturing provenance.
- Cutover/opening identity and positive reviewed inventory opening are required.
- A new source schema not explicitly rejected is not permission to bypass activity
  checks. Consumption, returns, reservations, final snapshot reconciliation and
  frozen cutoff need a separately approved transition contract.

Therefore final snapshot adoption remains a future bounded accounting dependency.
No change to current cutoff, G47, post_journal_v2, or authoritative costs was made.
No per-invoice historical posting coordinator is needed or proposed for this phase.

## Requested acceptance remains incomplete

| Operation | Current result |
|---|---|
| Safe physical receipt raw10 / ready-Abeer100 / components | BLOCKED before new writer |
| Supplier physical return2/1 | BLOCKED |
| Raw-to-ready atomic conversion | BLOCKED; no preparation/order workaround |
| Customer return quarantine/inspection | BLOCKED on consumer eligibility |
| Reservation/release/consumption for new lots | BLOCKED on same dependency |
| Expected8gold +99silver +3old =110 | NOT EXECUTED / NOT ACHIEVED |
| Operational purchase invoice support | Existing functionality; not counted as physical acceptance |
| Actual Web/Android UAT | NOT RUN for nonexistent writer; existing8135 fixture untouched |

The prerequisite fixture quantity10 is a diagnostic seed, not a receipt operation.
No before/after acceptance table is fabricated from it. Mongo replica-set readiness
does not convert this task to PASS. The earlier operational APK1.0.11/code14 remains
unchanged, distinct from the other application's Build44.

## Files and boundaries

Only `backend/tests/test_operational_physical_stock_prerequisites.py`, this report
and this task's STATUS.md are changed. Final commit/TREE are recorded in Issue1006
and the user-facing report. No product, accounting, fulfillment, preparation,
orders, Salla, release intent or dependency changes. No Merge/Deploy/Prepare/
Prepublish. Production business writes =0.
