# Prepaid shipping forecast coverage checkpoint

Base: 845f75599ecdaedf608b082f4265a676023a7b62 (PR #1828), deliberately separate from that frozen patch.

## Reproduced
Synthetic pending/in_progress Aramex order, approved prepaid SAR 30 + 15% VAT tariff valid at snapshot:
- Created before start: historical_shipping_quote_incomplete; no shipping estimate, shipping coverage false.
- Created after start: no shipping group and no shipping amount, yet shipping coverage true.
`reproduce.py` prints this distinction and asserts no database/state mutation. It has no production connection.

## Safe incremental fix
For a current, open, prepaid-matched carrier order without an immutable prepaid charge fact and without an active native driver assignment, shipping forecast coverage is now unknown with `current_prepaid_shipping_forecast_eligibility_unverified`. This issue is scoped only to shipping_expected; no amount is invented, no balances move, and independent product buckets stay complete when their own evidence is complete.
Closed orders, different carriers, paid prepaid facts and native driver assignments do not get this diagnostic. Existing historical behavior is preserved.

## Remaining calculation work
Do not simply expand the delivery-contract filter to order_completed. A current in_progress order may have previously completed and reopened; the open-cost projector does not load the bounded native status-transition history. A safe future estimate requires authoritative pre-completion eligibility and exclusion of already charged service, with incomplete history remaining unknown. The absence of a history entry alone is not proof that completion never happened. No production count or 34.50 live cash debit is claimed here.

## Verification
Three new cases fail on the unchanged parent (in_progress/completed/shipping each incorrectly produce no issue). The initial red invocation lacked PYTHONPATH and failed collection; rerun with the command environment below produced the intended three assertion failures. Restored final code: 209 passed, zero skips, two existing Pydantic deprecation warnings. Four negative controls cover charged/closed/other carrier/native driver. `git diff --check` passed.

Command:
```
PYTHONDONTWRITEBYTECODE=1 PYTHON_DOTENV_DISABLED=1 PYTHONPATH=backend:backend/tests python -m pytest --noconftest backend/tests/test_operational_open_cost_exposure.py backend/tests/test_operational_balance_prepaid_shipping.py backend/tests/test_operational_balance_cost_completeness.py -q --tb=short
```
No /app changes, financial backfill, tariff approval, merge, release intent, deployment or courier update.
