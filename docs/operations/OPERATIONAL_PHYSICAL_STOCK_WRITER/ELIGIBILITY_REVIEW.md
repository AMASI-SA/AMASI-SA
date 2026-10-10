# MZ2_OPERATIONAL_STOCK_ELIGIBILITY_FIX_FINAL

Status: BLOCKED at the explicitly required third-functional-file review gate.
This is diagnostic evidence, not corrected eligibility acceptance or writer completion.

## Authorization and scope

The narrow approval permits functional edits in fulfillment_v2_routes.py and
stock_component_consumption_service.py only. It requires stopping if an additional
functional file is necessary. Neither approved file has been modified in this
checkpoint. No receipt writer, operational_atomic expansion, accounting, opening,
G47, order transition, preparation or Salla behavior was changed.

## Evidence-loading dependency

`fulfillment_v2_routes._inventory_rows` (line 158 at source HEAD
25445293882d1bbca199930bf93d49c4ee663ad6) is synchronous and receives location
documents only. Current receipt state resides in mezan_inventory_receipts_v2.
The callers in product_inventory_receipt_routes.py:781 and
salla_inventory_sync_routes.py:418 supply location/occupancy projections without
receipt evidence. The fulfillment caller at line 678 is within the approved scope;
the other two callers are not.

The new real-Mongo diagnostic holds the persisted occupancy projection identical
while changing only the matching receipt from posted to pending. Both current
readers still return 10 available. This is a synthetic diagnostic fixture mutation,
not an invocation of an authorized production receipt-state transition.

There is also a concrete existing sequencing concern: stock_preparation_order_routes
creates a pending receipt at line 1401, places inventory at line 1586, and marks the
receipt posted at line 1622. Occupancy source labels alone do not demonstrate that
the final acceptance step completed. No preparation code was executed or changed.

Legacy evidence cannot safely be inferred from missing fields either. The opening
service preserves adopted occupancy items (lines 224 and 323) and records their
old identities in a posted receipt's adopted_receipt_ids (line 319). A valid legacy
lot can require this external adoption evidence. Blanket rejection of missing
evidence would therefore risk hiding valid legacy stock from existing readers.

## Minimum dependency requiring review

Authorize read-only receipt-evidence loading at the two external caller sites:

- product_inventory_receipt_routes.py: the inventory catalogue reader.
- salla_inventory_sync_routes.py: _inventory_facts, before _inventory_rows.

They would pass owner-scoped current receipt/adoption evidence to the shared rule
defined in an already approved file. This would not require running a Salla sync,
changing order selection, writing accounting, or changing business API contracts.
It nevertheless changes functional reader code outside the approved two-file
boundary, so it has NOT been implemented. No cache or permissive fallback was
introduced to bypass this dependency. A broader approved reader architecture could
also resolve it, but is not part of this checkpoint.

## Proposed eligibility contract, not implemented

Require matching owner, location, item/lot and receipt identity with proven accepted
source; reject damaged, quarantined, inspection-pending, unconfirmed and unknown
states. Legacy acceptance requires proven existing receipt or verified opening
adoption, not simply absence of a new status field. Preserve physical and reserved
quantities separately from eligible availability; do not alter approved valuation.
Reservation and final deduction must revalidate using current evidence in the same
transaction with CAS. Full implementation and concurrency validation remain pending.

## Fresh verification

Dedicated isolated replica set: operationalphysical on loopback port 27462.
Only temporary operational_physical_test_<uuid> databases, removed after tests.

Command (with backend and backend/tests on PYTHONPATH and the existing tzdata
zoneinfo directory on PYTHONTZPATH):

```text
python -m pytest --noconftest backend/tests/test_operational_physical_stock_prerequisites.py backend/tests/test_stock_eligibility_receipt_evidence_gap.py -q
```

Result: 12 passed in 4.08s, exit 0. Original 10 diagnostics preserved unchanged;
2 new product/component receipt-evidence diagnostics. Six original cases reproduce
unsafe condition eligibility; four exercise rollback/source/financial boundaries.
The new two cases reproduce missing receipt-state validation. These are not 12
corrected behavior PASS results. There are no after-fix results because no fix was
applied. JUnit: D:/codex-test-data/operational-physical-stock-20261010/evidence-loading-gap.xml.

No claim of safe post-reservation state-change consumption, legacy acceptance,
concurrent deduction, or prevention of every older-writer bypass is made. Those
remain required regression tests after the dependency is approved and implemented.
Web/Android UAT was not run in this diagnostic phase.

## Handoff

Draft PR #1317 remains stacked on codex/operational-app-20261006 at base
b93ec3e53d883a69b18941d53dc507656f6158dd. This checkpoint changes only this report,
STATUS.md and the new diagnostic test. Exact checkpoint HEAD/TREE are recorded in
the PR/Issue #1006 handoff, avoiding a self-referential commit identifier here.

Next: review the two reader-evidence call-site dependencies. Then implement and
verify eligibility before a separately approved physical receipt writer phase.
The 10 gold + 100 silver, returns 2 + 1, final 110 example has NOT been executed or
accepted by these tests. Production unchanged by this task; business writes = 0.
No merge, deploy, sync, prepare, prepublish, shared-server restart or lease action.
