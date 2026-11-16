# Shipping warning dependency correction

Base: de76aed339be775e2878071c92063d0ab9df06df (includes PR1821 product quantity corrections and Snapchat rollback). Branch: codex/financial-shipping-completion-20261108. Local isolated worktree: mezan-finance-shipping. No /app, live financial state, tariff, release lease, merge or deployment changed.

## Proven defect and change

The collector labels prepaid shipping evidence failures `component=prepaid_shipping`, but open-cost coverage did not recognize that declared component. Consequently a missing completion timestamp or unresolved prepaid charge blocked both product-cost buckets as well as shipping. The projection now retains the warning/count and shipping incompleteness, while allowing independently complete product costs to remain complete. It does not suppress canonical-source errors or alter any amount.

Equal issue codes are grouped for display. A scoped occurrence must not narrow an unscoped occurrence with that code. The projection now retains the conservative classifier for mixed occurrences, independent of input order.

Native-overlap boundary reviewed: `operational_historical_shipping_fee.native_conflicts` reads owner-scoped native courier_fee, store_driver_cod and shipping_settlement events and returns shipping fee/remittance conflicts. It does not inspect or alter supplier invoices. The financial-position cash/bank dependency classifier is unchanged; this fix only concerns product-vs-shipping cost buckets.

## Verification

- Original code: 3 new prepaid-scope tests failed as intended; unscoped control passed.
- Intermediate scoped implementation: both mixed scoped/unscoped orders failed as intended.
- Final command: `PYTHONDONTWRITEBYTECODE=1 PYTHON_DOTENV_DISABLED=1 PYTHONPATH=backend:backend/tests python -m pytest --noconftest backend/tests/test_operational_open_cost_exposure.py backend/tests/test_operational_balance_prepaid_shipping.py backend/tests/test_operational_balance_cost_completeness.py -q --tb=short`.
- 202 passed, no skips/failures. Existing two Pydantic deprecation warnings retained.
- Checks preserve output groups/currencies, all input state, and fail-closed unknown source coverage. `git diff --check` passed.

## Remaining and next action

Draft source checkpoint only; parent coordinator independently reviews, integrates current production, runs CI/V5 and publishes when the shared release guard permits. Actual production table counts and Aramex debit remain unverified by this patch. No assertion that all product tables will become complete while other real source gaps persist.

Separately investigate whether pre-completion prepaid orders have missing forecast shipping estimates. Do not mix that unproven hypothesis into this scoped correction. Live Aramex receipt/debit proof requires the genuine completed event, original shipment and approved cashbox; do not invent timestamps, reapprove rates, run financial backfill or add manual expenses.
