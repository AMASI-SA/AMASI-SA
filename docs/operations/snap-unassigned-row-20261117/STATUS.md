# Snapchat unassigned account row — 2026-11-17

## Scope and state

- Task: `snap-unassigned-row-20261117`.
- Repository: `AMASI-SA/AMASI-SA`.
- Branch: `codex/snap-unassigned-row-20261117`.
- Reviewed starting Production commit: `8df479ef24b4f8847c26ba18d48e3248f7b673cf`.
- User requested an additional **سناب غير محدد** row, followed by verification that bank commissions are included in profit.
- Implementation and local regression verification are complete. Independent integrated review and remote CI are pending at this checkpoint.
- The Draft PR is the canonical continuation record for the exact source commit and later CI state. Issue #1006 was read and has 2,500 comments; its existing body is preserved.
- **Production changed: no.** No merge, release lease, intent update, deployment, provider synchronization, business-data write, or accounting mutation was performed.

## Implemented behavior

1. `backend/dashboard_ads_account_results.py` returns `unassigned_result` for the daily and half-day cohorts using the already filtered order objects and existing request-local cost collector. It contains only Snapchat orders whose account cannot be established. Proven outside-scope orders remain separate.
2. Known orders, converted sales and known order costs remain explicit. Attribution stays incomplete; no account identity, spend, bank commission allocation or profit is invented.
3. `frontend/src/lib/dashboardAdsAccountResults.js` validates the cohort identity, coverage count, period, Riyadh timezone, time boundaries and currency flags before exposing the separate row. It never feeds that row back into the existing total calculation.
4. `frontend/src/components/DashboardAdsSpendCard.jsx` shows the row immediately after Snapchat accounts, only when there are unmatched orders and the source evidence is valid. It is included in the morning/evening overview without adding columns. Unknown spend, CPA, ROAS and profit remain unavailable. Outside-scope warnings remain visible.
5. `DashboardAdsAccountRow.jsx` and `DashboardAdsPeriodsOverview.jsx` disclose the already deducted bank commissions and explain that table profit is before operating expenses. No calculation or second deduction was added.

## Bank-fee finding

Read-only live inspection of the November 17 report confirmed that the bank commission is already included once in total costs. The difference between table profit and executive net profit exactly equals operating expenses. The source calculation is preserved; the change clarifies its displayed meaning.

## Fresh local evidence

- Focused frontend: **247 tests / 4 suites passed**, no failures or skips.
- Dashboard frontend regression: **425 tests / 13 suites passed**, no failures or skips.
- Backend account, cost and period contracts: **79 tests passed**, exit 0. Only existing Pydantic deprecation warnings remain.
- `git diff --check`: exit 0.
- New tests failed for the intended missing-row/missing-disclosure behavior before implementation.

Frontend regression command, from `frontend/`:

```bash
CI=true node node_modules/react-scripts/bin/react-scripts.js test \
  --watchAll=false --runInBand --runTestsByPath \
  src/components/DashboardAdsSpendCard.test.jsx \
  src/components/DashboardAdsSpendCard.manualRefresh.test.jsx \
  src/components/DashboardAdsAccountRow.test.jsx \
  src/components/DashboardAdsPeriodsOverview.test.jsx \
  src/lib/dashboardAdsAccountResults.test.js \
  src/pages/AdvancedDashboard.cardRefresh.test.jsx \
  src/lib/dashboardPlatformSpendClient.test.js \
  src/services/dashboardAdsSpend.test.js \
  src/components/DashboardAnalyticsPlacement.test.js \
  src/components/PaymentMethodSetupNotice.test.jsx \
  src/pages/operational/ObservedPaymentMethods.test.jsx \
  src/pages/operational/ProviderFeeSetup.test.jsx \
  src/services/accountingOnboarding.test.js
```

Backend command, using a compatible Python test environment from the repository root:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHON_DOTENV_DISABLED=1 PYTHONPATH=backend:backend/tests \
python -B -m pytest --noconftest -p no:cacheprovider -q \
  backend/tests/test_dashboard_ads_account_results.py \
  backend/tests/test_dashboard_ads_costs.py \
  backend/tests/test_dashboard_ads_periods.py --tb=short
```

## Next safe action

Review the integrated diff, resolve any concrete finding, and inspect exact-head remote CI. Keep the task recoverable in its Draft PR. Do not republish the completed bank-fee PR #1965. Any subsequent release of this new row must follow the separately authorized v5 source-A / intent-B workflow against the then-current Production base.
