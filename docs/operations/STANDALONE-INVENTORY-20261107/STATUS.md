# Independent inventory preparation — WIP

Owner decision (2026-11-07): prepare inventory independently under Products. Stock purchases serve future matching orders; current customer-order preparation purchases remain unchanged. Do not integrate reservation, fulfillment, assignment, consumption or order costing until inventory is complete and separately authorized.

Base: 8947eb0b4ad5b0370efecdaadc044268693122da.
Task branch: codex/standalone-inventory-20261107.

## Implemented slice
- Protected, lazy /inventory-v2 workspace under Products.
- Current stock, owner opening, purchase entry and purchase reports reuse existing APIs and permissions; no copied ledger or inventory state.
- Explicit deferred fulfillment notice. URL tab permissions fail closed. Default entry only mounts the stock reader when authorized.
- Web operational balances links to the standalone page. Existing Android and operational screens remain available during preparation.
- No backend, fulfillment, stock-admission or accounting writer change. Opening approval remains subject to the existing closed writer and valuation requirements.

## Read-only findings and remaining work
Observed live before implementation: physical writer closed; empty unfiltered stock results; purchase catalog blocked by incomplete variant identity/name. This slice does not resolve those business readiness blockers.
1. Make purchase catalog failures item-specific with visible exclusions and strict server revalidation. Preserve current order preparation catalog behavior.
2. Design and verify isolated stock purchase -> physical receiving -> valuation -> supplier/bank linkage; existing operational purchase registration alone is not physical receipt proof.
3. Reconcile operational opening quantities/costs with valuation without generating supplier debt or duplicating existing stock.
4. Verify fractional component quantities through purchasing, receiving and returns.
5. Verify valued supplier returns, damage and stocktake adjustments with once-only monetary effects.
6. Integrate stock asset value into operational financial position with explicit noncash presentation and no double expense.
7. Run isolated transactional Mongo and UI acceptance for the complete standalone lifecycle.
8. Only after separate owner authorization, plan matching/reservation/consumption for customer orders. Never alter current preparation as part of this task.

No production data, configuration, release lease, merge or deployment changed. This is a workspace foundation, not complete inventory acceptance or activation.

## Verification
Pending focused frontend checks after locked dependency installation. Git diff whitespace validation passed. Prior review could not run Python tests because pytest was absent; not claimed as passed.

## Durable checkpoint
PR: https://github.com/AMASI-SA/AMASI-SA/pull/1796 (Draft).
Implementation checkpoint: 1a201a7a2e35ee0ef5d1c32e8ed9a6f5bf5caee8.
Local frozen-lockfile install did not complete; offline attempt exited 1 because @jest/environment 27.5.1 was not cached. No functional test pass claimed. CI Operational Balance Web run 42387561312 was queued at inspection; other checks were in progress. Do not infer acceptance from these statuses.
Next: inspect exact implementation CI logs, resolve failures, then implement the isolated catalog/receiving/valuation stages above. All fulfillment integration remains deferred.

## 2026-11-07 continuation — WIP, not release ready
- Owner now authorizes inventory fixes deployment. Subsequent answers: owner will create new warehouse/cabinets personally after deployment; each product save must immediately record physical quantity (not draft/full-batch approval).
- Refreshed remote task bb178c43159e811d48c58a81f21e251adc8eb360 and merged production c634686 into local integration branch codex/inventory-numbering-20261107. Original task branch/PR #1796 preserved.
- Implementing opt-in compact cabinet-row-slot display on NEW warehouses; legacy barcode/code/ID and old warehouse remain intact. Independent stock admission has its own paused control; existing physical and financial controls untouched.
- WIP repeated single-entry inventory path reuses canonical opening validation and pending valuation receipts; idempotent request IDs, owner transactions, target isolated warehouse, no order integration/financial journals. UI implementation pending dedicated verification.
- Purchase screen configured catalog now avoids the unused legacy variant reader that blocked the entire screen.
- Local locked frontend installation succeeded. 58 existing OpeningInventory/InventoryPurchases tests passed. InventoryWorkspace test hit Jest 27 inability to resolve react-router/dom; fix test harness before claiming pass. Existing 6 warehouse generation tests passed. Real isolated Mongo tests being prepared; no transaction acceptance claim yet.
- No Production changes, merge, deployment or live stock writes. Do not deploy this WIP. Next: targeted tests (new control isolation, repeated saves, replay/conflict, capacity, rollback, variants, old opening regression), frontend save/retry/navigation, V5 release verification.
- Issue #1006 previously refused additional comments because its comment limit was reached; this status and PR are the durable checkpoint.
