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

## Independent profit review and regression fixes

The independent review reproduced three substantive failures before repair: a revised salary could still be reported complete despite a conflicting frozen employee day; an extreme payroll date range reached the unbounded candidate expansion; and a fully evidenced store-driver cash collection could never establish its zero platform fee. A fourth regression demonstrated that status exclusion could hide a stale canonical order. The initial COD test also had a missing synthetic `facts` fixture; that setup error was corrected before confirming the real failure, and was not counted as a product failure.

Repairs reconcile candidate salary days with saved salary days, paired obligations and conflicts; bound payroll expansion to 3,660 calendar days and 250,000 revision-days; index customer returns and conflicting provider facts once; validate canonical order identity/version before applying the status policy; and accept zero platform fee only for explicit COD/cash methods with full saved store-driver cash collection evidence, excluding splits, partial collection, card receipts and provider captures. Carrier COD charges remain a separate shipping component. Configured tariff projections can supply net fee when VAT is explicitly zero, or when separate saved tax evidence is bound to the same tariff/currency/tax amount; VAT recoverability is never assumed. No live tax evidence is claimed.

After repair: 89 tests passed across profit period, expense period, recorded salary and source suites (1.34 seconds, exit 0), and `git diff --check` passed. The root-owned return recognition helper was then independently reviewed against its existing compensation producer contract, with 25 combined return/period tests passing (0.86 seconds, exit 0). This helper reads an already-net current sale and explicit current VAT without subtracting the refund again or posting any financial effect. These results do not replace release-head checks or production certification.

## Driver bank/POS after-balance capture follow-up

The earlier cash-only optimization did not cover approved receipts with a driver counterparty or a POS receivable account. A new reproduction constructs valid sealed approval facts and their actual matching bank movement/POS obligation, then invokes the real audit capture hook after each effect. This measures capture overhead, not the complete approval HTTP transaction.

For 100 consecutive approvals over 5,000 existing bank movements, the pre-change fallback took 7.961s (bank) and 7.478s (POS). The candidate took 1.194s (bank, 6.67x faster) and 0.076s (POS, 98.24x faster). All 100 per-case snapshots matched before/after after excluding only capture timestamp and its dependent seal. No final-batch balance was copied onto preceding events.

`_driver_review_projection` computes only the affected bank/cash and driver/POS confirmed net balances from the current transaction state. It preserves opening allocation identity, confirmed obligations, settlements, overallocations, unallocated advances, signed corrections, incomplete claims and authoritative names. It does not cache across events or include estimates as confirmed debt. Special supplier/provider credit or funding models retain the existing full-report fallback. Existing cash projection behavior remains.

New tests: `backend/tests/test_operational_balance_activity_driver_projection.py`. Mixed-effect prefix parity compares to the authoritative full report, verifies current edited openings/allocations and immutable prior snapshots, and prohibits full-report calls for both 100-receipt supported cases. The benchmark helper is retained in this test module for reproduction.

Final isolated replica-set execution: **96 passed**, 5.73s, exit0 across the new driver projection suite, existing cash projection, store-delivery noncash review and daily-financial-movements suites. This includes real Mongo bank/POS approval and later settlement assertions. Run with `OPERATIONAL_TEST_MONGO_URI=mongodb://127.0.0.1:27326/?replicaSet=driverCapture`, `PYTHONPATH=backend:backend/tests`, and `python -m pytest <four files> -q --disable-warnings --tb=short`; start/init/wait for an owned loopback Mongo8.0.12 replica set as described above, and terminate only that process afterward. An initial standalone Mongo attempt failed the existing replica-set guard; the final run used the required replica set without altering/skipping assertions.
