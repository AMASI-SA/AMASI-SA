# Daily financial movements focused verification — 2026-11-08

This is isolated synthetic-data acceptance, not production balance certification or release approval.

## Result

118 passed, 0 failures/errors/skips, 5 existing Pydantic deprecation warnings; 24.30 seconds, exit 0. `git diff --check` passed. Test runner used base `python`, `PYTHONPATH=backend:backend/tests`, `python -B -m pytest --noconftest -q --tb=short` with these files:

- backend/tests/test_operational_balance_daily_totals_permissions.py
- backend/tests/test_operational_balance_activity_cash_projection.py
- backend/tests/test_operational_balance_daily_financial_movements.py
- backend/tests/test_operational_balance_app_permissions.py
- backend/tests/test_operational_balance_assigned_banks.py
- backend/tests/test_operational_balance_cash_assignments.py
- backend/tests/test_operational_driver_review_mongo.py
- backend/tests/test_operational_balance_driver_review_app_mongo.py

MongoDB binary: `/tmp/mongodb-linux-x86_64-ubuntu2204-8.0.12/bin/mongod`, version 8.0.12; executable SHA256 `f28b48b5f86b58a874978c90cedddd473d9a8befe6b445c3cc0520beeb004b45`.

Run an owned temporary replica set using subprocess.Popen, without --fork, with arguments `--dbpath <unique tempfile directory> --bind_ip 127.0.0.1 --port 27359 --replSet dailyScopeTotals --nounixsocket`. Confirm the port is unused before spawning. Initiate using a directConnection PyMongo client and `replSetInitiate` with the sole member `127.0.0.1:27359`; wait until `hello.isWritablePrimary` is true. Set BOTH `OPERATIONAL_TEST_MONGO_URI` and `MZ2_TEST_MONGO_URI` to `mongodb://127.0.0.1:27359/?replicaSet=dailyScopeTotals`. Run the suite above. Finally shutdown only that owned process and remove its temporary directory. The recorded run did so successfully. Do not reuse another task's process, database, or port.

The previous virtualenv path `/workspace/scratch/17a9cc329ec0/mezan-review-venv/bin/python` was unavailable in the resumed runtime (exit 127 before any tests); the successful result used base Python. This environment failure was not hidden or counted as acceptance.

## Verified scope

Browser access uses canonical `accounting.movements.view` with explicit denies respected. Movement creation separately requires `accounting.movements.import`; read grants do not permit writes. Assigned bank/cash restrictions are revalidated, including revocation immediately before response/persistence. Native permissions remain independent. Store drivers cannot use browser accounting permissions.

Daily rows are scoped and date-filtered before count, totals and ten-row pagination. `totals_by_currency` separates SAR/USD and cash directions from noncash amounts; `totals_basis` is `recorded_scoped_movements`. Unavailable sources return incomplete totals, not a certified zero. Missing historical balance evidence remains unknown without invalidating a known movement amount. Existing initialization was already `rows=[]`; no rows=None defect was found or fixed.

Tests cover permission/grant revocation, assignment revocation, hidden opposite accounts, pagination, multiple currencies, invalid direction/currency rejection, undated evidence, source availability, historical balance truthfulness, driver review idempotency, bank proof and independent collection/review rights. These tests do not constitute exhaustive acceptance of every pre-existing browser financial writer; unrelated return/exchange writers were outside this focused change.

## Performance evidence

100 new bank credits against 5,000 historical movements, three runs: baseline 6.538319 / 6.342531 / 6.346509 seconds; candidate 1.098389 / 1.132492 / 1.158673 seconds. Median reduction approximately 82%. All 100 intermediate balances matched (first 15001, last 15100). The dedicated suite also checks 120 mixed-effect prefixes against full reporting, refunds, transfers and current-state mutation behavior.

The optimization is restricted to bank/cash events without a debt counterparty. Debt/POS/expense counterparts retain the full-report path. Debt-heavy workload performance remains unverified; this evidence must not be generalized to all collection posting paths. No production financial data was changed.

## Follow-up: period expense evidence

`operational_profit_period_expenses.py` now reads the four canonical legacy expense collections declared by `expenses_routes.py` through a dedicated bounded, owner-filtered, exact-projection reader: daily expenses, annual rentals, prepaid expenses, and nonemployee salary categories. It also reads existing recurring contracts/invoices and checks operational expense payments and explicit advertising bank conversion fees. No writes, account-transaction summation, or opening balances are used. Household expenditure is excluded as personal consumption; charity records require business classification. Payroll remains in its separate component.

Known daily expense amounts count once; annual rental consumption follows the canonical 365 divisor, prepaid consumption its inclusive contract days, and recurring consumption the canonical calendar periods. Potential legacy/recurring duplication is an explicit row-specific issue. Allocated recurring payments are excluded only when their saved obligation/source identity matches the canonical contract; unrelated direct expense payments require an accrual link. Only an explicit bank conversion fee is expense evidence, never the advertising wallet principal.

The constant incompleteness placeholder was removed. An exhaustively read empty declared expense scope is a known zero. For actual expense rows, the current native create/update schemas do not define input-tax recoverability; the result preserves the known amount and identifies each affected source ID with `profit_expense_input_tax_basis_required`. There is no invented tax policy or default zero tax. Partial periods, historical revisions, missing actual utility invoices and ambiguous duplicate records remain explicit issues.

Latest focused result: 64 passed, 2 pre-existing Pydantic deprecation warnings, 1.08 seconds, exit 0; `git diff --check` clean. Command: `PYTHONPATH=backend:backend/tests python -B -m pytest --noconftest -q --tb=short backend/tests/test_operational_profit_period_expenses.py backend/tests/test_operational_balance_sources.py`. This covers 17 expense helper cases plus 47 strict source-boundary cases. The shared source fixture accepts only the new explicitly enumerated collections and exact approved field projection. Production source allowlists were not weakened.
