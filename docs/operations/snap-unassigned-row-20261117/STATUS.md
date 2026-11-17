# Snapchat unassigned account row — 2026-11-17

## Scope and state

- Task: `snap-unassigned-row-20261117`.
- Repository: `AMASI-SA/AMASI-SA`.
- Branch: `codex/snap-unassigned-row-20261117`.
- Reviewed starting Production commit: `8df479ef24b4f8847c26ba18d48e3248f7b673cf`.
- User requested an additional **سناب غير محدد** row, followed by verification that bank commissions are included in profit.
- Implementation, local regression verification and independent integrated review are complete. The independent reviewer found no blocking defects.
- Merge/publish state: **blocked by the repository dependency audit**. The feature tests and governed builds passed; the later Security Gate failure is documented below. Do not treat the feature as releasable while that failure remains.
- Verified implementation checkpoint: `be0ed46d9441d9739759c2061d0f33e229977395`, source tree `7f61ab2e6c120e232c18bbb9dbf36917aac7aa88`. This matches the locally tested source exactly. The subsequent handoff checkpoint changes only this status document.
- Draft PR [#1969](https://github.com/AMASI-SA/AMASI-SA/pull/1969) is the canonical continuation record for the latest full commit SHA and final CI state. Issue #1006 was read and has 2,500 comments; its existing body is preserved.
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

## Remote verification and handoff

The following completed on the implementation checkpoint above. PR merge checkout `814c0cad5be43491f6f9d1ec5c047f14ca0c9f0c` has the exact reviewed source tree, with Production base and implementation checkpoint as its parents.

- [Four-platform dashboard CI](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44161054392): success. Frontend 425 tests / 13 suites passed; production build and UI contracts passed. Backend 356 tests plus 5 subtests passed, with 2 warnings; compilation, source-parity and original provider-read contracts passed.
- [Hourly dashboard CI](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44161054158): success. Frontend 140 tests / 4 suites, production build, backend 12 tests, and provider/hourly contracts passed.
- [Release readiness](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44161054347): success. The governed A/B frontend build, retained artifact/proof verification, source-candidate intent artifact, worktree-cleanliness proof and host dispatcher contracts passed. The intent was not added to the branch; deployment and clean-clone release-adapter jobs were correctly skipped for this source-only candidate.
- Independent review covered single-count totals, outside-account scope, historical/incomplete snapshots, FX/time boundaries, no additional I/O, and no duplicate bank-fee deduction. It made no source edits.
- The subsequent documentation checkpoint `123a505f5adc8c11dcbff5bd63fc30d7eaed4746` repeated the dashboard and governed-build checks successfully. Its CI merge `9c0aa2ffec232dc51e5008f06fe029c0a86f85d6` has exact tree `be30fcf9402a735877ce80c1a24ceb414b22f10c` and parents Production base plus that checkpoint. The current follow-up again changes only this status document; the PR records its exact latest head and CI state.
- Remaining acceptance: the additional row and fee disclosure have not been visually checked in a deployed candidate. A synthetic local desktop/mobile preview was attempted, but Chromium exited before opening a page with `socket() failed: Operation not permitted`; no screenshot or layout observation was produced. The temporary server was stopped and no live business APIs were contacted. Component and contract tests passed. Production has not changed.

## Blocking dependency audit — 2026-11-17

- [Security Gate run 44161630418](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44161630418), frontend job `134316699399`, failed at **Audit production frontend dependencies** on checkpoint `123a505f5adc8c11dcbff5bd63fc30d7eaed4746`.
- The official [GHSA-fc5q-39xc-vjpf advisory](https://github.com/advisories/GHSA-fc5q-39xc-vjpf), updated November 17, affects `picomatch <=4.0.7` and lists no patched version. The [upstream releases](https://github.com/micromatch/picomatch/releases) and npm registry both still identify `4.0.7` as latest. Independent investigation confirmed no supported narrow upgrade is available.
- Audit reports five high-severity paths through Tailwind and Vite, using `picomatch` versions `2.3.2` and `4.0.7`. This is one advisory reported for five dependency paths.
- `frontend/package.json`, `frontend/yarn.lock`, `scripts/check_frontend_audit.cjs` and `.github/workflows/security-gate.yml` are byte-identical to Production base `8df479ef24b4f8847c26ba18d48e3248f7b673cf`. The feature did not introduce or change these dependencies or the audit policy. Lock SHA-256: `ade3795ddac57d89c236de42f3946160e04aa72abc87356b3b361954e06fefdc`.
- Upstream PR #194 remains open and explicitly leaves the `+(*a)` case vulnerable; it is not a fix for this advisory. A legitimate local patch or replacement needs separate reviewed security and compatibility work across both dependency versions. No exception, version relabeling, dependency reclassification, policy change or audit bypass was attempted.
- On that checkpoint, dashboard, hourly, release-readiness, CodeQL, G47, GA4, advertising and payment workflows passed. Operational Stock Eligibility and Operational Balance Web were still running at this final handoff. Dashboard CI again passed **425 frontend tests** and **356 backend tests plus 5 subtests**, and its production build passed.
- A source-candidate intent artifact exists in [readiness run 44161630326](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44161630326), but no intent commit or release was created. That artifact is bound to its recorded source SHA and must not be used for a later changed source or to bypass the failed audit.
- Issue #1006 rejected the continuation comment with HTTP 403 because its 2,500-comment limit disables comments. Its existing body was preserved; PR #1969 and this status document retain the complete handoff.

## Next safe action

Fetch `codex/snap-unassigned-row-20261117` and verify its full SHA against PR #1969. Resolve the unpatched `picomatch` dependency through a supported fixed release or separately reviewed actual remediation, preserving the strict audit policy; then run the required checks on the resulting exact head. Keep this feature PR blocked until that gate succeeds. Preserve the completed bank-fee release #1965. A subsequent production release requires its own authorized v5 source-A / intent-B workflow against the then-current Production base, followed by live row/fee-display acceptance. Do not create a lease, merge, publish, synchronize providers, or write financial data as part of this source-only task.
