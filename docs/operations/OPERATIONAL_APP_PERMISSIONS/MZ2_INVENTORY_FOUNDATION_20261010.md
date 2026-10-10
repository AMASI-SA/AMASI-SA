# MZ2 inventory foundation — comparison and bounded implementation

Status: **WIP, not universal-model acceptance and not release approval**.

User specification: `MZ2_PRODUCTS_INVENTORY_UNIVERSAL_MODEL_FULL_COVERAGE`.
This replaces the earlier proposal for a separate operational stock ledger.
No parallel stock ledger is retained in this branch.

## Existing authority and completed boundary

Physical stock is `warehouse_locations.occupancy.items`. Receipts are
`mezan_inventory_receipts_v2`; product reservations are
`mezan_inventory_reservations_v2`; stock events are `warehouse_location_events`.
Valuation authority is `mz2_inventory_cost_states`, not a second on-hand balance.
Products and components must resolve in `mezan_products_v2` and
`mezan_cost_resources_v2`. A component does not need a SKU.

Operational purchase invoice quantities are historical purchases minus supplier
returns. They are **not** proof of warehouse availability. Free personalization
allocations also do not create physical stock or multiply purchased quantities.

Canonical text fields now come from each product's normalized MZ2 options.
Option IDs, names, text/textarea type, required values, lengths and allocation
totals are checked; unsupported or unresolved options fail closed. Historical
invoice readback remains supported. No generated option IDs or raw Salla fallback
is accepted by the new customization path. Conditional option relationships not
preserved by the current normalization are not inferred from labels.

The read-only `/api/operational-balances/inventory` adapter serves the existing
warehouse authority to Web and the standalone operational app. It is a permission
adapter, not a new stock writer or store. Reports permission is required. The
existing inventory receiving endpoint has a different purchase/fulfillment
permission contract and cannot safely grant operational report access as-is.
Unknown costs remain unknown; unresolved availability is not advertised as saleable.

## Existing capability versus missing proof

| Capability | Existing implementation | Remaining gap |
|---|---|---|
| Simple product | Physical rows and receipts | Isolated complete writer/cost proof |
| Multiple colors/sizes | Canonical specifications and configuration keys | Preserve distinct option IDs and conditional groups without name collisions |
| Raw stock | `requires_preparation`, subset matching | Standalone safe receipt/transfer contract; unspecified size means one raw pool |
| Ready customized stock | `ready_complete`, exact matching | Physical creation with all canonical customization fields and cost proof |
| Raw to ready | Pure preview and preparation-specific routes exist | Independent atomic stock operation; do not invoke preparation workflow |
| Return awaiting inspection | Returns/damaged location purposes exist | Existing general row reader does not consistently exclude these from availability |
| Return release | No verified independent operation | Audited, idempotent condition transition preserving identity/value |
| Components | Resource identity, typed stock and consumption rules | Independent isolated transaction proof without order execution |
| Bundles | Not verified | Do not invent bundle semantics; confirm existing contract first |
| Made to order | Stockout/preorder policy exists | Policy is not physical quantity |
| Multiple locations | Existing occupancy and aggregated reads | Atomic location transfer with one correlated event identity |
| Services | Excluded from physical component demand by existing rules | Preserve this exclusion in every stock adapter |
| Reservation | Product reservation records exist | Independent last-unit concurrency and retry/release proof |
| Cost | Existing moving weighted average v1 | Stock writer is coupled to accounting approval; separation decision required |

Evidence references: `backend/product_inventory_rules.py`,
`backend/inventory_receipt_service.py`, `backend/product_inventory_receipt_routes.py`,
`backend/purchase_receiving_service.py`, `backend/stock_component_consumption_service.py`,
`backend/operational_atomic.py`, `frontend/src/services/mezanProductInventory.js`.
The last file provides pure previews, not proof of persistent stock operations.

## Cost/receiving boundary — proposed design, NOT implemented

`purchase_receiving_service.approve_and_receive` currently places physical stock,
updates moving-average valuation, and calls `post_journal_v2` within one approval.
The old standalone purchase-receipt route deliberately rejects writes with
`purchase_full_approval_required`. Do not bypass that rejection or call accounting
approval from the operational app.

Smallest proposed extraction, subject to user approval:

1. Extract the existing stock-only receipt, identity, cost calculation and event
   operations into a shared internal primitive. Keep the current cost policy.
2. Keep the accounting approval entry point and its all-or-nothing journal behavior
   unchanged, calling that primitive within its existing transaction.
3. Give the operational caller a narrow collection/field capability; it must not
   receive the broad fulfillment capability or access accounting collections.
4. Establish one canonical physical source identity per invoice line/receipt.
   Cross-path retries must reconcile the same receipt; a later accounting approval
   must not receive the same physical goods again. Conflicting payloads reject.
5. Do not migrate old operational invoices implicitly. Purchase history without
   verified location/receipt identity remains history, not on-hand stock.
6. Leave additional manufacturing cost unsupported until the existing approved
   policy supplies it. Do not infer a new cost-recognition treatment.

Expected diff surface: existing receiving service extraction + focused shared
stock primitive + narrow operational transaction profile + receiving adapter and
tests. No accounting policy, posting entries, order state, preparation page,
shipping or review-page behavior changes are proposed.

Required evidence before this extraction can be accepted: all existing G47
approval tests unchanged; operational receipt creates no journal; both callers
cannot double-receive one source; conflicting payload rejects; failure between
stock and cost rolls back; actual isolated replica-set concurrency, idempotency
and crash/retry tests. A plain standalone Mongo instance cannot prove transactions.

## Remaining acceptance scenarios

A/B/F/K/L/M can gain read/projection evidence from synthetic warehouse fixtures;
this alone does not prove their complete write lifecycle. C/D/E/G/H/I/J/N require
real isolated write/transaction evidence for the final agreed stock primitives.
None of the A–N end-to-end scenarios is declared fully accepted by this document.

Future preparation may query exact ready stock, compatible raw stock, location,
cost and movement history, then reserve/release via independent stock contracts.
This task does not wire those contracts into customer order execution.

Production writes = 0. No merge, deploy, release intent, prepare, prepublish,
accounting writer, Legacy fallback or real-order deduction is authorized here.
