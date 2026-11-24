# INVENTORY-CABINET-ARCHIVE-20261124

## Current state

**FROZEN A/B — source and immutable release-packet review passed; PR #2120 is Ready; one final CI job is still running.** No live cabinet has been archived, deleted, disabled or restored by this follow-up. Production has not changed for this task.

Repository: `AMASI-SA/AMASI-SA`.
Task branch: `codex/inventory-cabinet-archive-20261124`.
Verified starting production commit: `c6d05c7814e27e2f22a8c82b5bfe4cf177c17fbe` (PR2118), a descendant of the completed numbering release `cb98e5660380d362e78f937d60abc7139cd1c462` (PR2113).

The latest production change touches operational cost/report code and its release intent; warehouse source files remain unchanged from the completed numbering release. The old release identities and evidence branch remain frozen and must not be reused for this new feature.

## User-directed outcome

The owner asked for short cabinet/column/row labels, chose one row, and said they would create the replacement cabinet before removal of the old cabinet if possible, or another suitable solution. The numbering-only change is already complete. The owner now reports that the new cabinet exists.

During this follow-up the owner asked about resetting the whole hierarchy, then clarified that it is linked to employee permissions and probably services/components. This is not authorization to reset those linked records. Preserve the warehouse and sections. The archive solution targets only the old cabinet, and must reject any relevant live cabinet/location references rather than remapping them without a specific decision.

Fresh live UI observation at `https://mezansalla.com/components-v2?workspace=warehouse` confirms:

- New cabinet **د2**, **دولاب اماسي الافتراضي**, permanent storage, **one row and six columns**; labels **211, 221, 231, 241, 251, 261**, all six reported empty after the occupancy read completed.
- Old cabinet **د1**, **دولاب المراجعه**, three by three; legacy labels **001–009**, all nine reported empty in the UI.
- Neither cabinet's real identifier, full barcode, or numbering has been changed by this follow-up.

UI emptiness is not proof that there are no product or active workflow references. The new server-side action must check its own fresh complete data before any state change.

A read-only check of the existing editor host's configured **local** database found no matching warehouse. It is not the production warehouse evidence and cannot establish absence of live references. No database records were changed and no further database discovery is needed for this task; use the new server-side validation on the actual owner-scoped request.

## Verified gap and chosen solution

Read-only inspection found no supported individual cabinet delete, archive, restore or metadata-update operation. The existing `warehouse-locations-v2/reset` deletes the owner's entire storage hierarchy and counters, including the new cabinet; it is outside this task and must never be used here. The existing single-location disable operation has no restoration UI and leaves the cabinet visible.

Implement **reversible cabinet archive and restore**. Archive keeps records and historical references, hides the cabinet from active choices, and disables its empty locations with restoration metadata. Restore retains the same identities and labels, and returns each location to its exact pre-archive empty/disabled state. No hard deletion, counter reset, number reuse, stock quantity change, financial write or automatic migration is in scope.

## Change contract

- Manager-scoped `POST /warehouse-locations-v2/cabinets/{cabinet_id}/archive` and `/restore`, without a request body.
- Manager-scoped `GET /warehouse-locations-v2/warehouses/{warehouse_id}/archived-cabinets`, returning `items` and `total`.
- Complete slot-set, tenant, permission, occupancy, active-reference and parent-state validation. Unknown, inconsistent or busy state fails closed.
- Cabinet, child-state metadata and audit event changes are atomic, using a restricted existing operational transaction capability. No financial/control writes are introduced.
- Fix directly relevant stale-write seams so an old screen or concurrent placement cannot refill archived slots. Retain diagnostic/historical reads, while active selectors omit archived locations.
- Manager UI provides exact-target confirmation, archive errors, an accessible archived-cabinets list and restoration. The new cabinet remains untouched.

## Ownership and verification plan

Root coordinates integration, docs, GitHub and any separately governed release. `numbering_candidate_build` owns backend code and focused tests. `cabinet_archive_frontend` owns the warehouse page, its styles and focused tests. `numbering_release_review` independently reviews referential integrity, persistence and concurrency. All work uses the same task branch; no agent may modify another writer's files or operate production.

Required evidence: focused public-behavior tests; real Mongo transaction, rollback and concurrency checks; frontend cancellation, errors, exact target and restore tests; combined diff review; applicable CI/build/release gates; live verification of only the intended old cabinet after publication. Preserve all unrelated source work and production release leases.

## Verified implementation

The warehouse page now provides an exact-target archive confirmation, archived-cabinet reader, restoration, server rejection messages, duplicate-submit prevention, stale-read protection and saved-write/failed-refresh handling. The list remains reachable after archiving the last active cabinet. Delegated access cannot expose manager lifecycle controls.

Changed frontend paths: `frontend/src/pages/WarehouseHierarchyWorkspace.jsx`, `frontend/src/pages/warehouseHierarchy.css`, and new `frontend/src/pages/WarehouseHierarchyWorkspace.archive.test.jsx`.

The frontend implementer ran both warehouse workspace suites with the existing installed dependencies: **26/26 tests passed, two suites, exit 0**, including the unchanged 14 numbering/creation/print tests. The initial regression failed because the archive control was absent. Root then ran both suites through the original `frontend_release_toolchain.py exec` on the required Node 22.23.2/Yarn 1.22.22: **26/26 passed, two suites, exit 0, 5.699 seconds**. `git diff --check` passed. The three frontend paths were durably checkpointed at `ea37eab77b7054ce98438b002958d9e94c87a6ec`.

Backend implementation and focused tests are complete. `warehouse_cabinet_lifecycle.py` adds the three routes and restricted `warehouse_lifecycle` transaction profile. Existing warehouse readers and directly relevant bundle, receipt, opening, stocktake and component paths exclude archived slots. Stale placement uses state/occupancy compare-and-set and checks the matched count. Current bundle pins are validated on save inside the shared owner transaction; frozen history remains readable.

The backend author ran the two new archive test files on an exclusively owned MongoDB 7.0.14 replica: **73 passed in 15.60 seconds, exit 0, no skips**. The raw log is retained as `backend-focused.log` beside this document. Its SHA-256 is `f69a5756be355512dcdcc39d3570254f17e781a24e90c46e44b16aa4af02812f`.

The real transaction tests cover archive/restore with preserved parents, employee assignments, new cabinet, identities and history; active references; scope and manager boundaries; complete slot sets; rollback of both operations on audit failure; repeated concurrent requests; both orders of the placement/archive race; refused receipt initialization; current bundle pins and readable frozen history; and malformed markers. Legacy `current_quantity`, conflicting section/room IDs and non-text or blank parent aliases fail closed before writing.

Independent review of the final backend, frontend and new `.github/workflows/warehouse-cabinet-archive.yml` reports **PASS, no remaining source blockers**. The reviewer read the raw 73-test log and matched these source SHA-256 values:

| Path | SHA-256 |
| --- | --- |
| `backend/warehouse_cabinet_lifecycle.py` | `1bc7d2d75a0b2b5cf1275506be5c1817e45269924c4d03770b606e8213c39d9c` |
| `backend/tests/test_warehouse_cabinet_archive.py` | `f39ccc99e251b6c1c68802c29bba0e056ca0681267af23eb495a07d2cce869d8` |
| `backend/tests/test_warehouse_cabinet_archive_mongo.py` | `2ea8b852c32b2bba0bc59e35b128dee96723c56b19c71007d5b4dfc2d1a92268` |

The new workflow checks the actual PR head, uses a checksum-pinned owned Mongo binary with no external URI fallback, and rejects skipped/failing archive tests. It also runs the two frontend suites on the required Node version. Existing gates are unchanged. All source writers have stopped. The original governed build and intent verification now pass. Final-head CI has no failures; its current state is recorded in checkpoint E2 below. There has been no lease, publish or live mutation for this follow-up.

## Next safe action

Finish the remaining applicable final-head CI job and independently review the new guarded operator, then merge normally with expected B and verify tree-equivalent D. Complete the original release protocol from the reviewed immutable packet. Do not archive live data until publication and three-probe identity verification succeed. Then use the manager UI to archive only old d1 and verify the replacement d2 and warehouse/section structure. If a live reference blocks d1, preserve it and report the concrete blocker. Source review is not evidence of publication or absence of live links.

## Release checkpoint E1 — 2026-11-24 21:01 UTC

Reviewed source A: `b236f51bb6c52e6f5d14a136db92f7474e4f5873`; tree `0f86e247f16bdf6aef45561fb6c92be3d88d37b4`; prior governed production J: `c6d05c7814e27e2f22a8c82b5bfe4cf177c17fbe`. Remote task ref and local clean HEAD agree. The equivalent local checkpoint remains preserved on `codex/inventory-cabinet-archive-local-checkpoint-20261124`.

Root ran the archive and numbering Mongo tests, short-code and warehouse generation tests, existing bundle catalog tests, and product receipt tests on this exact clean A: **189 passed in 18.82 seconds, exit 0**. The JUnit XML confirms 189 cases with zero skips/errors/failures. Logs, XML, exact commands and the 27-path source snapshot are retained beside this document. The original `/app` release guard `status` command completed and displayed `active:false` before release bootstrap. No lease was created and no `/app` source or production data was changed by this task.

Next: original governed A/B artifact build, freeze an external intent against J/A, create intent-only B, final-head CI and review, normal merge, clean adapter rehearsal, then prepare/prepublish only for the authorized publish. Keep all later status updates on this separate evidence branch; source A is frozen and the deployment task branch may change only its release intent after A.

## Release checkpoint E2 — 2026-11-24

Intent-only B: `bdebc069ea5fa980cd16725e422ca70e79dadd17`; tree `74946da23e059283f49de2b04baf9860c8ee972d`. Remote and local A..B comparison contains exactly `release/release-intent-v5.json`. B has A as its sole parent. PR [#2120](https://github.com/AMASI-SA/AMASI-SA/pull/2120) is Ready. Production branch still points to J at the latest read; no D has been created.

The original governed build completed two identical clean passes, exit 0. Original A verification, external intent freeze, candidate-intent validation, and original B verification with `--reviewed-intent-v5` all passed. Runtime identity is `rg5-6cfb0e514bb3c65c229a9d224c329f24136bd0ce483bed1af3b74b05e848ca42`. Intent SHA-256 is `f27abedb9ff7ecf9cc8a3c92ec127628a6091842db522e81e1f79b40cdd2a57e`. The original build and B-verification logs are retained beside this document; exact artifact/meta/proof identities and all log hashes are recorded in `release-validation.json`.

Independent immutable-packet review passed: all 3,137 governed source records and 382 public artifact files plus metadata/proof match, critical backend hashes are unchanged, runtime identity was independently rederived, and B changes only intent. Original CI adapter rehearsals on host Node 20 and 24 both passed (jobs 138780195796 and 138780195566, run 45487964006), including clean deterministic builds and isolated package proofs. The new archive backend and frontend jobs passed. Connected-source, stock, accounting and Mongo gates passed; the latest root read has 53 successes, 10 conditional skips, and only Operational Balance Backend still running. Do not treat this checkpoint as all-green or production evidence.

Fresh read-only host inspection showed tracked-clean J, /app available space 422,457,344 bytes, and /tmp available space 236,544,008,192 bytes. Use an independently reviewed operator with owned staging under /tmp and measured space checks; preserve all foreign files/worktrees. The operator is still being prepared. Original guard status must be read again before host mutation/rehearsal. No /app source, service, database, lease, or live warehouse mutation has been made by this task.

Next: remaining CI, reviewed operator, normal merge and D/tree verification, clean original adapter rehearsal, original prepare/prepublish for authorized publication, explicit new Deployment Succeeded, original three-probe verification and closed lease, then archive only d1 through the manager UI if the live reference guards allow it.
