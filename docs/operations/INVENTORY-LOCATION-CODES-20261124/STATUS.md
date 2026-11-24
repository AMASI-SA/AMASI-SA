# INVENTORY-LOCATION-CODES-20261124

## Status and controlling owner decision

**Numbering-only implementation verified locally; publication explicitly authorized; production changed by this task: no at this source checkpoint.**
The owner settled the order as **cabinet → column → row**, then explicitly said they will create the cabinet themselves and asked the assistant to change numbering only. Live cabinet creation and possible removal of the old cabinet are deferred by the owner. Do not resume the earlier replacement plan.

No live cabinet, warehouse, location, inventory quantity, historical record, barcode or financial record was created, changed or deleted. No publication, Release Guard lease, prepare or prepublish was performed for this feature.

On 2026-11-24 the owner answered `نعم موافق` to the specific request to publish the numbering change after checks, while postponing creation of new cabinets and removal of the old one. This authorizes the numbering-only Protocol v5 release without another permission request. It does not authorize any live cabinet or inventory mutation.

## Source boundary

- Repository: `AMASI-SA/AMASI-SA`.
- Original implementation baseline: `80341983cf7e89145e4101b408ab68bb45f379cf`, tree `51e06077c65d99db02088ab441e1908118feb1d1`.
- Updated release baseline J: `3fd7594af806615d4c53616100dd546ef7b5fe9f`, tree `0e139ed1822245fd40f1b4b123cae4b3c490d9f8`, the reviewed PR2103 merge.
- Original task branch: `codex/inventory-location-short-codes-20261124` (preserved).
- Release task branch: `codex/inventory-location-short-codes-release-20261124`, based directly on updated J.
- Prior read-only checkpoint: `b3111de5716709c354521580bfdda4cfe4d06099`.
- Verified implementation source: `e34016484da1ee43409a1e61b632a30b72f81c0e`.
- Original draft PR: [#2106](https://github.com/AMASI-SA/AMASI-SA/pull/2106). The replacement release PR is recorded in Issue #1006 and the original PR; do not merge both.
- Documentation-only checkpoint: `f4898d018b93ab71daec0d79f40ea33d561ec40b`.
- Refreshed-base WIP checkpoint C: `d04b5b3d3a9fef5003a93da8362bd8e749c0d55e`, sole parent J; its 18 non-status feature files are byte-identical to `f4898d`. This final source adds the shipping-test fixture correction and updated verification record after C.
- The final source checkpoint, reviewed intent-only commit and eventual deployment evidence are recorded in this task's bounded Issue #1006 section. Do not append source or documentation changes after freezing the release source.

Production advanced to updated J while the test-fixture failure was being investigated. The new production paths and this task's paths are disjoint, except that the new production release owns its updated intent. A normal merge of the old task branch would itself appear in the original guard's full-history intent-change check. Consequently the release branch starts directly at J and carries the exact reviewed numbering files plus the verified fixture correction and this continuity document. The old branch is preserved without rewriting its history. The new production intent stays byte-identical until the separate intent-only B.

At 15:51 UTC the original shared guard reported an active foreign lease for `chatgpt-operational-recovery-20261124-continuation`, deployment J, source `24603be6a5bc88519f59365d8f3846bd31fc2a55`; Emergent displayed that owner's publication in progress. No shared mutation or release rehearsal for this task may start until the original guard reports inactive again. Prior PR2097 is complete and unrelated; other workspaces and leases remain untouched.

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

### PR and CI state

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

Read the release branch, replacement PR and final source checkpoint from this task's Issue #1006 section. The fixture and combined-base feature checks are verified. After the foreign publication has completed, build fixed source A twice under Node 22.23.2 / Yarn 1.22.22 with the reviewed backend URL, validate the candidate against complete Git ancestry, and create B changing only `release/release-intent-v5.json`. Require all applicable exact-B checks before merge, including source integration and both clean-clone host rehearsals. The earlier `f4898d` candidate is superseded and must not be published.

Publication is now explicitly authorized. Complete the matching clean-clone adapter rehearsals, preserve the reviewed A/B relation through merge, and refresh Production refs and the original guard before touching `/app`. Only after the matched rehearsal may the owner acquire a lease, prepublish and perform one Republish. Require a new explicit Deployment Succeeded, three exact-identity live verification probes, and final `active:false`. Preserve other conversations' leases/workspaces. Verify the new cabinet form read-only and cancel it; live cabinet creation and deletion remain deferred. No further naming/layout or publication clarification is needed.

## Release operations evidence branch (not a deployment branch)

The governed release source is frozen at A `e11a29881c7ba1622c17912af6d06d9f6f8acb0a`. Intent-only B is `c8766ed16c9806d56dad0fab733537833fa73bb4`, tree `a2dc14faa5d4278c52433b00a197115504073448`, in [PR2110](https://github.com/AMASI-SA/AMASI-SA/pull/2110). The original PR2106 is closed without merge.

This evidence branch `codex/inventory-location-codes-evidence-20261124` records operational receipts and the reviewable host helper separately. **Never merge this evidence branch into Production or append its files to frozen A/B.**

The original governed source-A build completed twice with matching artifacts, and the original verifier, external freeze, candidate loader and independent full-source/artifact/proof review passed. Intent: 1,529,501 bytes, SHA256 `460a55ac4f5242bd51321e9df23eba793954260be80b031815f6a42122d152c1`, Git blob `9804039303c159b0750f303ce049650e597eb612`. Runtime release ID: `rg5-f46ec42e0b8cb5f3bd757aae88f12a5023f06f3fe17cad80e4c53b042fbcb0ea`. Exact-B CI is running; no merge, app mutation, own lease or publication yet.

`RELEASE-EVIDENCE.json` retains the commands, exit statuses, source manifests, build/proof identities and source-build log hashes. `operators/pr2110_guarded_rehearsal.py` is staged for independent review, not executed. It is an identity-only adaptation of the existing reviewed PR2092 helper: reviewed base J and PR/task prefixes change; all backup, foreign-file preservation, original operation-lock, exact envelope, FF-only, original adapter and verification logic remain unchanged. It requires an independently reviewed exact deployment envelope and completed exact-D gates. It never prepares a lease, publishes, performs financial actions or automatically rolls back.

Current next action: require exact-B CI and both host rehearsals, review the helper and immutable deployment envelope, confirm the original foreign lease is closed, normal-merge preserving B tree, then run the guarded original app rehearsal. Only a successful rehearsal permits original prepare/prepublish and the already-authorized numbering-only publication. Require platform success plus original three-probe verification and inactive lease before completion.
