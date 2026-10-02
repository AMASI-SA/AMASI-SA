# Track G report source map and integration ports

All `/api/financial-provider-apps/accounting-module/reports/*` reads select
`accounting_general_ledger_v2` through the existing immutable journal verifier.
`legacy_active` returns `mz2_native_ledger_required`; no report falls back to
`general_ledger`, `accounts`, `counterparties`, or legacy financial balances.

| Report / identity | Classification authority |
| --- | --- |
| Financial position, trial balance, journals | Native verified ledger, all identities checked before any totals |
| Account statement | Exact entity type / ID; each subaccount remains separate |
| Bank / cash / overdraft | `mz2_financial_accounts` exact account type |
| Employee | `mezan_employees_v2` through onboarding identity adapter |
| Supplier | `mezan_suppliers_v2` through onboarding identity adapter |
| External person | `mz2_external_persons_v2` |
| Courier / driver | Existing approved courier policy and exact store-driver contract through identity adapter |
| Ad | Canonical financial account plus native ad binding; absent binding is UNRESOLVED |
| Prepaid / accrued / other receivable / other payable | `mz2_prepaid_selections_v2` / `mz2_opening_facts_v2` exact typed identity |
| Tax | Distinct native `input_vat` / `sales_vat_payable` system identities or typed opening facts |
| Inventory | Verified native opening category + exact inventory subaccount + evidence |
| Opening equity / system revenue / core expenses | Explicit existing native ledger identities; unknown system identities blocked |

New read-only reports: `account-statement/{entity_type}/{entity_id}`,
`couriers-drivers`, `suppliers`, `employees`, `ads`, `tax`,
`expenses-liabilities`. Existing permission `accounting.journals_reports.view`
and freshly loaded owner scope apply to every route. Query scope cannot override
the tenant or ledger. Statement balance is null when more than one subaccount
exists, preserving separate payable/advance and input/sales tax facts.

Unknown IDs or categories produce `unresolved_mz2_identity`, named
`readiness_blockers` carrying `UNRESOLVED`, and null financial-position totals.
A missing statement is unavailable, never zero. Explicit approved zero rows must
reference the exact opening group, accounting date, and evidence; statements and
trial balance preserve this evidence. Empty native scope without such evidence
is `opening_balance_evidence_missing`. Position omits absent categories rather
than inventing category zeroes. Tax asset and payable are never netted.

## Remaining integration dependencies

- Native ad binding capability is absent from fresh Production. No excluded PR
  was copied. Ad accounts block until that separate integration port is supplied.
- Runtime expense/revenue/liability producer categories beyond the explicit native
  contracts require an identity adapter. They remain visible classification
  blockers; no generic catch-all financial authority is introduced.
- Existing tests and runtime writers that expect `read_mz2_ledger` to read tagged
  legacy journals must migrate to native V2. This intentional fail-closed change
  is required by the MZ2-only report contract.
- `financial_position_ssot.py` remains a historical legacy implementation; its
  user-facing route labeling/quarantine is handled by the route-audit slice.

## Verification

`PYTHONPATH=backend MZ2_TEST_MONGO_URI=mongodb://127.0.0.1:27128/?replicaSet=trackg python -m pytest backend/tests/test_track_g_native_reports.py -q -o asyncio_mode=auto`

18 passed, exit 0. Seventeen report-boundary cases use synthetic native read fixtures;
one disposable local replica-set test exercises the real native journal
serializer, immutable verification, balance computation, report reconciliation,
and corruption rejection. The local test creates a synthetic opening fixture in
an isolated random database and removes that database afterward. No production
Opening action, activation, financial write, deployment, or Release Guard ran.
Forbidden legacy collection access raises immediately in every report test.


Follow-up regression run: `PYTHONPATH=backend;backend/tests` and the same local
Mongo URI, `python -m pytest backend/tests/test_mz2_report_isolation.py backend/tests/test_mz2_report_dates.py backend/tests/test_track_g_native_reports.py -q -o asyncio_mode=auto`:
37 passed, exit 0; five existing Pydantic deprecation warnings in the historical
legacy diagnostic control. Legacy fixture success expectations now assert
`mz2_native_ledger_required` rather than silently re-enabling the retired reader.
Auth revocation, scope rejection, corrupt legacy evidence, and historical isolation
coverage remain. Native tests separately prove exact identities, foreign/inactive
registry rejection, reconciliation, corruption, empty/missing versus explicit zero.

Onboarding integration: `mz2_report_readiness(db, owner=owner)` is read-only and
returns `{applicable, status, blockers}`. No native opening pointer means
`not_opened`, with no report blocker. An existing native book returns exact
identity blockers or the precise ledger/evidence reason. This port does not
activate, post, or mutate anything.


Employee identity refinement: the V2 employee operational ID is reportable only
when the native registry explicitly binds `financial_entity_id` to that exact
V2 ID (`financial_identity_ready=true`). Missing or different bindings surface
`onboarding_employee_financial_identity_dependency`; no operational-ID inference.
Successful fixtures carry the explicit binding, and both missing/different
mappings are regression-tested. Async fixtures use `pytest_asyncio.fixture`.
Final focused native report run without asyncio-mode override: 20 passed, exit 0.
