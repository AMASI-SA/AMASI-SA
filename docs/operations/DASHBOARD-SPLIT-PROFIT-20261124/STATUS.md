# Dashboard split-payment profit correction

## Scope and checkpoint

- Task: `DASHBOARD-SPLIT-PROFIT-20261124`.
- Repository: `AMASI-SA/AMASI-SA`.
- Task branch: `codex/dashboard-split-payment-profit-20261124`.
- Production target: `hotfix/prod-snap-meta-final`.
- Starting production-branch commit: `c6d05c7814e27e2f22a8c82b5bfe4cf177c17fbe`.
- Observed live frontend source before the change: `a611477be8d52b18f9ad851f44facf9c78be2905`.
- Verified implementation commit: `05333b6682be0cf5125568503bdd0175b8212121`.
  The subsequent status-only commit records the local validation and blocker;
  it is not a Protocol-v5 intent-only B or a remote checkpoint.
- Remote checkpoint/PR: **blocked by automatic approval review**. The push was
  rejected because the current user message did not explicitly authorize
  uploading potentially nonpublic source to GitHub. A read-only `ls-remote`
  subsequently confirmed that this task branch does not exist remotely.
- Production changed: **no**. No merge, shared `/app` change, release lease,
  publish, settings write, tariff change, sync, backfill, or accounting post occurred.

## Confirmed defect

The live executive dashboard withheld net profit with
`provider_split_payment_review_required`, while product-cost, currency and
financial-cohort completeness were true. The generic UI message made this look
like a missing commission configuration. Two native, fully paid SAR payment
shapes were unsupported: a card plus the internal customer wallet, and two mada
transactions with distinct references. Customer details and real transaction
references are not retained here or in fixtures.

## Implemented behavior

- `backend/dashboard_split_payment_fees.py` reuses the existing split-evidence
  validator for owner-bound, current, fully paid Salla transactions. It applies
  the saved capture tariff to each distinct external transaction only when that
  tariff is valid throughout the possible payment interval. No capture date is
  invented. Repeated identical references do not incur another fixed charge.
- `backend/dashboard_payment_fees.py` retains bounded native payment evidence,
  binds the owner context, detects selected-cohort reference collisions and
  exposes independently verified internal-wallet principal. Actual fees keep
  priority over tariff estimates.
- `backend/legacy_dashboard_reader.py` retains wallet redemption in sales and
  profit, while excluding it once from electronic principal and expected cash
  transfer. Unknown wallet evidence leaves cash-like totals unknown even when
  imported actual fees remain valid. Status-filtered electronic cohorts retain
  separate before/after completeness.
- The frontend completeness helper explains pending split-payment matching and
  continues hiding unknown or stale profit values.
- The existing payment-fee CI workflow includes the new module and regression
  files. No operational posting code or legacy fixed-fee contract changed.

Unsupported, ambiguous, foreign-currency, refunded, cancelled, stale or
cross-order-conflicting splits remain guarded. Duplicate-reference detection is
bounded to the already selected owner cohort and yields through the CPU budget.

## Synthetic acceptance values

These are configured-tariff estimates, not actual settlement fees or a current
live profit claim.

| Synthetic tender | Fee net | Fee VAT | Fee total |
| --- | ---: | ---: | ---: |
| Card145.86 + internal wallet33.01 | 4.21 | 0.63 | 4.84 |
| Mada329.40 + distinct mada198.72 | 7.28 | 1.09 | 8.37 |
| Combined | 11.49 | 1.72 | 13.21 |

For the canonical dashboard fixture with full sales706.99, product cost20,
and no advertising/shipping costs, profit is673.78, internal wallet33.01,
electronic principal673.98, and expected transfer/electronic net660.77.

## Verification completed

Original positive cases first reproduced the split-payment block. Cash-basis
tests also failed before integration: expected transfer incorrectly included
wallet principal. The frontend regressions first reproduced the generic hint.

The final source passed:

- **300 backend tests**, exit0, including all current payment-fee workflow
  suites, four canonical read-only cash-basis cases, 45 split-fee cases and the
  seven unchanged legacy same-rail fee cases.
- **189 frontend tests in seven suites**, exit0, including every current
  payment-fee workflow suite and executive-profit arithmetic. Repeated under
  the pinned Node22.23.2/Yarn1.22.22 release toolchain: 189 passed, exit0.
- Python syntax checks and `git diff --check`, exit0.
- Independent code review: no remaining blockers. Concrete refund/cancellation,
  projected currency, and wallet-cash findings were fixed and regression-tested.

The first combined backend command failed collection because a legacy test
imports sibling modules without the package prefix. Re-running with
`PYTHONPATH=backend:backend/tests` passed; no application change was needed.
Existing Pydantic/React test deprecation warnings remain unrelated.

Backend reproduction from repository root:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=backend:backend/tests python -m pytest \
  --noconftest -p no:cacheprovider -q --tb=short \
  backend/tests/test_dashboard_v2_ad_account_costs.py \
  backend/tests/test_dashboard_weekly_payment_fees.py \
  backend/tests/test_dashboard_operational_statement_fees.py \
  backend/tests/test_provider_fee_statement_actual_sources.py \
  backend/tests/test_provider_fee_weekly_average.py \
  backend/tests/test_provider_fee_weekly_sources.py \
  backend/tests/test_dashboard_dated_payment_fees.py \
  backend/tests/test_dashboard_split_payment_fees.py \
  backend/tests/test_dashboard_split_payment_cash_basis.py \
  backend/tests/test_dashboard_card_snapshots.py \
  backend/tests/test_dashboard_weekly_read_model.py \
  backend/tests/test_dashboard_demand_builders.py \
  backend/tests/test_operational_balance_configured_provider_fees.py
```

Frontend reproduction from `frontend` with installed project dependencies:

```bash
CI=true node node_modules/react-scripts/scripts/test.js \
  --watch=false --runInBand --resolver ./scripts/jest-package-exports.cjs \
  --runTestsByPath \
  src/components/ProfitSummaryCard.paymentFees.test.jsx \
  src/pages/AdvancedDashboard.paymentFees.test.jsx \
  src/components/DashboardAnalyticsPlacement.test.js \
  src/pages/AdvancedDashboard.cardRefresh.test.jsx \
  src/hooks/useDashboardCardSnapshot.test.jsx \
  src/lib/demandRead.test.js src/lib/dashboardExecutiveProfit.test.js
```

## Release state and next safe action

Source is verified locally. The governed Node22.23.2/Yarn1.22.22 frozen-lockfile
installation, two-pass frontend build and `verify_frontend_build.py` all passed
for implementation commit `05333b6682be0cf5125568503bdd0175b8212121` (exit0).
The local build used task-owned dependencies preseeded from an existing cache;
it is **not** a clean-clone adapter rehearsal. The borrowed symlink was removed
before the copy, and other tasks' caches/checkouts were not changed.

- Frontend artifact tree:
  `59de118c0e3fc1da1f837d09335725fb24ccd2898de28551f71271d9e756740b`.
- Reproducibility proof:
  `c1c14b1074e0b44efd0ab0b50bbe87eab0b5f396675de794f709db405f084e1f`.
- Both passes retained the same build-meta SHA256:
  `d6b7e94b78a839c539c170020b7217820e0c166921d2e66cf9ceab9e1d0460b2`.

Remote CI, draft PR, reviewed intent, adapter rehearsal and publication remain
pending. Ask for explicit permission to push this task branch to
`AMASI-SA/AMASI-SA`, open the reviewable PR, and publish after required release
checks. **Do not bypass the rejected push with another API or upload route.**

After authorization, fetch/inspect the current remote branch, push the preserved
task branch and create a draft PR with the exact head. Follow `AGENTS.md` and
`docs/PRODUCTION_RELEASE_PROTOCOL.md` for source A and intent-only B. Reverify
the current published base before freezing: the observed live source was from
PR2113 while the starting production branch already included PR2118 and its
newer intent. No guessed published-base SHA or rewritten old intent was used.

Publishing is a separately authorized step. Before shared `/app` work, its
actual guard must report inactive; do not reuse or clear another task's lease.
After authorized publication, require the new deployment success and all three
official release probes, then check the executive dashboard with current data.

Issue1006 read attempts returned `Transport closed`. No ledger comment was sent;
this task status and draft PR preserve the reviewable continuation. Browser
diagnosis completed before its transport disconnected during tab cleanup; no
post-release visual claim is made. Other task checkouts and the previous tracked
release intent remain untouched.
