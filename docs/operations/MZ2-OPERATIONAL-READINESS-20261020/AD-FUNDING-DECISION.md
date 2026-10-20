# Owner funding clarification — 2026-10-20

The owner explicitly described TikTok as supporting prepaid wallet funding and
postpaid spending when the wallet is empty. Do not infer funding modes for the
other four selected accounts from this example.

The native ad cost-settings page remains the source of configured withdrawal
currency, conversion and bank fee settings. It contains no funding-mode field.
Defaults shown on that page are not evidence of owner-confirmed settings.

## Verified implementation gap

`operational_balance_sources.hybrid_policy` accepts only `explicit_split`.
`operational_balance_engine.reconcile` splits cumulative daily spending by
`wallet_fraction`, regardless of the available prepaid balance.

A fresh pure synthetic reproduction used no opening wallet, confirmed spend
200 SAR, hybrid fraction 1. Result: wallet -200, payable 0. That is existing
fixed-split behavior, not the owner's described empty-wallet/postpaid behavior.
No database or real account was written by this reproduction.

Do not simply choose hybrid, invent a fraction, or reinterpret existing
confirmed days under a new allocation rule. Preserve the existing fixed-split
contract for accounts that actually use it.

## Final payment decision — supersedes separate-only restriction

The owner subsequently explicitly approved: pay outstanding debt first and add
the excess to the wallet. With debt 100 and payment 200, debt becomes zero,
wallet becomes 100, and bank outflow is exactly 200. A payment below the debt
reduces debt only. With no debt, the whole payment funds the wallet.

The earlier restriction against overpayment is superseded and removed.
Hybrid generic payments now require allocation of min(payment, outstanding
debt), including explicit allocation payloads, before any remainder becomes
wallet credit. Existing explicit wallet funding and exact settlement endpoints
retain their meanings. The normal hybrid payment label explains debt-first
and excess funding.

Implementation must distinguish source spend from actual bank payment; maintain
nonduplicating cumulative daily updates and immutable closed days; account for
the timing of funding without retroactively rewriting earlier debt; preserve
currency and tenant isolation; and test zero/partial wallet, repeated refresh,
funding after debt, settlement and concurrent payment before live activation.

Production unchanged. The payment-priority guard and label are implemented
locally. Automatic allocation of advertising spend when the wallet is depleted
remains unimplemented; that requires the chronological evidence described above.
