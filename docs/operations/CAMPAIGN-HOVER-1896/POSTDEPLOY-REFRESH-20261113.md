# Post-deployment campaign snapshot refresh — source only

Base: 8d25f7a6fa9a310b319b753c32457e70987f7c51. Prior live guard verification passed; financial matching still has unresolved cases.

Confirmed defect: both pages watched only the campaign-ID set. A new order in the same campaign did not refetch the report. Backend TTL alone cannot refresh retained component state.

Fix: a shared bounded hook observes order IDs, status, amounts, quantities and totals; a new order revision gets a new tenant-scoped server snapshot. Repeated identical lists coalesce, old responses cannot overwrite the latest state, and no request is triggered on hover. A three-minute freshness bucket is noticed only through existing visible order refresh; no new polling/cron is added.

Tests: same campaign 30 to 31; unchanged lists; no independent polling; quantities/cancellations; delayed result; tenant isolation and request coalescing. Exact CI must pass before readiness.

Unresolved separately: TikTok visible name but no trusted campaign ID; Google campaign/account mapping; screenshot timezone America/Los_Angeles conflicts with earlier owner description America/New_York; missing Snap account-local spend. Do not guess campaign IDs from names/products or change account timezones. Read-only live evidence is needed.

No production merge/deployment, /app or release lease, provider sync, database or accounting/ad write. Preserve the accepted partial-spend policy. This patch does not claim the attribution/timezone issues fixed.
