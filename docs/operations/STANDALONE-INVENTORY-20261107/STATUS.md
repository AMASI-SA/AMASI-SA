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
