# INVENTORY-LOCATION-CODES-20261124

## Status and controlling owner decision

**Numbering-only implementation verified locally; publication explicitly authorized; production changed by this task: no at this source checkpoint.**
The owner settled the order as **cabinet → column → row**, then explicitly said they will create the cabinet themselves and asked the assistant to change numbering only. Live cabinet creation and possible removal of the old cabinet are deferred by the owner. Do not resume the earlier replacement plan.

No live cabinet, warehouse, location, inventory quantity, historical record, barcode or financial record was created, changed or deleted. No publication, Release Guard lease, prepare or prepublish was performed for this feature.

On 2026-11-24 the owner answered `نعم موافق` to the specific request to publish the numbering change after checks, while postponing creation of new cabinets and removal of the old one. This authorizes the numbering-only Protocol v5 release without another permission request. It does not authorize any live cabinet or inventory mutation.

## Source boundary

- Repository: `AMASI-SA/AMASI-SA`.
- Current release baseline J2: `20fc28f3432a78996f1d70f3356a38bd78b0309e`, tree `5f6ab712785d772fe22461d9d08a4693e992fbcd`, the normal PR2109 merge into `hotfix/prod-snap-meta-final` at 2026-11-24 16:42:58 UTC.
- Current release branch: `codex/inventory-location-short-codes-release2-20261124`, starting directly at J2.
- Previous release baseline J1: `3fd7594af806615d4c53616100dd546ef7b5fe9f` (PR2103).
- Previous reviewed source A1: `e11a29881c7ba1622c17912af6d06d9f6f8acb0a`, tree `ad3c5544b91ae7fcd34f86fa5c8387cc58add075`.
- Previous intent-only B1: `c8766ed16c9806d56dad0fab733537833fa73bb4`, tree `a2dc14faa5d4278c52433b00a197115504073448`, sole parent A1.
- Previous release PR [#2110](https://github.com/AMASI-SA/AMASI-SA/pull/2110) and branch `codex/inventory-location-short-codes-release-20261124` are preserved and superseded by the current refresh. Do not merge or publish their old intent.
- Original implementation baseline: `80341983cf7e89145e4101b408ab68bb45f379cf`, tree `51e06077c65d99db02088ab441e1908118feb1d1`.
- Original task branch `codex/inventory-location-short-codes-20261124`, implementation `e34016484da1ee43409a1e61b632a30b72f81c0e`, documentation checkpoint `f4898d018b93ab71daec0d79f40ea33d561ec40b`, and original closed/unmerged PR [#2106](https://github.com/AMASI-SA/AMASI-SA/pull/2106) remain historical evidence.
- The current source A2, candidate, intent-only B2, replacement PR and later deployment evidence are recorded in this task's bounded Issue #1006 section and the separate `codex/inventory-location-codes-evidence-20261124` branch. Do not append source or documentation changes after freezing A2; later evidence stays on the evidence branch.

Production source advanced from J1 to J2 after all B1 checks completed. J1..J2 changes eight paths: four operational source files, two operational tests, the operational task status and its release intent. None overlaps the 20 numbering-owned paths; Frontend, package/lockfiles, release controls, workflows and AGENTS did not change. This refreshed source carries the same 19 non-status blobs from A1 plus this updated status, directly on J2. The J2 intent remains byte-identical throughout J2..A2. A normal merge of the previous task branch is unsuitable because its earlier intent history would fail the original full-history source check. No old branch is rebased or force-pushed.

At the resumed 16:48 UTC host observation the original guard reported `active:false`, while `/app` remained tracked-clean at J1. GitHub proves PR2109 merged; that alone does not prove it was deployed. Its owner is preparing publication in a separate workspace. Preserve that owner's lease/workspaces and publication. Private source-A bootstrap may proceed without a lease; shared `/app` changes and release rehearsal require a fresh original `active:false` and the expected J2 host baseline. This feature has not changed Production.

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
- Focused tests cover these behaviors, full scanner values, no-write cancellation, actual Mongo indexes and concurrent allocation. A narrowly scoped shipping-identity test fixture resolves the pre-existing suite-order CI blocker; it changes no application behavior or assertions.
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

After carrying the reviewed feature onto updated production J, both selections were rerun from clean checkpoint C in `/workspace/scratch/021675d08c11/location-codes-release/bootstrap`: **56 passed in 4.83 seconds** and **10 real-Mongo tests passed in 7.82 seconds**, both exit 0. The Mongo rerun used task-owned cache/basetemp directories under `location-codes-release`; it retained the same explicit owned `mongod` and rejected external database fallback. The tracked worktree remained clean.

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

### Completed previous release evidence and current refresh

B1's final GitHub result is **47 completed checks: 41 success and six conditional skips, with zero pending, failure or neutral results**. The 18 workflow records comprise 17 successes and one conditional skip. The six skipped checks are branch/manual or unrelated-surface conditions; they do not replace required checks.

- [Source integration](https://github.com/AMASI-SA/AMASI-SA/actions/runs/45434916714/job/138590009956): backend 290 tests plus 237 subtests; Frontend 491 suites / 5,552 tests, no failures or skips; later browser/persistence integration steps also passed.
- [Operational Backend](https://github.com/AMASI-SA/AMASI-SA/actions/runs/45434917023/job/138590217078): 7,454 tests plus 151 subtests, followed by the required 106-test and 16-test / 18-subtest groups.
- [Stock Eligibility](https://github.com/AMASI-SA/AMASI-SA/actions/runs/45434917705/job/138589991564): 6,127 tests plus 97 subtests, with skips prohibited.
- [Host20](https://github.com/AMASI-SA/AMASI-SA/actions/runs/45434917528/job/138591601944) and [Host24](https://github.com/AMASI-SA/AMASI-SA/actions/runs/45434917528/job/138591601868): all 21 steps passed in each clean-clone/no-Git adapter rehearsal, including artifact, proof, runtime identity, isolated package boundaries and JSON metadata.
- B1 candidate RID: `rg5-f46ec42e0b8cb5f3bd757aae88f12a5023f06f3fe17cad80e4c53b042fbcb0ea`; artifact tree `20bb37b904e519478211c634321272a4a293c5e44f3b3e0c77fc90e8cb3c9cc6`. These values are historical and must not be reused for A2/B2.
- Independent review confirmed all final B1 check records, selected raw logs, the zero-overlap J2 delta, and unchanged Frontend/control inputs.

Those results remain evidence for their exact source. The new A2/B2 pair requires its own governed double build, validated candidate, identity and applicable exact-B2 CI; exact-deployment CI is also required before the reviewed host helper. Because the warehouse workflow does not select the new short-code/Mongo test files, rerun the focused 56 plus 10 isolated tests on A2. Do not repeat the unchanged full local Frontend suite without a concrete new risk.

### Historical shipping fixture investigation

The original PR #2106 was not merged. At `f4898d018b93ab71daec0d79f40ea33d561ec40b`, the MZ2 A+B source integration job failed to load `OrderDetailsV2.shippingIdentity.test.jsx`: an installed `react-router-dom` package was mocked as virtual, and suite order could make Jest resolve the real ESM module through `PermissionRoute.jsx`. The run passed 490 other suites and 5,541 assertions, but its later integration steps did not run. That failure is not waived by the local feature results. The replacement release branch must pass its own applicable GitHub checks.

The same ordered `PermissionRoute` then shipping-identity pair reproduced the loading error on both the unchanged production baseline `80341983cf7e89145e4101b408ab68bb45f379cf` and `f4898d`. A full local run in a different order passed 491 suites and 5,552 assertions, confirming why an isolated passing run is insufficient.

The final correction adds 16 fixture lines in `frontend/src/pages/OrderDetailsV2.shippingIdentity.test.jsx`: it preserves the existing page-level virtual router mock and selects the package's actual CommonJS `react-router/dom` entry when a previous suite cached the real router identity. Application routing, `PermissionRoute`, `userHasPermission`, dependencies, the global resolver, all original mocks, setup and assertions remain unchanged. Removing only `virtual:true` was tested and failed two of three orderings; replacing the original mock also broke the reversed layout order. These failed alternatives were not retained.

The final fixture SHA256 is `b1eec8ea9b727e9620b1d07d813472ffa31e873e10551e2d7849dcbf9203b165`. Explicit ordered regressions passed: PermissionRoute → shipping **14 tests**; shipping → layout → PermissionRoute **15 tests**; layout → PermissionRoute → shipping **15 tests**; zero skips in each. The full CI-equivalent command then passed **491/491 suites, 5,552/5,552 tests, zero skips/todos, exit 0, 262.923 seconds**:

```bash
CI=true XDG_CACHE_HOME=/workspace/scratch/021675d08c11/location-codes-testenv/cache PYTHONDONTWRITEBYTECODE=1 python -B scripts/frontend_release_toolchain.py exec -- yarn --cwd frontend test --watchAll=false --runInBand --json --outputFile=/workspace/scratch/021675d08c11/location-codes-testenv/shipping-identity-current-full-after.json
```

The full run used `f4898d` plus this exact fixture. Updated J has no Frontend changes, and C's Frontend tree equals `f4898d`; the final source carries the same tested Frontend bytes. Independent review checked raw JSON/logs, all three orderings, unchanged assertions, and the final hash. `git diff --check` passed. Exact release-head CI remains required before merge; these local results do not claim that skipped post-failure GitHub integration steps already passed.

The initial original `/app` guard was `active:false`, and `/app` was clean on `80341983cf7e89145e4101b408ab68bb45f379cf`; the later updated-J/foreign-lease observation above supersedes that release precondition. The authenticated live warehouse showed the existing 3-by-3 cabinet and its nine legacy locations. These are prepublication observations, not proof that the new feature is deployed.

## Deferred deletion and historical research

[REPLACEMENT-REVIEW.md](REPLACEMENT-REVIEW.md) is a historical read-only assessment, not an instruction to execute replacement. The live UI previously showed one 3-by-3 cabinet with nine empty/readable locations. Production historical reference counts were not established. A host-local database without the matching target was rejected as production evidence and not mutated.

If the owner later requests deletion, evaluate the exact cabinet and its references in authenticated production. Current emptiness alone does not prove safe deletion. Do not use the global warehouse reset.

Odoo documents recognizable location names, parent hierarchy and barcodes, not a universal digit format. SAP documents coordinate examples. The owner's final three-coordinate decision supersedes the original four-part example and the earlier fourth-coordinate clarification.

- https://www.odoo.com/documentation/19.0/applications/inventory_and_mrp/inventory/warehouses_storage/inventory_management/use_locations.html
- https://www.odoo.com/documentation/20.0/applications/inventory_and_mrp/inventory/warehouses_storage/inventory_management/use_locations.html
- https://help.sap.com/docs/SAP_SUPPLY_CHAIN_MANAGEMENT/7a6fc3d598d140b0988065937035fff6/55c8cb53ad377114e10000000a174cb4.html?version=7.0.3

## Next safe action

Read the exact current source/intent/PR from this task's Issue #1006 section and evidence branch. Establish A2 directly on J2 with the 19 reviewed non-status blobs unchanged; verify the owned-path diff, inherited intent and complete ancestry. Run the focused 56 plus 10 isolated tests, build A2 twice using the original governed Node 22.23.2 / Yarn 1.22.22 tooling and reviewed backend URL, validate the external candidate, and create B2 changing only `release/release-intent-v5.json`. All previous candidates are superseded for deployment.

Require all applicable exact-B2 checks, including source integration and both clean-clone host rehearsals. Refresh Production refs before merging normally with B2's tree preserved, then require the applicable exact-deployment checks before the reviewed host rehearsal. If Production advances again, stop and regenerate a source/intent pair on the new base rather than merging unbound source after A.

Publication is explicitly authorized. Refresh the original guard and confirm the shared host is on the expected baseline without a foreign lease. Use only the independently reviewed identity-updated rehearsal helper and its exact seven-field envelope, preserving unrelated files and making verified backups of the four adapter cleanup targets. Only after that matched rehearsal may this task acquire its own lease, prepublish and perform one Republish. Require a newer explicit Deployment Succeeded, three exact-identity live verification probes, and final `active:false`. Verify the new cabinet form read-only and cancel it; live cabinet creation and deletion remain deferred. No further naming, dimensions or publication clarification is needed.
