# Cabinet replacement review — 24 November 2026

## Scope and result

The owner requests replacement of current unused cabinets with short cabinet and location names. The user-facing warehouse view showed one cabinet, three shelves, three compartments per shelf, nine readable locations, and all nine marked empty. No stock, warehouse, cabinet or location write was performed.

Source reviewed: `ae870c9c9a143ffe497a68d39b3be3495d6f5f85`, whose application tree is production baseline `80341983cf7e89145e4101b408ab68bb45f379cf`. Root and an independent reviewer inspected the deletion, creation, labeling and stock-reference paths.

## Existing operations

| Operation | Source evidence | Consequence |
| --- | --- | --- |
| Create a cabinet in an existing section | `backend/warehouse_room_routes.py::create_section_cabinet` | Name is optional; dimensions are row/column only; numbering is inherited from the warehouse. |
| Create a cabinet directly in a warehouse | `backend/warehouse_location_v2_routes.py::create_cabinet` | Same numbering inheritance; a short cabinet name does not change location codes. |
| Disable a location | `backend/warehouse_location_routes.py::disable_location` | Checks current state and occupancy; no historical-reference checks or corresponding restore endpoint were established. |
| Delete or rename one cabinet | No route in the warehouse modules and no control in the four inspected warehouse screens | A bounded replacement needs additional implementation or an explicitly scoped data operation. |
| Reset workspace | `backend/warehouse_reset_routes.py::reset_warehouse_data` | Hard-deletes all owner locations, cabinets, sections, warehouses, warehouse events and counters. No occupancy/reference preflight, transaction or restore is present in that function. This is outside the requested cabinet-only scope. |

Frontend reset calls `/warehouse-locations-v2/reset` with only the confirmation word. It does not transmit a cabinet or section identifier. The routed inventory workspace hides that global reset when using delegated page access.

## References requiring verification before replacement

All counts must be scoped to the authenticated merchant and the exact selected cabinet/location IDs. Released or completed history must not be excluded simply because the current occupancy is empty.

| Collection | Confirmed reference paths |
| --- | --- |
| `warehouse_locations` | `id`, `cabinet_id`, `state`, `occupancy` |
| `mezan_inventory_receipts_v2` | `location_id` |
| `mezan_inventory_reservations_v2` | `allocations.location_id`, `allocations.inventory_row_key` |
| `mezan_component_consumption_units_v1` | `allocations.location_id`, linked receipt/lot identities |
| `mz2_purchase_receiving_operations` | `request.receipts.location_id` |
| `purchase_invoices` | Linked `approval_operation_id` and `receipt_ids` |
| `mz2_inventory_stocktakes` | `scope.location_ids`, `lines.location_id`, `posted_differences.location_id`, `audit.snapshot.location_id` |
| `mz2_operational_inventory_openings_v1` | `result.lines.location_id` |
| `mz2_standalone_inventory_entries_v1` | `result.lines.location_id` |
| `mz2_inventory_purchase_receipts_v1` | `result.lines.location_id`, location references in frozen invoice lines |
| `mz2_opening_inventory_imports` | Allocation location IDs in `document`/`plan`; historical `baseline_locations` |
| `mz2_opening_inventory_initializations` | Linked receipt and import identities |
| `mz2_inventory_cost_states` | Linked opening-import identities |
| `warehouse_location_events` | `cabinet_id`, `location_id`, linked `receipt_id` |
| `operational_balance_states_v1` | Merchant-scoped purchase lines plus nested audit/request/engine snapshots |
| `operational_balance_audit_pages_v1` | Merchant-scoped, validated and decoded historical events |

This is a source-backed reference map, not a claim that any of these records exist for the target. Creation-only events and general baseline snapshots must be distinguished from stock transactions. Compressed archive contents cannot be cleared by a plain-text search. Missing production access is not evidence of zero references.

## Naming contract

The existing generator creates one location per row/column intersection. The independent row-major sequential number is not an additional physical coordinate. Existing opt-in `cabinet-row-slot` labels have three parts and preserve scanner identifiers. The owner's earlier example has four parts in cabinet/column/row/bin order, so its final physical meaning remains to be settled.

Both cabinet creation request models reject extra fields and do not accept a numbering override. Deleting and recreating under a legacy warehouse therefore recreates the long labels. Counters also advance independently of document deletion; deleting a cabinet does not automatically make the next cabinet number 1.

Changing only `display_code` could preserve identities, but would be a different operation from the owner's requested replacement and would not cover every existing print/receiving consumer. No such substitution was made.

## Fresh execution evidence and limits

- Remote task branch and production reference matched the recorded source boundary; the local tracked checkout was clean before documentation updates.
- Live authenticated UI: one cabinet, 3 by 3, nine empty/readable locations. No form was submitted.
- Original Release Guard `status` on the authorized host returned `{"active": false}`. No lease, prepare, prepublish or publication was initiated.
- The host's default Python lacked `dotenv`; this attempt stopped before database access. The existing `/root/.venv/bin/python` was then used without installing packages.
- The bounded target query failed closed when no unique target was found. A subsequent read-only count reported a local database host and zero exact/name/code matches. No connection value, credential, merchant ID or private row content was printed or saved here.
- That host database was not treated as production. No production reference-count result or safe-to-delete verdict is claimed.
- No application code changed and no tests were run for this read-only review. No stock or financial operation, deletion, reset, deployment or backfill occurred.

## Next safe action

Resolve the fourth-coordinate/layout contract, prepare a narrow cabinet replacement with exact target preview and association checks, then verify those checks in the authenticated production scope. Preserve the warehouse and both sections, existing dimensions unless changed by the owner, historical records, and unrelated balance-recovery work. Do not use the global reset or mutate the host-local database to imitate production success.
