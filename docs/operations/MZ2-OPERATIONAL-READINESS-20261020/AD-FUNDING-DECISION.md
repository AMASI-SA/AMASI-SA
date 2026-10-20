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

## Payment decision confirmed

The owner explicitly chose separate wallet funding and debt settlement.
With debt 100 and a new wallet funding 200, wallet becomes 200 and debt remains
100. A separate debt settlement 100 then clears debt without changing wallet.
There is no automatic netting of the payment against old debt.

Implementation must distinguish source spend from actual bank payment; maintain
nonduplicating cumulative daily updates and immutable closed days; account for
the timing of funding without retroactively rewriting earlier debt; preserve
currency and tenant isolation; and test zero/partial wallet, repeated refresh,
funding after debt, settlement and concurrent payment before live activation.

Production unchanged. No new funding algorithm has been implemented or enabled.
