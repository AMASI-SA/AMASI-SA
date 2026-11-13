# Post-deployment campaign hover follow-up — verified source checkpoint

Date: 2026-11-13. This document lives on the isolated ops branch only; do not merge that branch or its patch workflow into production.

## Recoverable source and production boundary

Repository: AMASI-SA/AMASI-SA.
Production inspected: hotfix/prod-snap-meta-final @ 8d25f7a6fa9a310b319b753c32457e70987f7c51.
Task branch: codex/campaign-hover-live-refresh-20261113.
Exact tested source: 37e1ad91b1dd9b1698f96d520f92a7b500609a80.
Draft PR: #1907. No merge, release intent freeze or live deployment in this task.
Prior #1905 live release passed the owner's three identity probes; that proved release identity, not campaign metric correctness.

## Confirmed stale-card defect and repair

OrdersV2 and homepage LatestOrders fetched only when the sorted campaign-ID set changed. A new order for the same campaign did not change that key. Backend cache expiry cannot initiate a missing client request, so a stale count could persist.

The source now connects both surfaces to useCampaignHover. A bounded fingerprint of visible order identity, time, status, amount, quantity and totals distinguishes changed Salla order snapshots. It carries a non-authoritative cache revision to the existing authenticated endpoint. Tenant identity still comes from authentication. A new revision does not reuse the prior cached count; concurrent equal revisions coalesce. Late old responses cannot replace a newer component snapshot. Repeated equal list refreshes do not fetch again. A three-minute freshness bucket can be noticed through the existing order-reader refresh only. No new interval, cron, sync cadence, provider call or hover/focus/tap fetch was added.

Modified source: frontend hook, service, OrdersV2, AdvancedDashboard; backend cache/route revision. Regression tests and the existing exact-source acceptance workflow were extended. Existing partial-hour spend policy, unknown values, attribution logic and release guards remain unchanged.

## Verified tests

Campaign hover source coverage run 43489513316, exact HEAD 37e1ad91b1dd9b1698f96d520f92a7b500609a80:
- source-coverage job132106647745: SUCCESS; 55 pytest cases passed, covering provider response handling, hourly spend, Salla reader and revision cache.
- frontend-coverage job132106647756: SUCCESS; 4 suites /16 tests passed, including same campaign 30 ->31, no independent polling, unchanged lists, delayed response, amount/quantity/cancellation revisions and no guessed request without a trusted campaign ID.
- UI tests emit existing React act/deprecation warnings; no test failures.
These are repository test fixtures, not a live financial reconciliation or proof that every mirrored Salla order is up to date.

## Sanitized read-only live findings

Direct Salla reads of examples from the user's screenshots established two distinct input shapes; no customer identifiers or payloads are stored here:
- TikTok: explicit utm_source=tiktok, medium=paid and a readable utm_campaign label, but no numeric platform campaign ID in the returned order. Commerce grouped by the explicit UTM label must not be confused with a verified platform-ID spend join. A unique verified catalogue/alias link is required for that join; do not guess from product title.
- Google: utm_source=google, but the campaign slot contains a generic click-origin placeholder and gad_source=1, not an actual campaign name or ID. This sample cannot be assigned to a specific ad campaign from these fields alone. Do not extrapolate that every Google order has this shape.
- A read-only Snapchat Marketing query through the connected reporting account found the screenshot's campaign under a USD account whose authoritative ad_account_timezone is America/Los_Angeles. The displayed Los_Angeles timezone is not itself the bug. The earlier New_York assumption does not apply to this checked campaign. Do not change the ad account timezone or relabel saved Riyadh totals to make the card complete. No account IDs, business campaign names or customer data are committed in this checkpoint.

## Remaining work / next safe actions

1. Check all exact-source CI and current PR/production refs before release work. The accepted refresh patch has not been deployed.
2. Complete native Google/TikTok campaign/account resolution where IDs exist; design a distinct truthful Salla-label commerce scope where only UTM labels exist. Preserve no-name-guessing for spend and no invented campaign for Google placeholders.
3. Inspect the currently running Snapchat synchronizer and account-local snapshots for the verified Los_Angeles account. Determine whether the new writer is on that path and whether source-day facts/FX have been populated. This remains unproven; do not claim another redeploy alone fixes it.
4. Reconcile a real new eligible Salla order to the same campaign's count/quantity/sales after deployment, and measure read latency. Retain independent Salla commerce and advisory partial-hour spend policy.
5. Follow unchanged V5 source A + intent-only B and lease protocol before any production merge/publication. Do not modify the prior release or active lease.

Production changed: NO. No /app access/mutation, lease creation, manual sync/backfill, database write or advertising/accounting change. Only source/ops GitHub work and read-only Salla/Snapchat inspection occurred. Issue1006's prior comment-capacity failure makes this document and PR1907 the continuation record.
