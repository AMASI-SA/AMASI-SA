# Track B — onboarding V1 integration candidate

Status: **TRACK_B_ONBOARDING_DOMAINS_READY_FOR_INTEGRATION**.

Source-only candidate. No merge, deployment, Release Intent, live DB write, opening post, writer transition, or activation. P02 remains LOCKED. This status means Track B source is ready to combine with Track A; it does not assert deployed endpoints or live cutover readiness.

## Source identity and recovery

- Branch: `codex/mz2-onboarding-domains-ui-20260930`.
- Shared source base: `1d9d65d8576d5bc852f52262b84af150356b9385`.
- Shared base tree: `82ed0d05eee935d22c7a4050f21e66cdb9f4f551`.
- Recovery checkpoint `1e3c2bfc9cffbfc2e071e86789836f7c385d84e5`, tree `1e5ea651d9d3cfb2a6c1ef48edeca3608139a647`, was pushed before binding. Remote was checked at `6c8a343846cd097952f0cac415e46e8d0c23fe0e`; exact force-with-lease protected against concurrent remote changes. Subsequent integration commit is a normal fast-forward above the recovery point.
- Original independent checkpoint remains locally on `codex/mz2-onboarding-independent-checkpoint-20260930`.
- The shared base is a source identity, not a fresh assertion of live Production HEAD.
- Final HEAD/tree are reported by Git in the handoff, avoiding self-referential hashes in this file.
- `release/release-intent-v5.json` has no Track B delta against the shared base.

## Authority

- [16 stages: 5899581442](https://github.com/AMASI-SA/AMASI-SA/issues/1006#issuecomment-5899581442).
- [Track A V1: 5900935996](https://github.com/AMASI-SA/AMASI-SA/issues/1006#issuecomment-5900935996).
- [Identity, valuation and response clarification: 5900990152](https://github.com/AMASI-SA/AMASI-SA/issues/1006#issuecomment-5900990152).
- [Canonical financial URL correction: 5901180648](https://github.com/AMASI-SA/AMASI-SA/issues/1006#issuecomment-5901180648). This preserves the existing `/financial-provider-apps` mount. The client reads `definitions.financial_base`, validates the reviewed value, then removes `/api` because the existing HTTP client supplies that prefix.

## Implemented binding

`AccountingWorkspace` now renders the 16-stage onboarding wrapper for opening balances. It loads definitions, saved sessions, owner-scoped identities and canonical financial accounts. Session create requires explicit Riyadh cutover; resume reads the server document, without localStorage or a parallel session API.

The seven server sections remain `banks_cash`, `providers`, `couriers_cod`, `inventory`, `suppliers`, `payroll_obligations`, `equity`. Providers and advertising share providers; couriers and drivers share couriers_cod; suppliers and external persons share suppliers; prepaid/accrued/other balances share equity. Full replacement preserves sibling financial lines. The bound UI shows seven-section server progress rather than treating all sixteen screens as separate financial sections.

Saves use global CAS/version and stable idempotency keys. Uncertain network responses retain the exact request for explicit retry. Stale versions and same-key/different-payload conflicts require explicit reload. An advanced replay also requires reconciliation before any new save. Retry preserves other local edits. There is no autosave; navigation and typing perform no writes. Only setup save, preview and review endpoints are exposed; no handoff/post/transition/activation method exists in this client.

Uploaded evidence uses original file bytes and the existing service's source_file_id/sha256. Typed display references are not file IDs or proof. Complete and not_applicable are explicit financial-section choices; not_applicable needs reason/evidence and no sibling lines. Unknown amounts remain unknown. Zero is represented by an explicit decimal-string amount and meaning=zero. Preview/review use server validation, safe public error messages and fresh server snapshots; reviewed/handed-off sessions are immutable but all screens remain navigable. Readiness is informational and never enables live actions.

External person uses the exact selected owner-scoped counterparty ID. New contacts use existing `/counterparties` with `kind=general`; no new registry kind, name matching or inferred balance. The domain-specific phone extension is isolated to existing request schemas/storage (maximum 40 characters). Creating a contact locks navigation/session switching until its result is selected.

Inventory financial save contains explicit inventory_asset lines and valuation total_sar/evidence_file_id/manifest_hash/account_totals. Every account has its own value. Financial account references are explicit documented references, never inferred from product/category names. Physical quantities/allocations/resources/locations are excluded. Changing a restored valuation invalidates its old manifest proof until a fresh upload. The existing opening_inventory core and post-opening physical approval lifecycle are unchanged.

## Endpoints used

Under `/api/accounting-module/onboarding`:

- GET `/definitions`, `/sessions`, `/sessions/{id}`, `/identities/{kind}`, `/sessions/{id}/readiness`.
- POST `/sessions`, `/sessions/{id}/preview`, `/sessions/{id}/review`.
- PUT `/sessions/{id}/cutover`, `/sessions/{id}/sections/{section_id}`.

From the verified `definitions.financial_base`:

- GET `/api/financial-provider-apps/accounting-module/financial-accounts`.
- POST multipart `/api/financial-provider-apps/accounting-module/financial-accounts/opening-balances/evidence`.

Existing domain endpoints:

- POST `/api/counterparties`, kind=general, only on explicit create.
- GET `/api/products-v2` (pagination), `/api/components-v2/workspace`, `/api/warehouse-locations/locations`, only when the operator loads the inventory catalog. Existing scope/permissions remain enforced; failures are not replaced with a fabricated empty catalog. Bounded catalog truncation is shown. Registered component units remain unchanged, including missing-unit validation; no conversion/default unit is invented.

## Intentionally local fields

V1 explicitly excludes courier delivery cost, VAT, commission tiers, effective dates and advanced settlement terms; product/component physical quantities, allocations, warehouse/location/barcodes/specifications/preparation; ad funding references; free-text expense coverage notes. These remain volatile domain drafts in this page, with a persistent warning that reload/session restore discards them. No storage endpoint is invented. Each courier draft remains independent.

Payment-fee configuration is unavailable without a backend capability; future-fee learning is not claimed. Prepaid/accrued classifications come from definitions and do not imply automated amortization. Advertising wallets/payables use explicitly selected canonical financial accounts; profile identity is not inferred from a financial-account ID or untyped external_ref.

## Files

- `frontend/src/pages/accounting/AccountingWorkspace.jsx` and its existing opening-route test.
- `frontend/src/pages/accounting/onboarding/AccountingOnboarding.jsx` and integration/component tests.
- `OnboardingWizardView.jsx`, `OpeningEntityEditor.jsx`, `OpeningCourierEditor.jsx`, `OpeningInventoryEditor.jsx`, `OpeningTermsEditor.jsx`, `onboardingStages.js`, `onboardingDecimal.js`, `onboardingFinancialAdapter.js`, and tests in that same directory.
- `frontend/src/services/accountingOnboarding.js`, `onboardingSessionController.js`, `onboardingInventoryCatalog.js`, and tests.
- `backend/counterparties_routes.py` and `backend/tests/test_counterparty_phone.py` (phone extension).
- Preserved independent `backend/accounting_onboarding_domains.py` and its tests. This helper remains unmounted; final identity reads use Track A, not a competing endpoint.
- This report and eight synthetic-fixture screenshots.

## Validation

**85 frontend tests PASS (12 suites)**: original 26 UI tests retained; client/controller/projection tests expanded; 12 actual-wrapper tests cover create/resume, zero, not_applicable, stale CAS, lost-response replay without sibling loss, exact external ID, per-account inventory, safe preview errors, reviewed immutability and navigation, permission gates, no autosave/live actions. Existing accounting workspace regression tests also pass.

**13 backend tests PASS**: original 9 domain tests plus 4 real FastAPI contact-route tests with an in-memory contact-only database. No actual Mongo connection or live writes. One dependency deprecation warning from FastAPI/httpx remains.

Frontend command (from frontend; modulePaths exposes the already installed pnpm transitive react-router dependency):

```powershell
$routerModules = node -e "const p=require('path');console.log(p.dirname(p.dirname(require.resolve('react-router/package.json',{paths:[p.dirname(require.resolve('react-router-dom/package.json'))]}))));"
$env:CI='true'
node node_modules/react-scripts/bin/react-scripts.js test --watchAll=false --runInBand --testMatch '**/onboarding/*.test.jsx' '**/onboardingFinancialAdapter.test.js' '**/accountingOnboarding.test.js' '**/onboardingSessionController.test.js' '**/onboardingInventoryCatalog.test.js' '**/AccountingOpeningBalances.test.jsx' '**/AccountingWorkspaceReports.test.jsx' --modulePaths "$routerModules"
```

Backend command (from backend with PYTHONPATH set to that directory):

```powershell
& 'C:/Users/amasi/.codex/tmp/mz2-track-b-python/Scripts/python.exe' -m pytest --noconftest tests/test_accounting_onboarding_domains.py tests/test_counterparty_phone.py -q
```

Actual wrapper Vite fixture build passed (86 modules). Headless Edge exercised create/resume, explicit zero, preview/review CAS and reviewed lock. No page/console errors, no non-loopback requests, no horizontal overflow at 390px. Contract transport was in memory: this proves source/component behavior, not a live combined Track A deployment. Full application production build/live smoke was not run. Standalone ESLint could not run because the repository has ESLint 9 without a flat config; no lint pass is claimed or repository config changed.

Screenshots, all visibly synthetic:

- [Original cutover desktop](screenshots/01-cutover-desktop.png)
- [Original cutover mobile](screenshots/01-cutover-mobile.png)
- [Independent courier drafts](screenshots/07-courier-draft.png)
- [Product/component inventory](screenshots/10-inventory.png)
- [Independent final review](screenshots/15-review.png)
- [Bound session desktop](screenshots/16-session-desktop.png)
- [Bound session mobile](screenshots/17-session-mobile.png)
- [Reviewed session lock](screenshots/18-reviewed-session.png)

Local isolated browser harness: `C:/Users/amasi/.codex/tmp/mz2-track-b-review/session-proof`. Original five screenshots are preserved, not represented as newly captured integration proof.

## Blockers

No remaining Track B source blocker for integration with the published V1 contract. Combined-source deployment, live smoke, financial handoff/post, physical inventory approval and activation are outside this task and remain unauthorized. Domain-local persistence exclusions are explicit V1 limits, not hidden completed capabilities.
