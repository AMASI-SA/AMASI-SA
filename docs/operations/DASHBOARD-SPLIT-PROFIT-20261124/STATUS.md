# Dashboard split-payment profit correction

## Scope and source integration

- Task: `DASHBOARD-SPLIT-PROFIT-20261124`.
- Repository: `AMASI-SA/AMASI-SA`.
- New release branch: `codex/dashboard-split-profit-release-20261125`.
- Preserved original task branch: `codex/dashboard-split-payment-profit-20261124`.
- Production target: `hotfix/prod-snap-meta-final`.
- Original base J: `c6d05c7814e27e2f22a8c82b5bfe4cf177c17fbe`.
- Current production/integration base J2:
  `f199a471f8bc6e05b475bb9b65a12107cbdc946b`, the normal merge of PR #2120.
- The user explicitly authorized upload, merge and publication on
  **25 November 2026, Asia/Riyadh**: "نعم ارفع وادمح وانشر".
  The earlier automatic approval rejection was resolved by that authorization.
  The later Git CLI push lacked a credential (`could not read Username`);
  authorized source checkpoints use the connected GitHub Git Data API.
- Original reviewed remote checkpoint:
  `6af60231b7a89e789c1f26600dcc8ad60b0589a0`, tree
  `210fe97c253e84dcd6798c6d9758cc2f4fe9289e`, exactly equivalent to preserved
  local checkpoint `764daef45a768f28bbc13cd103788aaf6316604a`.
  All 10 blob IDs, remote tree, commit and branch ref were independently checked.
- The subsequent source/status checkpoint is
  `9138afc446066d920d4373415f00e78b08520854`, tree
  `b210b92cb4d8f3caf5ae65f2fa1fa9f9a7cde0f8`.
  Its build work is historical following the production-base advance.
- Original implementation commit
  `05333b6682be0cf5125568503bdd0175b8212121` and its local successor remain
  preserved on `codex/dashboard-split-payment-profit-local-20261124`.
- [Draft PR #2121](https://github.com/AMASI-SA/AMASI-SA/pull/2121) remains the
  historical reviewed dashboard candidate. A new release PR from the new branch
  will supersede it; do not merge the old candidate. The new PR number is not
  known at this source checkpoint.
- The new enclosing source commit has **one parent, J2**, and exactly the ten
  dashboard paths described below. Read the remote release-branch head for its
  full SHA; this document does not embed its own commit SHA. It is source A2,
  not an intent-only Protocol-v5 B commit.
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

Additional build work was bound to source
`9138afc446066d920d4373415f00e78b08520854` while the production branch advanced.
That evidence is now historical too. The new single-parent source A2 must be
rebuilt and verified before freezing a new intent-only B2; earlier artifacts do
not identify the new source or its runtime release.

## Why a fresh linear source is required

PR #2120 merged at 2026-11-24T21:23:10Z as
`f199a471f8bc6e05b475bb9b65a12107cbdc946b`, advancing production while the
dashboard source was being built. Its 28 changed paths have no overlap with
the ten dashboard paths from J to `9138afc...`.

A trial merge with first parent J2 and second parent `9138afc...` failed the
original `_assert_candidate_source_transition` check: the complete candidate
history included an intent change. That trial is not a releasable source and
was not published. The validator remains unchanged.

The replacement source has J2 as its sole parent. It overlays the exact nine
reviewed application/test/workflow blobs from `9138afc...`, plus this updated
status document, onto J2. It does not include the old source as a second parent
or rewrite either preserved branch. Every PR #2120 path and its inherited
`release/release-intent-v5.json` blob
`ba1de70733b798225dd8aa39a4c7569b55df3a4c` are preserved.

The only changed paths relative to J2 are:

- `.github/workflows/prod-dashboard-v2-ad-fx-payment-fees.yml`
- `backend/dashboard_payment_fees.py`
- `backend/dashboard_split_payment_fees.py`
- `backend/legacy_dashboard_reader.py`
- `backend/tests/test_dashboard_split_payment_cash_basis.py`
- `backend/tests/test_dashboard_split_payment_fees.py`
- `docs/operations/DASHBOARD-SPLIT-PROFIT-20261124/STATUS.md`
- `frontend/src/components/ProfitSummaryCard.paymentFees.test.jsx`
- `frontend/src/lib/paymentFeeCompleteness.js`
- `frontend/src/pages/AdvancedDashboard.paymentFees.test.jsx`

The integration uploader independently calculates the desired Git tree from J2
and these exact blobs, requires the remote tree to match, and confirms the
production ref is still J2 before creating the previously absent release branch.
No local checkout edit, old-branch rewrite, intent change, PR creation, merge or
publication is part of this source upload.

## Published-base evidence and shared release coordination

The initial dashboard diagnosis observed live frontend source
`a611477be8d52b18f9ad851f44facf9c78be2905`. Later
[PR #2118 publication evidence](https://github.com/AMASI-SA/AMASI-SA/blob/65b65d107b59ea9ba83e4a891bf24163dacef9f3/docs/operations/OPERATIONAL-COST-SOURCE-DIAGNOSTICS-20261124/STATUS.json)
reports a newer platform publication:

- Source: `6e25a762b17ad87ce8efeb37f2952a478ae9427b`.
- Intent-only B: `a0f5bd77f91b85b2d75885aa4173c4e48aa16ad7`.
- Reported PR #2118 merge/deployment J (now a production ancestor):
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

[PR #2120](https://github.com/AMASI-SA/AMASI-SA/pull/2120) is now merged into
production as J2. Its warehouse changes and newer tracked intent are retained in
the new dashboard source. The current published identity and that release's
completion still require independent confirmation; a GitHub merge alone does
not establish a successful publication or an inactive shared lease.

Root must confirm the current publisher's state before this task proceeds with
shared-host work. If the production or published base changes again, rebuild
from the newly reviewed exact source/base and freeze a fresh intent. Preserve
other owners' leases and keep the original release validators unchanged.

## Release state and exact next actions

Upload, merge and publication remain authorized. Original PR #2121 and its
source history are preserved. The replacement source A2, new release PR, exact
source-head CI/review, governed build, new intent-only B2, original adapter
rehearsal and publication verification form the remaining release path.

1. Read and verify the new remote release-branch commit: sole parent J2, exact
   ten changed paths, nine original implementation blobs, updated status, and
   unchanged inherited intent. Adopt it in an isolated or clean task checkout
   while preserving both earlier branches and all unrelated local work.
2. Create the replacement reviewable PR and link it to superseded PR #2121.
   Confirm current production J2, current published identity, and the actual
   original shared guard before any shared workspace action. Wait for the
   active publisher if its lease remains active; do not clear or reuse it.
3. Follow `AGENTS.md` and `docs/PRODUCTION_RELEASE_PROTOCOL.md`: run the original
   candidate-transition check, build exact A2 with the governed toolchain,
   validate its candidate, and freeze B2 changing only
   `release/release-intent-v5.json`. Require exact-head CI/review and a normal
   merge preserving A2/B2 ancestry.
4. Complete the original adapter rehearsal and Release Guard preparation and
   prepublication checks. Publish once through the authorized production flow.
5. Require a newer explicit deployment success and all three original verifier
   probes with the intended source/runtime identity, then inactive guard status.
   Check current executive-dashboard payment completeness and profit afterward.

Root owns PR/continuation-ledger updates, source adoption, intent freeze, merge
and publication. This integration preserves prior branches and other tasks'
source/intent. No post-release dashboard or live-profit claim is made.
