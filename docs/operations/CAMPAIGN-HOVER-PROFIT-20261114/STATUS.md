# Campaign hover costs, operating allocation and profit

Task: `CAMPAIGN-HOVER-PROFIT-20261114`

Repository: `AMASI-SA/AMASI-SA`

Task branch: `codex/campaign-hover-profit-20261114`

Reviewed production base J: `4c623c640fb63ac3c138fa4a9cabb20e2b39dca7` (PR #1919).
The initial work began on PR #1918 merge `23cc06da003c18c0f3fc6c2e749aad1326666166`
and was fast-forwarded to J before creating a source checkpoint. None of PR #1919's
source paths overlap this feature. The latest exact task SHA is recorded in the
task PR because this document cannot contain its own commit identity.

## User contract

Add three rows to the existing campaign card:

1. Direct total costs: the same product/component/selected-option, shipping,
   payment, and attributable advertising bank-fee authorities as the executive
   profit summary. Advertising spend and allocated operating expenses are not
   included in this subtotal.
2. Operating expenses: today's executive operating total divided by today's
   executive gross SAR sales, multiplied by the campaign's existing sales. The
   ratio is dynamic, not a fixed 3% or the screenshot's rounded 3.09%. Retain the
   full ratio until rounding the campaign amount to halalas. Display both the
   allocated amount and percentage, with the store-day basis/date visible.
3. Net profit: campaign sales minus campaign ad spend, direct costs and allocated
   operating expenses. Keep losses negative. Incomplete costs must show
   `التكلفة غير مكتملة`; incomplete profit must not display a numerical result.

The latest user instruction includes proportional operating expenses and
supersedes the earlier request to exclude them. The reference executive period
is today's store day in `Asia/Riyadh`; campaign commerce/spend retain their
existing advertising-account day. No saved allocation or accounting write is
performed. Existing campaign scope, counts, time windows, source matching, ad
spend, CPA, ROAS, cache and refresh behavior are preserved.

## Implementation

- `backend/order_engine/campaign_hover.py` retains the exact qualifying campaign
  order cohort and includes the raw/promoted fields needed for cost verification.
- `backend/order_engine/campaign_hover_profit.py` reuses the read-only cost
  authority, bounded catalog context, current option evidence, shipping/payment
  calculator and campaign-specific advertising bank-fee policy. One distinct
  order cohort is priced once for the visible campaign batch.
- `backend/order_engine/campaign_hover_operating.py` reads the executive status
  cohort and inferred-date settings; uses shared SAR conversion, Employee OS
  salary history/leave rules and V2 recurring obligations. Legacy rent/prepaid
  totals replaced by V2 in the executive summary are not added twice.
- Finite time, row and line limits fail closed. Missing product price, unmatched
  current lines/options/variants/parents, absent currency conversion, ambiguous
  bank-fee account identity, unresolved refunds or unavailable operating basis
  cannot certify net profit. Valid Salla base cost fallback remains permitted by
  the executive completeness rule.
- `CampaignHoverBadge.jsx` shows the three rows, provisional-spend warning and
  operating basis. Measured height, viewport bounds and pinned keyboard/pointer
  scrolling keep the expanded card usable. No new fetch, polling or sync path.
- Dedicated backend/frontend acceptance tests cover actual shared authorities,
  cost gaps, currency, fee normalization, refunds, proportional allocation,
  losses, current-versus-promoted identity and viewport interaction. The existing
  campaign acceptance workflow includes these suites and their dependencies.

## Independent review and local verification

Independent backend review checked executive operating parity, isolation,
read-only behavior and missing-data handling. Its findings about native line
coverage, Meta `act_` account aliases and work admission before copying were
fixed. The final identity extension additionally checks nested variants,
variant removal, parent identity and conflicting catalog aliases. Independent
review confirmed both final identity findings resolved, with no remaining
blocker in the reviewed financial scope.

Local frontend acceptance: 9 suites, 50 tests passed, exit 0. Existing React
test-environment/deprecation warnings remain; no frontend test failed.

Final integrated backend acceptance: **188 passed, exit 0**, across 12 suites
in 14.22 seconds. The new cost and operating suites account for 51 passing cases.
Two existing Pydantic deprecation warnings remain. An initial run found the old
query budget predating the financial sources; the test now exercises complete
successful calculations and proves the identical exact 30-read map for both 1
and 20 distinct campaigns. It checks each cost/operating source explicitly, with
the two intended commerce/executive order reads and separate settings reads.
The full existing daily suite also passed independently (28 cases).

The 12 backend suites are the exact list in
`.github/workflows/campaign-hover-source-acceptance.yml`, executed from `backend`
with `python -B -m pytest --noconftest -p no:cacheprovider -q --tb=short` and
`PYTHONDONTWRITEBYTECODE=1` plus a synthetic test-only JWT secret. The 9 frontend
suites are that workflow's exact `--runTestsByPath` list, run with `CI=true` using
`python scripts/frontend_release_toolchain.py exec -- yarn --cwd frontend test
--watchAll=false --runInBand --runTestsByPath ...`. `git diff --check` passed.

Local commands use the governed Node 22.23.2/Yarn 1.22.22 toolchain for frontend,
and an existing Python environment read-only for focused offline pytest. Exact
source CI, real-Mongo acceptance, full frontend/security gates and v5 clean-clone
rehearsals remain mandatory; mock-backed local tests do not replace them.

## Release and continuation

Production changed by this task: **no**. No `/app` mutation, lease or publish has
started for this feature. PR #1919 has its own independent release operation;
read the shared guard and current production branch before any future `/app`
operation. Preserve its lease and unrelated workspace/editor state.

No order, payment, settlement, product configuration, ad account, advertising
budget/bid, provider sync, scheduled refresh or business-data mutation is part
of this task. Paid Emergent chat is not used.

Issue #1006 has reached its comment limit. This document and the dedicated task
PR are the continuation ledger; do not repeatedly try to append there.

First source checkpoint: `2c26dea8487bf6779a6eb01a65771f175e83b0f4`, PR #1920.
Its governed candidate built and independently validated, but two CI jobs found
the new shared financial test fixtures require `mongomock-motor`, which their
old install commands did not include. The campaign and Snapchat workflows now
install the repository's existing pinned mock packages (`mongomock==4.3.0`,
`mongomock-motor==0.0.36`). No test was skipped, removed or weakened; no production
dependency or runtime source changed for this correction. The first candidate
must not be used after this new source checkpoint; a fresh exact-source build is
required. Other CI results remain evidence for their precise source only.

Next safe action: push the CI dependency correction as the new source A on PR
#1920, then wait for all applicable exact-A gates. Independently validate
the governed candidate intent, create intent-only B, pass exact-B gates, ordinary
merge with B's exact tree, and follow the authorized v5 deployment protocol. Do
not deploy this feature using the inherited PR #1919 intent.
