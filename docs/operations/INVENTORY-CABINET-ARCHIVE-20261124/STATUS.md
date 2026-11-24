# INVENTORY-CABINET-ARCHIVE-20261124

## Current state

**IMPLEMENTING — reversible retirement of an empty cabinet.** No live cabinet has been archived, deleted, disabled or restored by this follow-up. Production has not changed for this task.

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

## Next safe action

Complete the bounded implementation and its focused tests, then inspect the integrated diff. Do not archive live data until the new operation has passed review and the repository's complete production release protocol. A remote checkpoint of this diagnosis is not release readiness.
