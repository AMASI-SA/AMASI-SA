# Dashboard advertising bank-fee reconciliation

## Recoverable source

- Task: `dashboard-ad-bank-fees-20261117`.
- Repository: `AMASI-SA/AMASI-SA`.
- Branch: `codex/dashboard-ad-bank-fees-20261117`.
- Reviewed base: `b439b683f26ffdd61b7b9de80315ea21747d135c`.
- Implementation commit: `8a37fdb811618a6512e20f237d47d78cad317269`.
- Verified source tree: `3fed845518477b6baa58281cfa94cf05db07c0b4`.
- This status is a documentation checkpoint after the implementation. The PR
  records its exact head SHA and remote/CI state.

## Authorization and completed behavior

The user requested that bank commissions on Snapchat spending be deducted in
the advertising table, with the reported daily profit becoming SAR 25,255.12.
This checkpoint implements that calculation. It does not authorize a new
production merge or publication, change advertising settings, or move orders
between Snapchat accounts.

Changed runtime file: `frontend/src/lib/dashboardAdsAccountResults.js`.
Regression file: `frontend/src/lib/dashboardAdsAccountResults.test.js`.

- Join the existing executive `ads_v2.bank_commissions` authority to the
  matching Snapchat account, or Meta/TikTok provider, once.
- Include the verified fee in total costs and subtract it from daily profit;
  preserve spend, sales, orders, CPA, ROAS, customer-payment fees, attribution,
  and the existing compact UI.
- Validate provider/account identities, coverage, matching spend, commission
  rate, charged amount, fee-subject spend, and canonical daily totals. Missing
  or contradictory fee evidence cannot certify a complete profit.
- Apply the same fee contract to full-cohort totals, legacy totals, and both
  half-day totals. Preserve server cohort rounding of customer-payment fees.
- Keep direct orders free of advertising fees. Google remains outside the
  existing executive advertising-bank-fee authority.
- Use verified hourly spending for half-day fee allocation. For Snapchat,
  use original native precision and the canonical FX rate for both USD and
  SAR accounts. The morning charge is rounded cumulatively and the elapsed
  afternoon receives the remaining canonical daily charge. At a full-morning
  endpoint, use the server's daily fee directly. Future afternoons remain blank.

Half-day fees are a calculation from verified hourly spending and the existing
rate, not an independent bank-statement amount. A positive provider fee across
multiple accounts is not allocated from a provider-wide hourly total. Missing
hourly/native/FX proof leaves the affected half-day profit unknown while a
proven daily profit can remain available. No hourly spending is fabricated.

## Reconciliation proved by regression

The reported case is reproduced with synthetic account identities and the
provided aggregate amounts, including two Snapchat orders without an exact
account assignment:

```text
Sales                             109,199.23
Advertising spend                  32,875.20
Products                           38,190.95
Shipping                            7,458.75
Customer-payment fees               4,784.24
Snapchat USD-account bank fee          624.57
Snapchat SAR-account bank fee           10.40
Profit before operating expenses   25,255.12
Operating expenses                  1,894.92
Executive profit                   23,360.20
```

The advertising table still excludes operating expenses. Its cost total
becomes SAR 51,068.91, excluding advertising spend. The complete cohort retains
393 orders and SAR 109,199.23 sales; the two unmatched orders are not divided
between accounts. Partial account attribution continues to withhold account
profits without suppressing the independently proved cohort total.

## Verification and review

Initial focused regressions failed against the old calculation. The first
independent review then identified four defects: incomplete canonical-total
validation, rate/fee contradictions, midpoint rounding at a zero-spend
afternoon, and SAR native-precision handling. All four were corrected and
covered before the final review.

Final command, exit 0:

```bash
CI=true PYTHONDONTWRITEBYTECODE=1 python -B scripts/frontend_release_toolchain.py exec -- \
  yarn --cwd frontend test --watchAll=false --runInBand --verbose=false --runTestsByPath \
  src/lib/dashboardAdsAccountResults.test.js \
  src/components/DashboardAdsSpendCard.test.jsx \
  src/components/DashboardAdsAccountRow.test.jsx \
  src/components/DashboardAdsPeriodsOverview.test.jsx
```

- Four suites, 215 tests passed, zero failures/skips.
- `git diff --check`: exit 0.
- Independent read-only source review: no remaining concrete blocker to a
  source checkpoint/Draft PR; reviewed exactly-once deduction, cohort rounding,
  attribution guards, and documented half-day limitations.
- The CLI push had no write credential. The GitHub connector saved the exact
  tested source tree; its tree SHA matched the local implementation before
  the remote branch was created. No force push was used.
- No backend/runtime/UI layout files changed. No financial/provider/database
  writes, new network requests, polling, or synchronization added.

## Publication and continuity

Production changed by this task: **no**. No merge, release intent, governed
release build, shared `/app` operation, release lease, or publication performed.
Unrelated courier changes on the base remain intact.

Issue #1006 is saturated: 2,500 comments and a 258,337-character body at the
latest read. The task PR and this status retain the continuation record; do
not overwrite or truncate that ledger to add this task.

Next safe action: verify the pushed task branch/PR head and associated CI.
Before any separately authorized publication, re-read `AGENTS.md`, fresh
production ref, and shared release-guard status; prepare a fresh reviewed v5
A/B source/intent and complete its gates. Do not reuse the base release intent
for this changed frontend source.
