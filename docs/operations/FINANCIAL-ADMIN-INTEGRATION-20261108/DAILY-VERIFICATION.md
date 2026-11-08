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
