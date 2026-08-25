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
`cutover_ready` remains false, and Decision Intelligence stays ineligible until
the Production comparison is explicitly accepted.

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
