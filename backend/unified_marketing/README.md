# Unified Marketing Data Contract

`unified-marketing-data-v1` is the provider-neutral, read-only boundary for
paid marketing reporting in Mezan. Provider implementations own ingestion and
normalization; pages and future Decision Intelligence consumers read this
contract instead of provider collections or provider-specific response keys.

## Canonical hierarchy

| Contract level | Snapchat V2 | Meta V2 | TikTok V2 | Google Ads V2 |
| --- | --- | --- | --- | --- |
| `account` | Ad Account | Ad Account | Advertiser | Customer |
| `campaign` | Campaign | Campaign | Campaign | Campaign |
| `ad_group` | Ad Squad | Ad Set | Ad Group | Ad Group |
| `ad` | Ad | Ad | Ad | Ad |

The adapter retains the native level in `entity.provider_level`; consumers use
only the canonical `entity.level` for hierarchy behavior.

## Metric boundary

- Delivery: spend, SAR-converted spend, impressions, views, clicks, CTR,
  exact-window reach/frequency, and video completion.
- Platform outcomes: view-content, add-to-cart, checkout, billing, purchases,
  revenue, and ROAS.
- Commerce outcomes: attributed Salla orders, revenue, ROAS, and attribution
  scope.
- Quality and lineage: sync/coverage state, source fact count, reconciliation,
  adapter, source version, collection, and explicit provider metric mappings.

Missing or incomplete data is `null` with `partial`/`unavailable` coverage. It
must never be converted into a confirmed zero by an adapter or UI.

Reach and frequency are non-additive. An adapter may expose them only for the
exact provider TOTAL window that produced them. Daily or hourly frequency must
never be summed across a longer period.

## Adapter rules

1. Provider clients and fact storage remain inside their provider V2 package.
2. An adapter maps provider facts into `UnifiedMarketingReport`; it does not
   call provider APIs, write provider data, or execute campaign mutations.
3. Salla reads are read-only and are mapped into typed, currency-aware commerce
   fields. Direct, foreign-platform, and ambiguous orders are not distributed
   to campaigns.
4. Mutation workflows remain separately governed (preview, approval, execute,
   read-back verification, and rollback).
5. Dashboard and AI consumers must not import provider files. After cutover,
   they consume this contract only.

`unified_marketing.gateway` is the only read entry point for cross-platform
consumers. Provider readers live behind that gateway. Dashboard first records
a fail-closed shadow comparison: its existing source remains authoritative,
and Decision Intelligence stays ineligible until the Production comparison is
explicitly accepted. `cutover_ready` may become true only for a closed period
whose Unified projection is complete and reconciled to Snapchat's matching
provider TOTAL window. The preferred acceptance basis is an exact complete V1
match. If V1 itself is incomplete, the response records the explicit
`provider_reconciliation_fallback` basis instead of fabricating a V1 amount.
Open periods can never pass through this fallback.

Meta V2 follows the same boundary without calling Graph from Unified
Marketing. Native ingestion persists account and campaign daily facts plus
dedicated hierarchy/settings snapshots, ad-set/ad daily facts, and per-level
coverage manifests. The Meta reader requires exactly one selected connected
account, the account timezone, every requested local date, a complete
campaign → ad set → ad hierarchy, settings evidence, SAR amounts, and totals
that reconcile at every level. Missing projections remain partial and fail
closed; provider reads and analytical projection writes stay in native
ingestion only.

After Production acceptance, both Dashboard read paths obtain Snapchat daily,
hourly, KPI, FX, commission, and quality fields through
`load_unified_marketing_dashboard_spend`. The gateway maps the V2 projection
into the stable Dashboard snapshot; Dashboard modules do not import Snapchat
V2 storage or adapter files. The legacy Dashboard reader remains available
only to the isolated Shadow observer and rollback path.

Decision Intelligence reads aggregate entity reports and ordered daily TOTAL
facts through the same gateway. Campaign AI candidate metrics, funnel
evidence, temporal evidence, product destinations, and creative identifiers do
not import Snapchat performance collections. The bounded provider-media
preview collector remains a separate read-only adapter because fetching an
actual ad asset is provider-specific; it has no recommendation or write
authority. Child levels receive the parent campaign's
Salla/profitability context only; the adapter never invents Salla attribution
for an Ad Group or Ad. The old Snapchat AI readers remain named rollback
functions and are used only by the post-cutover observer.

Campaign AI Shadow compares only the overlapping V1 rollback entities and
retains every metric drift in its diagnostics. An exact V1 overlap match is
preferred. Exact provider TOTAL facts may use the explicit
`provider_total_facts_fallback_v1_observer_drift` basis. When Snapchat has no
daily TOTAL row for a hierarchy level, the page-equivalent, fully synchronized
V2 hourly facts may use
`complete_unified_v2_fallback_v1_observer_drift`, but only when every compared
row is complete, conversion-time, sourced through the Unified V2 adapter, and
its lineage is one of the approved V2 fact collections. Unknown, incomplete,
or impression-time sources fail closed. This does not rewrite V1 or hide
metric drift.

The acceptance proof always uses the last closed calendar day in the
advertising account timezone. It never rolls to a newly opened local day with
zero or provisional evidence. Live AI reads use the current account-local
window after cutover; only the immutable observer proof is anchored to the
closed day.

## Decision gate

Every report defaults to:

```json
{
  "decision_eligibility": {
    "eligible": false,
    "reason": "shadow_sync_not_accepted"
  }
}
```

No adapter may enable this flag. A separate acceptance gate can do so only
after provider reconciliation, hierarchy performance coverage, Salla
attribution checks, and Shadow Sync acceptance have passed in Production.

`GET /api/integrations-v2/snapchat-v2/unified-readiness` is the fail-closed
acceptance proof for Snapchat V2. With no dates it evaluates the last closed
account-local day. It requires a complete reconciled account contract, complete
campaign/ad-group/ad reports, a complete non-truncated Salla comparison, and an
explicit Decision Intelligence isolation guard. A passing proof makes the
contract data ready to consume; it does not connect or enable Decision
Intelligence.

Meta reports use `meta_shadow_not_accepted` until their native-versus-Unified
comparison passes for the same closed account-local window. The comparison
also requires exact Salla campaign attribution, complete profitability,
freshness after the local period end, and Decision Intelligence isolation.
Passing adds recommendation-only evidence to Phase 5; it never grants approval
execution, schedules automatic execution, or performs a provider/database
write from the decision path.
