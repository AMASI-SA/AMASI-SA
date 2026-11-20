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
