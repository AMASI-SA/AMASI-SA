# Dashboard split-payment profit correction

## Scope and checkpoint

- Task: `DASHBOARD-SPLIT-PROFIT-20261124`.
- Repository: `AMASI-SA/AMASI-SA`.
- Task branch: `codex/dashboard-split-payment-profit-20261124`.
- Production target: `hotfix/prod-snap-meta-final`.
- Starting and currently observed production-branch commit J:
  `c6d05c7814e27e2f22a8c82b5bfe4cf177c17fbe`.
- The user explicitly authorized upload, merge and publication on
  **25 November 2026, Asia/Riyadh**: "نعم ارفع وادمح وانشر".
  The earlier automatic approval rejection is resolved by that new authorization.
- The subsequent Git CLI push failed because the local Git credential was
  unavailable (`could not read Username`), rather than another approval rejection.
  The authorized upload used the connected GitHub Git Data API.
- Verified remote source checkpoint before this status update:
  `6af60231b7a89e789c1f26600dcc8ad60b0589a0`, with sole parent J and tree
  `210fe97c253e84dcd6798c6d9758cc2f4fe9289e`.
  All 10 uploaded blob IDs matched local Git objects; the complete remote tree
  exactly matched preserved local checkpoint
  `764daef45a768f28bbc13cd103788aaf6316604a`.
  Independent commit/ref reads and `git ls-remote` confirmed the remote branch.
- Original local implementation commit
  `05333b6682be0cf5125568503bdd0175b8212121` and its status-only successor
  remain preserved on `codex/dashboard-split-payment-profit-local-20261124`.
  The main task checkout adopted remote `6af60231...` with a clean worktree.
- [Draft PR #2121](https://github.com/AMASI-SA/AMASI-SA/pull/2121) is open against
  `hotfix/prod-snap-meta-final`; CI was running at the preceding source checkpoint.
  Read the exact current PR head for the enclosing source commit containing this
  status update. This document does not embed its own commit SHA, and this update
  is not an intent-only Protocol-v5 B commit.
- Production changed by this task: **no**. No merge, shared `/app` change, release
  lease, publication, settings write, tariff change, sync, backfill or accounting
  post has occurred.

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
| Card 145.86 + internal wallet 33.01 | 4.21 | 0.63 | 4.84 |
| Mada 329.40 + distinct mada 198.72 | 7.28 | 1.09 | 8.37 |
| Combined | 11.49 | 1.72 | 13.21 |

For the canonical dashboard fixture with full sales 706.99, product cost 20,
and no advertising/shipping costs, profit is 673.78, internal wallet 33.01,
electronic principal 673.98, and expected transfer/electronic net 660.77.

## Historical implementation verification

Original positive cases first reproduced the split-payment block. Cash-basis
tests also failed before integration: expected transfer incorrectly included
wallet principal. The frontend regressions first reproduced the generic hint.

The reviewed implementation passed **489 tests** in total. These completed
local checks remain historical evidence for the preserved implementation; final
source-head CI and the new governed build are still required:

- **300 backend tests**, exit 0, including all current payment-fee workflow
  suites, four canonical read-only cash-basis cases, 45 split-fee cases and the
  seven unchanged legacy same-rail fee cases.
- **189 frontend tests in seven suites**, exit 0, including every current
  payment-fee workflow suite and executive-profit arithmetic. Repeated under
  the pinned Node 22.23.2/Yarn 1.22.22 release toolchain: 189 passed, exit 0.
- Python syntax checks and `git diff --check`, exit 0.
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

## Historical build evidence

The governed Node 22.23.2/Yarn 1.22.22 frozen-lockfile installation, two-pass
frontend build and `verify_frontend_build.py` passed for original implementation
commit `05333b6682be0cf5125568503bdd0175b8212121` (exit 0). The local build used
task-owned dependencies preseeded from an existing cache; it was not the
clean-clone adapter rehearsal. The borrowed symlink was removed before the copy,
and other tasks' caches/checkouts were not changed.

- Frontend artifact tree:
  `59de118c0e3fc1da1f837d09335725fb24ccd2898de28551f71271d9e756740b`.
- Reproducibility proof:
  `c1c14b1074e0b44efd0ab0b50bbe87eab0b5f396675de794f709db405f084e1f`.
- Both passes retained the same build-meta SHA256:
  `d6b7e94b78a839c539c170020b7217820e0c166921d2e66cf9ceab9e1d0460b2`.

The new final remote source A must be rebuilt and verified before freezing a new
intent-only B. The historical artifacts are not the release identity for that
new source SHA.

## Published-base evidence and shared release coordination

The initial dashboard diagnosis observed live frontend source
`a611477be8d52b18f9ad851f44facf9c78be2905`. Later
[PR #2118 publication evidence](https://github.com/AMASI-SA/AMASI-SA/blob/65b65d107b59ea9ba83e4a891bf24163dacef9f3/docs/operations/OPERATIONAL-COST-SOURCE-DIAGNOSTICS-20261124/STATUS.json)
reports a newer platform publication:

- Source: `6e25a762b17ad87ce8efeb37f2952a478ae9427b`.
- Intent-only B: `a0f5bd77f91b85b2d75885aa4173c4e48aa16ad7`.
- Merge/deployment and current production branch J:
  `c6d05c7814e27e2f22a8c82b5bfe4cf177c17fbe`.
- Runtime ID:
  `rg5-b7cabd1d3fb44fd96dee501e461a869b40872100213fd64c55665539fe9ab97e`.
- Platform version `eeecd3f`, reported finished 24 November at 23:41 Riyadh.

That immutable PR #2118 record still described its original three-probe verifier
as running. A later PR #2120 continuation observation at 21:01 UTC reported an
inactive shared guard. These are other tasks' recorded observations; this task
has not yet independently confirmed the current shared guard or final live
identity. The actual original `/app` guard status must be read before any shared
workspace mutation or release rehearsal. Preserve every other owner's lease.

[PR #2120](https://github.com/AMASI-SA/AMASI-SA/pull/2120) is another open candidate
with disjoint application changes and its own already frozen A/B pair. It shares
the production branch and `release/release-intent-v5.json`. The shared production
intent and publication must be coordinated serially. Root must coordinate the
serial merge/publication order and re-read J before finalizing this release.
If the published base or source changes, rebuild and freeze from the new exact
source; do not rewrite another candidate's reviewed intent or bypass validators.

## Release state and exact next actions

Upload, merge and publication are authorized. Draft PR #2121 and the remote
checkpoint exist. Required final-head CI/review, the final-source governed build,
new reviewed intent, original adapter rehearsal and publication verification
remain pending.

1. Read the remote task branch/PR head after this status-only source commit;
   verify its tree and sole-parent relationship, then fast-forward the clean
   task checkout while preserving the backup branch.
2. Confirm serial ownership with PR #2120 and read the actual current production
   ref and original shared guard. Proceed with shared work only when the guard
   reports inactive and the reviewed release order is unchanged.
3. Follow `AGENTS.md` and `docs/PRODUCTION_RELEASE_PROTOCOL.md`: build the exact
   final source A with the governed toolchain, validate its candidate, and create
   B changing only `release/release-intent-v5.json`. Re-read exact-head CI and
   review evidence before normal merge; preserve A/B ancestry.
4. Complete the original clean-clone adapter rehearsal and original Release
   Guard preparation/prepublication steps. Publish once through the authorized
   production workflow after its concrete checks pass.
5. Require a newer explicit deployment success and all three original verifier
   probes with the intended source/runtime identity, then inactive guard status.
   Check current executive-dashboard payment completeness and profit afterward.

Root owns PR/continuation-ledger updates, final source adoption, intent freeze,
merge and publication. This status update changes only this document and posts
no external comments. Browser diagnosis completed before its transport
disconnected during cleanup; no post-release visual claim is made. Other task
checkouts and the inherited tracked release intent remain untouched.
