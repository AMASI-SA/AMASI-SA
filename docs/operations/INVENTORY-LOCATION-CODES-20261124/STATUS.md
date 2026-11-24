# INVENTORY-LOCATION-CODES-20261124

## Status

**Research and source inspection complete; implementation awaits one physical-layout clarification.**
Production changed: **no**. No source, configuration, stored location, inventory quantity, barcode or deployment changes were made.

The owner requests shorter storage names in inventory selectors. The supplied example is cabinet 1 / column 2 / row 2 / bin 1 -> 1221, replacing a long location label such as RUH-WH001-S01-C01-001. The follow-up asks how Odoo and established warehouse systems number locations and requests a similar approach.

## Verified source boundary

Repository: AMASI-SA/AMASI-SA.
Baseline: 80341983cf7e89145e4101b408ab68bb45f379cf.
Baseline tree: 51e06077c65d99db02088ab441e1908118feb1d1.
Task branch: codex/inventory-location-short-codes-20261124.

The prior inventory-side-options release PR2097 is already complete and remains separate. This task must not repeat that publication or take over the concurrent balance-recovery work.

## Direct code findings

- `backend/warehouse_location_routes.py::generate_location_rows` constructs a two-dimensional `length * width` cabinet grid. Each location has row, column, grid_x, grid_y and a row-major sequential number.
- `backend/warehouse_location_v2_routes.py` supplies cabinet_number and preserves code, barcode_value and QR identity.
- No independent nested-bin coordinate exists inside a row/column intersection in that model.
- `backend/warehouse_location_labels.py::location_label_fields` already provides an opt-in `cabinet-row-slot` display code in cabinet-row-column order, such as 1-2-2. It leaves scanner identities unchanged.
- `backend/operational_purchase_metadata.py::locations` already prefers stored display_code, then name/code. Legacy locations without display_code retain long labels.
- `frontend/src/pages/operational/StandaloneInventoryEntry.jsx` displays the catalog location name plus warehouse context.
- `frontend/src/pages/WarehouseHierarchyWorkspace.jsx` contains short grid labels and print-label behavior for the opt-in scheme.

The existing sequential location number is not a fourth independent physical coordinate. Do not append a fabricated bin number or infer coordinates by blindly truncating the long code.

## Primary-source research

Odoo supports a recognizable location name, a parent location hierarchy, and a location barcode. Its documentation does not prescribe one universal numeric format. Example hierarchy: WH/Stock/Zone A/Refrigerator 1.

- https://www.odoo.com/documentation/19.0/applications/inventory_and_mrp/inventory/warehouses_storage/inventory_management/use_locations.html
- https://www.odoo.com/documentation/20.0/applications/inventory_and_mrp/inventory/warehouses_storage/inventory_management/use_locations.html

SAP EWM documents coordinate-based location names, e.g. 01-02-03 for aisle/stack/level, with letters/numbers aligned to warehouse structure.

- https://help.sap.com/docs/SAP_SUPPLY_CHAIN_MANAGEMENT/7a6fc3d598d140b0988065937035fff6/55c8cb53ad377114e10000000a174cb4.html?version=7.0.3

These are naming/design references, not a claim that Mezan implements Odoo or SAP.

## Proposed behavior, not yet applied

- Human-facing labels use short, separated coordinates scoped by warehouse/section context.
- Preserve actual location IDs, stored historical relationships, barcode/QR values and scan validation.
- For one final compartment per intersection, a cabinet-column-row code is sufficient, e.g. 1-2-2.
- If separate numbered boxes exist inside an intersection, the requested fourth level is meaningful, e.g. 1-2-2-1; implement it explicitly rather than inventing coordinates for old rows.
- Numeric concatenation is ambiguous for multi-digit coordinates: 1/12/2/1 and 11/2/2/1 both become 11221.
- Final display order must follow the owner's decision; the existing opt-in order is cabinet-row-column, which differs from the owner's example order.

## Required clarification and next safe action

Ask whether each cabinet row/column intersection is the final storage compartment, or contains independently numbered bins/boxes. This changes the data model and cannot be decided from a display example alone.

After the answer, implement the smallest consistent change across catalog labels, entry/purchase/receiving selectors, warehouse map and print labels as applicable. Preserve frozen document labels and underlying IDs. Use targeted validation for coordinate correctness, multi-digit values, cross-warehouse duplicates, legacy fallback and unchanged write payload identities. No tests were executed for this research-only milestone, and no deployment claim is made.

Before implementation, refresh the production/task refs and applicable instructions; do not assume another release remains idle.
