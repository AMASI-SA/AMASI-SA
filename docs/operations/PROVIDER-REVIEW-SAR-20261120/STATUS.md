# Provider review currency consistency

Task branch: codex/provider-review-sar-consistency-20261120
Base: d58d68eaabf4995c088aca884826373c3b45805f

Root cause: workspace provider cards/drill-down use provider_balances_sar=True,
but review_groups used the default native-currency report. SAR-only saved fixed
fees then correctly reject native QAR/KWD, creating false missing-fee reviews.
Review now uses the same saved SAR valuation path whenever providers are scoped.
No live source fetching, financial write, rate override, or extra report pass.

Validation: 78 tests passed across the new regression and existing configured
fees, known subtotals, SAR and fee-display suites. Added separate historical
review preservation regression afterward; final focused suite has five cases.
Synthetic SAR/QAR/KWD with configured fee, missing FX preservation and historical
reconciliation preservation are covered. Input snapshot stays unchanged.

Production changed: no. Deployment, live recalculation and live acceptance have
not been performed. Historical principal reconciliation remains a separate
issue; this patch must not release held claims. Position-page gross/net display
and historical PayPal claims remain follow-up scope. No release intent frozen.
Next: PR checks, governed release preparation, and post-release verification.
Issue 1006 is at its comment limit; this document and task PR carry the handoff.

## Unified position and excluded-principal disclosure checkpoint

Extended scope: provider rows in financial position reuse the account-card net
component. The SAR provider headline totals the same visible positions from the
same context snapshot; positive balances only, with partial coverage labeled.
Provider summary exposes paid principal not included in known net, separately
from net money, subtracting documented refunds/allocated gross and folding alias
observations. Missing FX/aggregate evidence cannot claim complete coverage.
Cards, position rows and order drill-down show the excluded amount before fees;
order counts remain gated by full movement/report access. No new network request
or financial mutation; historical holds remain intact.

Validation: 83 backend tests passed, including 4 additional cases for excluded
principal, known net/opening independence, missing FX, and refund/settlement
exclusion. Frontend coverage includes position/card/headline parity and separate
pending disclosure. Production still unchanged by this task. Public build-meta
continues to report source a2ce4b6fd16d914f8650ddcd85c1e8ac0d98006a.

Deployment blocker: available cloud browser profiles have no saved login for
Emergent (both domain_count=0). Need authenticated official project terminal to
inspect shared release guard before any rehearsal/publish. No lease was created
or cleared, no release intent frozen and no production branch changed.
Next: complete frontend validation, preserve checkpoint on PR2031, restore
Emergent sign-in, inspect shared guard and current source, then governed A/B
build + intent-only commit + release checks, publish and three-probe verification.
