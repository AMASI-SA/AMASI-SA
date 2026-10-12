# Isolated Production compatibility rehearsal

Date: 2026-10-11. No merge, commit, push, deployment, database access, Salla synchronization, or Production mutation was performed by this review.

## Inputs and boundaries

- Reviewed Production: `b41cd9cc36a738a0528dd89ddc7349b31db2a361`.
- Approved #1317: `365899e6d3c491a65610d91ec815fc1e647085d7`.
- Common ancestor: `a7977f4cfc1ef0a721fc25d783661332f32fe3b6`.
- Branch divergence at review: 85 Production-only / 61 #1317-only commits.
- Isolated temporary branch: `codex/eligibility-production-compat-20261011`.
- Worktree: `D:/codex-worktrees/eligibility-production-compat-20261011`.

The original four-file eligibility patch was applied to an isolated checkout of Production using `git apply --3way`. The current additional read/query changes were then applied incrementally; the projection file was applied as an addition because it does not exist in Production. Original #1317, #1271, Production and the main task checkout were not integrated or merged.

## Result

- Four original eligibility files: clean application, no conflicts.
- Additional fulfillment/catalog/Salla read changes: clean application, no conflicts.
- Projection addition: clean application.
- `git diff --cached --check`: PASS; no unresolved files.
- Original four-file rehearsal index tree: `37724e7213c4c7d3c4852fdc01bec9984ee8cbb9`.
- Updated five-file rehearsal index tree: `93919d526ce4f5947135ca2d68bbf12f4ca21e23`.
- AST parsing: all five files PASS.
- Imports: four original eligibility modules PASS. First historical attempt lacked local timezone data; using the already installed timezone database resolved it without dependency changes.

The projection import on this deliberately narrow Production rehearsal **FAILS** with `ModuleNotFoundError: No module named 'operational_balance_store'`. Production also lacks `operational_balance_routes.py` and `operational_inventory_projection.py`. This confirms an actual stacked dependency: the new PR is an incremental change on #1317/#1271, not a standalone deployable four/five-file patch for current Production. Full approved Operational Balance integration and runtime verification are still required; no missing dependency was copied into the rehearsal to hide this result.

## Production behavior preserved

Only `fulfillment_v2_routes.py` has changes on both sides since the common ancestor, in separate hunks. The rehearsal retains Production's:

- `_require_print_completed_workflow` and `refresh_shipping_label` imports and refresh path;
- explicit local review-stage preservation when refreshed readiness is revoked;
- existing completed-print validation behavior.

No whole Production file was replaced by an old branch version. The other original three eligibility files have no Production divergence since the common ancestor. Clean application proves textual integration only, not complete stacked runtime acceptance.

## Follow-up findings verified

The shared `INVENTORY_LOCATION_ELIGIBILITY_FIELDS` is now included in all four explicit read projections: product decision, product consumption, product catalog, and Salla inventory reader. Location-level condition, purpose, receipt confirmation and valuation evidence therefore reach the same eligibility rule used by the balance projection. Components already read complete location documents.

Pending valuation detection in the projection now inspects location, item and all receipt witnesses independently of whichever eligibility rejection occurs first; another rejection cannot accidentally restore financial-value presentation.

The Operational Balance workflow now uses an isolated Replica Set rather than standalone Mongo. The projection uses a read-only snapshot transaction so occupancy, receipt evidence and reservations describe one consistent snapshot. This requires transaction-capable Mongo topology at runtime. Production topology was not inspected or changed; standalone compatibility is not claimed, and no fallback that could overstate availability is proposed. Final CI results belong in the main acceptance report.

## Remaining release gate

Before any separately authorized integration/release: recheck latest Production, integrate the approved stack without dropping its shipping/review changes, run the complete combined candidate tests, and verify the target Mongo topology supports snapshot transactions. This rehearsal does not authorize any of those actions or the physical receipt writer.
