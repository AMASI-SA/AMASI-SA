# H2 — currency input rule

Status: locally implemented and verified; deliberately uncommitted under the user's freeze instruction. No new PR or CI run. PR #1217 remains frozen and untouched.

Worktree: `C:/Users/amasi/.codex/worktrees/mz2-h2-currency/ميزان`
Branch: `codex/mz2-h2-currency`
Base Production HEAD: `5a7b44b71c6c9974aba358493b3267a47d6e6314`
Base TREE: `87f9a44af5dc7d00fbc62417fb17e2f6273d8d93`
Frozen H1 HEAD: `be1540582c32760c6308c975d6ebf6308feeb7aa`
Frozen H1 TREE: `8eb15e8c78f4bc7712dc0aa94156f1bf6bdd74db`

## Contract delivered

- Actual currency controls use native Select only, backed by a pinned current ISO 4217 vocabulary. No free text or legacy settings source.
- SAR defaults for new, unbound rows; the existing submission adapter supplies FX parity 1. Explicit invalid/blank/null saved currencies are never silently changed to SAR.
- Foreign currencies expose positive FX rate, Riyadh timestamp, and source/evidence requirements. Existing backend decimal conversion, parity and evidence semantics are unchanged.
- Bound MZ2 Financial Account currency is displayed as read-only output. Missing/invalid account currency shows BLOCKED_BY_BACKEND / not_ready, with no manual alternative.
- Independent advertising wallet/payable account FX metadata remains isolated by actual account ID. The adapter emits the existing individual opening-line payloads; no new backend schema, balance calculation or advertising writer.
- Restored offset-aware FX timestamps display Riyadh local time correctly. Changing currency clears stale FX metadata and evidence, never changes the entered amount.
- Backend rejects unlisted currency at AccountCreate, OpeningLine and incomplete SectionData validation boundaries, before persistence. Existing lowercase API compatibility is preserved.
- Existing SAR-only policy screens are not expanded to new currencies; unavailable backend capabilities remain unavailable. Creating a financial account has no amount/FX snapshot and therefore does not invent one.

Scope: existing MZ2 account creation, generic opening balances, entity/bank/ad opening forms, prepaid/obligation forms and their current adapters. Legacy Accounts and advertising settings are untouched. Stage 10 files, ledger semantics, writers and release controls are untouched.

## Currency source

[SIX official ISO 4217 Maintenance Agency](https://www.six-group.com/en/products-services/financial-information/market-reference-data/data-standards.html), [List One XML](https://www.six-group.com/dam/download/financial-information/data-center/iso-currrency/lists/list-one.xml).

Snapshot published 2026-09-17: 178 current currency/fund codes. The full published vocabulary is retained; no arbitrary supported-country or fund exclusion policy is invented. Backend JSON records source SHA256 and publication date. Frontend uses an identical bundled copy, verified by a parity test. No runtime network lookup or FX-rate download.

## Evidence

- Initial regression: account currency rendered INPUT instead of SELECT (red.log).
- Frontend: **81 tests / 9 suites PASS** (frontend.log).
- Backend: **44 tests PASS** (backend.log).
- Source Vite build: **PASS** (build.log); existing CSS import/chunk warnings remain unrelated.
- Tests cover invalid codes including direct HTTP 422 request-model rejection, exact frontend/backend vocabulary parity, SAR reset, foreign required fields, read-only MZ2 account currency, no missing-account SAR fallback, separate account FX payloads, and Riyadh timestamp restoration.
- Backend test command: `PYTHONPATH=backend python -m pytest backend/tests/test_accounting_currency.py backend/tests/test_financial_accounts.py backend/tests/test_accounting_opening_categories.py backend/tests/test_accounting_onboarding_domains.py -q`.
- Frontend: Node 22.23.2, CRA Jest `--watchAll=false --runInBand --testMatch '**/CurrencyFields.test.jsx' '**/AccountingFinancialAccounts.test.jsx' '**/onboarding/*.test.jsx' '**/onboarding/*.test.js'`; source build through `node node_modules/vite/bin/vite.js build`.
- No Production API requests or browser writes. Full backend route/Mongo integration and deployed-browser verification are not claimed. CI was not launched because no commit/push was performed.

## Recovery / next step

The complete uncommitted patch is preserved in the original workspace at `outputs/H2-CURRENCY-20261001/H2-CURRENCY.patch`. Review it against the exact base above. Do not apply it over independently modified identity/onboarding files without reviewing those diffs. Backend validation touches Financial Account and onboarding contract files used by independent tracks; integration must reconcile these minimal validators rather than copy other branches.

Next safe action: review the patch and integration base. No commit/rebase/push/merge/deploy is authorized by this delivery. New HEAD/TREE do not exist because the patch is uncommitted.

Production writes = 0. Commit = NO. Rebase = NO. Merge = NO. Deploy = NO.
