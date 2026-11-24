# INVENTORY-LOCATION-CODES-20261124

## Status and controlling owner decision

**Numbering-only implementation verified locally; production changed: no.**
The owner settled the order as **cabinet → column → row**, then explicitly said they will create the cabinet themselves and asked the assistant to change numbering only. Live cabinet creation and possible removal of the old cabinet are deferred by the owner. Do not resume the earlier replacement plan.

No live cabinet, warehouse, location, inventory quantity, historical record, barcode or financial record was created, changed or deleted. No publication, Release Guard lease, prepare or prepublish was performed for this feature.

## Source boundary

- Repository: `AMASI-SA/AMASI-SA`.
- Production baseline: `80341983cf7e89145e4101b408ab68bb45f379cf`.
- Baseline tree: `51e06077c65d99db02088ab441e1908118feb1d1`.
- Task branch: `codex/inventory-location-short-codes-20261124`.
- Prior read-only checkpoint: `b3111de5716709c354521580bfdda4cfe4d06099`.
- Verified implementation source: `e34016484da1ee43409a1e61b632a30b72f81c0e`.
- Draft PR: [#2106](https://github.com/AMASI-SA/AMASI-SA/pull/2106), open against `hotfix/prod-snap-meta-final`.
- The final documentation checkpoint is recorded in this task's bounded Issue #1006 section. It changes documentation only after the verified implementation source.

Production still matched the baseline on the fresh pre-checkpoint read. Prior PR2097 is complete and unrelated. Concurrent balance-recovery work, other workspaces and leases remain outside this task.

## Implemented behavior

| Coordinates | Display label |
| --- | --- |
| Cabinet 1, column 1, row 1 | `111` |
| Cabinet 1, column 2, row 1 | `121` |
| Cabinet 1, column 2, row 3 | `123` |
| Cabinet 2, column 3, row 1 | `231` |
| Cabinet 1, column 12, row 3 | `1-12-3` |
| Cabinet 11, column 2, row 3 | `11-2-3` |

- New policy: `cabinet-column-row`. Concatenate three single-digit coordinates; separate all three with hyphens if any coordinate is 10 or greater.
- The routed UI explicitly requests this policy for new warehouses and cabinets, including cabinets inside an existing legacy warehouse. The warehouse record is not rewritten.
- Dimensions remain the owner's choice. Original defaults of four rows and six columns remain. The earlier mention of one row is not forced on future creations; the form now names rows and columns clearly.
- All new direct and section cabinets allocate from one owner/warehouse counter. Atomic `$max` then `$inc` reserve above both valid existing cabinet numbers across all sections/statuses and the existing shared counter. Old section counters and records are not reset or rewritten.
- With old cabinet 1 retained, a next cabinet numbered 2 has `211`, `221`, `231` for one row and three columns. Do not promise restarting at 1.
- A named partial unique index protects the new policy without imposing a new constraint on legacy duplicate section numbers or dropping an installed global unique/sparse index.
- Missing old cabinet numbers are not invented from codes. Present malformed numbers/counters return a conflict instead of silent repair. Seeding fails closed above the explicit 20,000-numbered-cabinet read bound.
- Existing `legacy` and `cabinet-row-slot` display semantics remain compatible. There is no migration or read-time relabeling of old locations.
- Short labels reach map, list, search, details, printing and relevant location selectors. Purchase options include warehouse context; opening-catalog provenance remains unchanged.
- Location IDs, `code`, `barcode_value`, `qr_value`, scan validation and frozen document labels retain their meanings. Printing prefers the actual barcode, then actual code, then the established full-code fallback for delegates without occupancy access. Short labels never become scanner identities.

## Changed surfaces

- Backend: `warehouse_location_labels.py`, new `warehouse_cabinet_numbering.py`, V2 direct/section creation routes, and display-field passthrough in `purchase_invoices_routes.py`, `product_inventory_receipt_routes.py`, `accounting_onboarding_domains.py`.
- Frontend: `WarehouseHierarchyWorkspace.jsx`, and location options in `StockPreparationOrders.jsx` and `OpeningInventoryEditor.jsx`.
- Focused tests cover these behaviors, full scanner values, no-write cancellation, actual Mongo indexes and concurrent allocation.
- No deletion/replacement API or UI, global reset modification, stocktake, reservation or financial write change.

## Verification

### Backend: 56 passed, exit 0 (4.68 seconds)

```bash
env -u MONGO_URL -u MONGODB_URI PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=backend:backend/tests /workspace/scratch/021675d08c11/location-codes-testenv/python/bin/python -B -m pytest --noconftest -q -p no:cacheprovider backend/tests/test_warehouse_short_codes.py backend/tests/test_warehouse_location_routes.py backend/tests/test_product_inventory_receipt_routes.py backend/tests/test_accounting_onboarding_domains.py
```

Feature tests failed against the old implementation, then passed. Added catalog tests reproduced missing display-code and warehouse-context fields before their fixes. The final selection covers coordinate order, non-square grids, multi-digit labels, legacy compatibility, tenant scope, counter floors, malformed values, stored-record preservation, catalog DTOs and full-barcode scans.

### Real MongoDB 7.0.14: 10 passed, exit 0 (7.50 seconds)

```bash
env -u MONGO_URL -u MONGODB_URI -u DEMAND_READ_TEST_MONGO_URI DEMAND_READ_TEST_MONGOD=/workspace/scratch/021675d08c11/location-codes-testenv/mongo/mongodb-linux-x86_64-ubuntu2204-7.0.14/bin/mongod PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH=backend:backend/tests /workspace/scratch/021675d08c11/location-codes-testenv/python/bin/python -B -m pytest --noconftest -p pytest_asyncio.plugin -q -o cache_dir=/workspace/scratch/021675d08c11/location-codes-testenv/pytest-cache-numbering --basetemp=/workspace/scratch/021675d08c11/location-codes-testenv/numbering-pytest backend/tests/test_warehouse_cabinet_numbering_mongo.py
```

The fixture creates/cleans its owned loopback replica and rejects a supplied database URI. Tests cover 24 simultaneous allocations each with absent, zero, stale and higher counters; all statuses/sections; owner/warehouse isolation; the exact old unique+sparse index alongside the new partial index; legacy duplicate numbers without the old global index; both route index initializers and both HTTP creation paths. Existing rows remained unchanged.

Partial-index creation over pre-existing duplicates is tested on real Mongo because mongomock does not implement that index-creation case faithfully. An extra broader operational metadata test attempt required an absent local Mongo on port 27305 and was stopped; it is not claimed as passed or required for this narrowed scope.

### Frontend: 39 passed in four suites, exit 0

```bash
CI=true XDG_CACHE_HOME=/workspace/scratch/021675d08c11/location-codes-testenv/cache PYTHONDONTWRITEBYTECODE=1 python -B scripts/frontend_release_toolchain.py exec -- yarn --cwd frontend test --watchAll=false --runInBand --runTestsByPath src/pages/WarehouseHierarchyWorkspace.test.jsx src/pages/accounting/onboarding/OpeningInventoryEditor.test.jsx src/pages/InventoryReceivingWorkspace.readPolicy.test.jsx src/services/mezanInventoryReceiving.test.js
```

Governed Node 22.23.2 / Yarn 1.22.22. Tests cover map/list/search/details/printing, actual-code fallback, delegated printing, explicit override inside a legacy warehouse, user-chosen dimensions, unchanged IDs/full scanner values, warehouse context, and no mutation when opening/cancelling creation. Three consumer assertions failed before the corresponding fixes, then passed.

`git diff --check`: clean. Independent source review resolved missing opening-catalog passthrough, purchase warehouse context and stored-code print fallback. No source-review blocker remained.

### Fixed-source Vite build: passed, exit 0

A fresh owned clone checked out `e34016484da1ee43409a1e61b632a30b72f81c0e` detached. Its dependency symlink points only to the owned test environment, whose package/lockfile match the source. The command follows the source-only build precedent in `.github/workflows/mz2-onboarding-integration.yml` and does not invoke the deployment adapter:

```bash
CI=false REACT_APP_BACKEND_URL=https://mezansalla.com XDG_CACHE_HOME=/workspace/scratch/021675d08c11/location-codes-testenv/cache PYTHONDONTWRITEBYTECODE=1 python -B scripts/frontend_release_toolchain.py exec -- yarn --cwd frontend node node_modules/vite/bin/vite.js build
```

Governed Node 22.23.2 / Yarn 1.22.22; Vite 8.2.4 transformed 7,853 modules and completed in 10.73 seconds (18.15 seconds with launcher). Output contained 383 files and `index.html`. The clone's tracked status and diff remained clean. The release-intent SHA256 remained `58a66e394c4dd50a8f9a00affb8f677bfc88910224b5135de330ebf7c7c2f75c` before and after. This proves source compilation, not a Protocol v5 deployment artifact.

Non-blocking build warnings concerned the environment's experimental proxy agent, Vite's future native config loading, an existing font-import position, mixed static/dynamic sidebar import, and chunks over 500 kB. They did not fail the build and were not expanded into this numbering change. The first invocation stopped before Vite due to the launcher's repository-root working directory; the final command above uses `yarn --cwd frontend node` and passed.

### PR and CI state

PR #2106 is draft/open and mergeable without source conflicts; it has not been merged. GitHub CI was still running/queued at the handoff snapshot. On implementation SHA `e34016484da1ee43409a1e61b632a30b72f81c0e`, Page and operational read performance and MZ2 Accounting UI Phase 2 had succeeded; the other 14 workflows were pending/in progress. No complete-CI or deployment-readiness claim is made. Inspect the latest PR-head checks before merge or release; do not substitute these local results for an unresolved required check.

## Deferred deletion and historical research

[REPLACEMENT-REVIEW.md](REPLACEMENT-REVIEW.md) is a historical read-only assessment, not an instruction to execute replacement. The live UI previously showed one 3-by-3 cabinet with nine empty/readable locations. Production historical reference counts were not established. A host-local database without the matching target was rejected as production evidence and not mutated.

If the owner later requests deletion, evaluate the exact cabinet and its references in authenticated production. Current emptiness alone does not prove safe deletion. Do not use the global warehouse reset.

Odoo documents recognizable location names, parent hierarchy and barcodes, not a universal digit format. SAP documents coordinate examples. The owner's final three-coordinate decision supersedes the original four-part example and the earlier fourth-coordinate clarification.

- https://www.odoo.com/documentation/19.0/applications/inventory_and_mrp/inventory/warehouses_storage/inventory_management/use_locations.html
- https://www.odoo.com/documentation/20.0/applications/inventory_and_mrp/inventory/warehouses_storage/inventory_management/use_locations.html
- https://help.sap.com/docs/SAP_SUPPLY_CHAIN_MANAGEMENT/7a6fc3d598d140b0988065937035fff6/55c8cb53ad377114e10000000a174cb4.html?version=7.0.3

## Next safe action

Read the final branch checkpoint from this task's Issue #1006 section, verify the PR head, and inspect the remaining checks on PR #2106. Resolve any failure attributable to this change before proceeding. The verified implementation and source build are remotely recoverable. No further naming/layout clarification is needed.

Publication is a separately authorized Protocol v5 action. No release intent has been created for this feature. Before a later release, refresh refs and original guard status and preserve other conversations' leases/workspaces.
