# SNAP-REPORT-1 — root-cause and data-flow audit

Scope: `/ads-manager?provider=snapchat&tab=campaigns` on base
`a9d31c94db80b57cb558133c39effa64da92f94c`.

## The actual request path

`Layout.jsx` replaces the normal `/ads-manager` child with
`MarketingPlatformWorkspace`. The page calls `getMarketingPerformance`, which
calls `GET /integrations-v2/snapchat_ads/campaign-report`. The route delegates
to `build_account_timezone_campaign_report`; router composition applies the
provider TOTAL projection, then created-order/profitability semantics to that
final visible generation, then the truth contract and non-financial
hourly/catalog presentation wrappers.

Selected accounts come only from `mezan_integration_accounts_v2`, scoped by
`user_id`, `provider=snapchat_ads`, connected API provenance and
`mezan_selected=true`. Snapchat facts come from
`mezan_snapchat_performance_account_day_v3`, the authoritative TOTAL collection
used by `snapchat_platform_source_integrity`, and
`mezan_ads_native_entities_v2`. Salla facts come from `unified_orders` and the
current Mezan product-cost catalog.

## Nine symptoms and their root causes

| # | Symptom | Root cause on the base SHA | Corrected contract |
|---|---|---|---|
| 1 | Orders/sales changed between otherwise equivalent views | Generic `orders`, `sales_sar`, `results` and `roas` were rewritten by the backend result-source selector and again by `marketingCampaignSelectedSourceGuard`. | Commercial aliases are disabled for this report. Salla and Snapchat fields are independently named end to end. |
| 2 | An older response could appear after a newer account/range request | `marketingCampaignStaleResponseGuard` replaced an old response body with the latest successful body, while the page retained prior `data` when a new request failed. The snapshot cache was keyed only by platform. | Each request carries and verifies `request_id`; the page clears the prior generation before dispatch, ignores older completion, and leaves a failed generation empty. Snapshot consumers are cleared at dispatch and cannot hydrate financial rows. |
| 3 | Incident A: 66 campaign-matched orders while Salla total was 25 | The response exposed matched campaign counts without an independently measured selected-window Salla total or invariant. Padded-window counts were mislabeled as coverage. | The same deduplicated selected-window order set emits total, matched and unmatched. `matched <= total` is enforced; violation sets matching/Salla to `failed` and nulls financial ratios. |
| 4 | Incident B: Salla 55→66 and SAR 15,298.79→17,971.72 while Snapchat spend stayed SAR 1,060.85, making “current” ROAS rise | The profitability cache key omitted order/attribution evidence and timezone; the frontend could also combine a historical successful snapshot with a newer response. | Financial caching is disabled for this read path. Salla changes are visible as Salla facts, but current ROAS/CPA are null unless both independently timestamped source windows are complete and reconciled. |
| 5 | Incident C: `aa3cdaec-b950-4df3-b28d-c3502cd2bf7b` missing | Campaign rows were created only from performance rows. A valid entity with zero/no in-range fact row never reached the active filter. | Campaign identity is the union of selected-account entities and in-range facts. An active entity remains visible with null metrics and `data_status=outside_date_range`; true exclusions report `inactive`, `deleted`, `filtered`, or `pagination_truncated`. |
| 6 | Incident D: `8ae1e7cd-db61-41bd-af15-9f52824709d6` missing | Same performance-first identity defect as #5; the response gave no exclusion reason, so provider absence, account scope, date scope and pagination were indistinguishable. | Same union and reason contract as #5. An exact UUID search performs one bounded, user-scoped catalog diagnostic and returns exactly one of `outside_account`, `deleted`, `provider_missing`, or `source_failed`; it never invents a row or a zero. |
| 7 | Incident E: a campaign with one Salla order and SAR 132.92 sales displayed “no matching orders” for product cost | The table inferred matching from a separately cached/optionally hydrated `profitability.orders`, not from the campaign’s Salla attribution result. Missing profitability therefore looked like zero orders. | The row uses `salla_orders` for matching. One matched order with unresolved cost is `cost_incomplete`; it cannot become `no_matching_orders`. Cost, sales and profit use the same financial-order evidence. |
| 8 | A 4 September Salla order could enter a 3 September selection for a Los Angeles ad account | Salla creation timestamps were localized to the selected Snapchat account timezone. `_order_timestamp` also fell back to mutable `updated_at`. | UI/business and Salla attribution dates are always `Asia/Riyadh`; Snapchat facts retain `America/Los_Angeles` (or the account timezone). Only immutable creation timestamps are used, then `order_date` as an explicit fallback. |
| 9 | Filtering/pagination made campaigns and totals disagree or disappear silently | The base reader built campaigns from facts, filtered, paginated, then a wrapper computed profitability from the visible page while headline counts came from report-wide coverage. Frontend infinite pagination accumulated rows from multiple server pages. | Active/search filters run before pagination; page rows are not merged across request generations. Report-wide totals stay report-wide, campaign exclusion reasons are explicit, and row-limit truncation yields `partial` rather than a valid-looking zero. |

## Every displayed number after the fix

| UI number | Response field | Backend authority | Mongo scope | Time/source rules |
|---|---|---|---|---|
| Salla total orders | `totals.salla_total_orders` | `build_created_and_financial_outcomes` | `unified_orders`, exact `user_id`, bounded padded read then exact selected window | Creation day in `Asia/Riyadh`; all statuses; duplicates removed |
| Salla matched orders | `totals.salla_matched_orders`, campaign `salla_orders` | literal UTM join | same order IDs as Salla attribution | only literal `utm_campaign_id == Snapchat campaign_id`; no name/case/space normalization |
| Salla unmatched orders | `totals.salla_unmatched_orders` | total minus matched invariant | same selected-window order set | never negative; null on invariant failure |
| Salla sales | `totals.salla_sales_sar`, campaign `salla_sales_sar` | financial-status subset of the matched order set | `unified_orders` | Salla/Mezan policy; `Asia/Riyadh` |
| Product cost/profit | `salla_profitability` | current Mezan product-cost resolver | current product/cost catalog plus the same financial matched orders | no financial result cache; missing cost is incomplete |
| Snapchat spend | `snapchat_spend_sar` | direct selected-account TOTAL; campaign TOTAL for rows | provider TOTAL collection, exact user/account/date/timezone/action-report partition | Snapchat account-local date and `conversion` or `impression` partition |
| Snapchat purchases/value | `snapchat_purchases`, `snapchat_purchase_value_sar` | Snapchat Ads API TOTAL facts | same selected-account partition | never filled from Salla and never replaced by account spend or another result source |
| Impressions/clicks/funnel | existing provider fields | Snapchat Ads API | selected account/campaign and date partition | provider-only |
| Salla ROAS/CPA | `salla_roas`, `salla_cpa_sar` | explicit Salla outcome divided by explicit Snapchat spend | no extra source | only when Salla, Snapchat and matching are complete and reconciled |
| Snapchat ROAS/CPA | `snapchat_roas`, `snapchat_cpa_sar` | provider value/purchases divided by provider spend | provider-only | only when Snapchat is complete |

Every response includes `request_id`, selected account ID/name, dates,
`effective_timezone`, account/Salla timezones, independent `as_of` values,
source statuses, matching status, numeric account/campaign reconciliation,
bounded row limits, filter/page metadata, requested-ID diagnostics and campaign
exclusion reasons. Source failures are independent: Salla success is retained
if Snapchat fails and vice versa.

## The two reported campaign IDs

The repository and local environment contain no production Mongo snapshot or
database connection for either ID, so their historical production state cannot
be truthfully classified from this checkout. The code-level cause of silent
absence is proven: the old reader iterated `campaign_groups` (performance facts)
instead of the entity catalog. Regression fixtures cover both exact IDs as
active entity-only campaigns and prove they survive the active scope with null,
not zero, metrics. The deployed/read-authorized response will now distinguish
the actual live reason instead of silently hiding the row.

## Cache and bounds

The active profitability cache was keyed by user/account/date/cost revision and
spend signature, but not order/attribution evidence or timezone. It is disabled
for this report. The frontend platform-only snapshot is cleared at every new
request and is not used to hydrate campaign financial values. Order reads use
`limit + 1` and fail on more than 100,000 rows; performance and entity reads do
the same at their declared bounds. The authoritative TOTAL reader also requests
100,001 rows and fails rather than silently truncating. Active/search filters
precede page slicing.

## Current pull-request overlap

GitHub's current file lists show zero direct changed-file overlap with PRs
#1001, #1002, #1003, #1004, #1005, #1007, #1008, #1009, #1010, or #1011.
#1006 is the open Release Train issue, not a pull request. The hard dependency
is semantic and ancestral: this branch starts exactly at #1004 head
`a9d31c94db80b57cb558133c39effa64da92f94c`. PR #1008 can change how readily
current catalog cost is maintained but does not change this report's read
contract. The #1009 → #1010 → #1011 Decision Intelligence stack remains
untouched and must be revalidated against this corrected truth contract before
activation.

## Verification and bounded benchmark

The focused backend regression set passes 98 tests, including the #1004
freshness/TOTAL invariants. The focused frontend set passes 55 tests across 12
suites, including navigation/reload state, failed-latest-request behavior,
source switching, tables, charts, normalization, and snapshot hydration being
disabled. A synthetic read-only run with 5,000 campaigns, 10,000 Salla orders,
and a 25-row page used 2 order queries, 1,754.837 ms, and 4.38 MB peak traced
memory. It matched 10,000 unique orders, excluded 0 duplicates, and reported 0
cache hits/misses because the financial cache is disabled.
