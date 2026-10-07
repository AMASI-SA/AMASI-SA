# Customer-service operational returns — 2026-10-07

Companion Draft #1271 only. Frozen #1263, native #257, Accounting and Production are unchanged.

## Approved behavior

- Separate responsive Web route `/operational-balances/customer-returns`, linked from the operational navigation.
- Search the canonical MZ2 order, select available line quantities or the entire available order.
- `pending` records products without a bank/platform refund. `refunded` records a refund already executed externally; this page never invokes a bank or provider refund API.
- Confirm a pending case in place. Requested quantities cannot exceed original quantities across concurrent cases. Actual refunds cannot exceed the original order total across cases.
- Bank refund reduces operational actual bank once. Provider refund enters the existing provider projection once through its immutable refund identity; no second bank debit.
- Original platform commission remains retained. Read approved MZ2 fee policies; missing/incompatible retention policy blocks confirmation instead of guessing or editing policy.
- Return delivery price is the original confirmed **base shipping** component when present; otherwise reconstruct from the approved MZ2 delivery tariff at the evidenced original delivery date. Missing date/rate is incomplete, never today's tariff or a hardcoded price. COD collection commission is excluded. Tax follows that existing component contract.
- **17.25 is an example, not a universal rate.** Tests use 28.75, a later tariff of 46.00 and a frozen original price of 23.00.
- Internal store courier return is free and creates no additional delivery liability.
- Carrier return shipment is expected until marked executed, then confirmed; this alone does not reduce bank actual. Record carrier, shipment reference and execution state.

## Persistence and boundaries

`customer_returns` lives in the existing operational owner aggregate. Case, refund effect, shipping obligation, audit and replay result share its atomic compare-and-swap. Ownership-bound operation claims prevent changing owner on retry. The UI persists an unresolved request before sending, locks editing and retries the same request after reload.

All source reads use the existing MZ2 allowlist. No source order, carrier contract, commission policy, supplier invoice or inventory record is written. Browser membership uses the existing Web access contract. New routes are not granted to native clients; no Mobile permission change.

Executed bank references cannot be recorded again as manual bank movements, in either order. No receipt attachment is required. Refund reference and execution time establish the identity of the externally completed refund.

Platform refunds require the original platform and a post-operational-start order whose projection can be proved; baseline-era platform refunds remain rejected. Incomplete or conflicting source evidence rejects financial confirmation atomically.

## Verification

- Local isolated Mongo `127.0.0.1:27316`, disposable `operational_balance_test_<uuid>` databases, synthetic sources only.
- New backend return suite: 16 PASS, covering pending/actual, partial/full, concurrent retry/quantity conflicts, fee retention, bank read-back after refresh, historical and frozen shipping price, free driver, changed/missing quote, invalid quantities/source/amount, browser API ownership, native rejection and duplicate bank references.
- Full operational + employee management regression: **227 PASS**, 0 FAIL, 0 SKIP, exit 0. Command: `python -m pytest tests/test_operational_balance*.py tests/test_employees_v2_management.py -q` (PowerShell expands file glob).
- Web suites: CustomerReturns, OperationalBalances, DomainDetails, MovementHistory, MezanV2NavigationShell.employees, EmployeesV2Management. 81 PASS.
- `node scripts/verify-customer-returns-browser.cjs <evidence-directory>`: desktop 1440x1000 and mobile 390x844 PASS. Pending save → reload → confirm same case → reload, RTL, no horizontal overflow. All API requests intercepted with synthetic data, external origins blocked. This is a UI fixture plus separate real Mongo/ASGI tests, not full connected deployed UAT or Android UAT.
- Local screenshots: `D:/codex-evidence/operational-customer-returns-20261007/{desktop,mobile}-{pending,confirm}.png`.
- Initial browser fixture locator was corrected to match the existing nested select label; no application gate weakened. Initial regression command used an incorrect employee-test filename; rerun uses the actual existing suite.

No Merge, Deploy, Prepare, Prepublish, APK, Accounting writer, stock writer or lease action. Production writes = 0. Review the Draft before release; no release acceptance claimed.
