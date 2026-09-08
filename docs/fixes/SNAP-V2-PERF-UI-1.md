# SNAP-V2-PERF-UI-1 audit and benchmark

## Scope and safety

- Base: `origin/hotfix/prod-snap-meta-final` at
  `1de6118484ac4fe1d0981e230618dbb573d8c58c`.
- UI scope: `/snapchat-accounts` only.
- The shared Ads Manager campaign page and SNAP-REPORT-1 / PR #1012 were not
  changed.
- The work is read-only on page load. It does not publish, deploy, create a
  release intent or lease, call a provider write, create a proposal, or write
  Salla/accounting data.

## Root cause

The previous campaign route read entity catalogues and performance facts into
Python with limits as high as 20,000, enriched the resulting rows, returned the
full set, and relied on the browser to display a 25-row slice. Campaign
settings were independently capped at 500 and campaign budget aggregation
could hydrate up to 10,000 account-wide Ad Squad rows. The visible row count
therefore did not bound backend work, response bytes, or Python memory.

## Before and after data flow

Before:

```text
entity catalogue + fact rows -> Python joins/sort/enrichment for all rows
-> full JSON response -> browser filter/sort/slice(25)
-> broad settings read -> account-wide child budget hydration
```

After:

```text
entity catalogue exact account/level/parent match
+ period fact identities missing from the latest catalogue
-> Mongo lookup + filter + stable sort + one sibling facet
   (identity count, filtered count, filtered summary, skip/limit 25)
-> Python receives <=25 entity rows + one scalar summary
-> Salla row detail constrained to visible campaign identities
-> independent Mongo Salla summary aggregate
-> settings exact-ID batch for visible rows + scalar child aggregate
-> browser renders the returned page without local pagination
```

Ad Squads and Ads have no initial request. Opening a Campaign requests only
that Campaign's Ad Squads; opening an Ad Squad requests only that Ad Squad's
Ads. Parent constraints are in the first Mongo `$match`, before lookup, sort,
skip, and limit.

## Backend contracts

`GET /api/integrations-v2/snapchat-v2/{campaigns|ad-squads|ads}` accepts:

- `page` (default 1), `page_size` (default 25, maximum 100)
- `search` (name or ID, maximum 100 characters)
- `active_only`
- `sort_by=default|spend|name`
- `sort_direction=asc|desc`
- child routes require their exact parent IDs

The existing outer response remains compatible. The `unified` contract now
contains `rows`, `page`, `page_size`, `total`, `filtered_total`, `pages`,
`has_more`, `sort`, `filters`, and `request_id`; the outer response also
contains `pagination`, `request_id`, and read diagnostics. Stable ordering uses
`external_id` as the final tie-breaker. Unsupported Salla-dependent sorts fail
closed instead of claiming an unbounded server sort.

The entity facet computes spend and Snapchat outcomes across the full filtered
scope, independently of the current page. The separately fetched `/report`
contract remains authoritative for account headline spend and reconciliation.
Salla rows are enriched only for visible IDs. Salla attribution totals use an
independent Mongo aggregate. Canonical profitability then materializes at most
10,000 exact-ID, financially included orders and loads only the products,
variants, parents, SKUs, profiles, bindings, services/components, options, and
resources referenced by those orders. When search/active filters are present,
the same filters are pushed into the campaign lookup; ambiguous source-only or
name-only orders are excluded from financial profit rather than guessed into a
campaign.

## Settings and management proof

Visible campaign/Ad Squad settings are requested with an exact comma-separated
ID set, limited to at most 100 and to the current page size in the workspace.
Campaign child budget/status/strategy data is grouped in Mongo, returning one
scalar per visible campaign and zero child entity documents to Python. A
Campaign still has no invented direct budget: only a fully covered `Ad Squads
Budget Total` is shown. Ad Squads show direct daily budget. Bid labels preserve
`TARGET_COST`, `LOWEST_COST_WITH_MAX_BID`, and `AUTO_BID` semantics.

Opening the management drawer always starts a separate one-ID settings GET.
Preview, approval, and execution remain blocked until that targeted result is
fresh, mapping-verified, bound to the selected account, and (for Ad Squads)
bound to the selected Campaign. Generation counters prevent a late bulk,
account, page, or previously selected entity response from replacing the
current state. Closing the drawer does not reload the page.

## UI structure

The page is now header, summary cards, financial-first entity table, then the
hourly section. The 17 primary columns explicitly label Salla and Snapchat
sources. Engagement metrics remain available in an expandable secondary row.
The table minimum width is 1,680px instead of the former 2,450px design. The
large management panel is mounted only in an on-demand drawer.

## Reuse and overlap audit

- PR #986: preserved budget, child aggregation meaning, bid/Target Cost
  semantics, freshness, preview/approval/execute, verification, and rollback
  guards.
- PR #988: ported the status run facet that removes N+1 queries, concurrent
  independent stored reads, shape-checked read indexes, privacy-safe
  `Server-Timing`, identical in-flight GET coalescing, and stale-result guards.
- PR #990: ported exact visible-row settings batches, one-entity targeted GET,
  selection beyond the former first-500 boundary, account/entity/parent
  validation, and targeted stale-response protection.
- The old #988/#990 page implementations are superseded by the bounded entity
  service, the financial-first table, and the on-demand drawer. Neither PR was
  merged, cherry-picked, modified, or closed.
- #1001, #1003, #1004, #1009, #1010, #1011, and #1012 were reviewed for file
  overlap. The acceptance-review matrix below records the actual overlapping
  files and functions. #1012 uses shared reporting/table code, so this task
  added `SnapchatFinancialEntityTable` and did not modify
  `UnifiedMarketingEntityTable` or the Ads Manager route.

### Acceptance overlap matrix

| PR | Shared file | Other PR's functions | This PR's functions | Preservation result |
| --- | --- | --- | --- | --- |
| #1003 runtime/server | `backend/server.py` | main Mongo client options, `/ready`, `/auth/me` availability | `SnapchatV2ReadTimingMiddleware` registration and `_global_startup` read-index hook | Disjoint hunks. The acceptance patch does not edit `server.py`; bounded startup readiness remains a train-assembly requirement. |
| #1004 Snapchat reliability | `backend/snapchat_v2/routes.py` | `SnapchatV2SyncInput`, `_daily_retry_dates`, `/snapchat-v2/sync` | `_entity_performance_report`, Campaign/Ad Squad/Ad GETs | Sync/retry and Provider TOTAL/hourly selection are unchanged. Child GETs now require their parents. |
| #1004 Snapchat reliability | `backend/unified_marketing/readers/snapchat_v2.py` | `_projection_financial_run_statuses`, `load_snapchat_v2_dashboard_spend` | `_page_management_identities`, `load_snapchat_v2_entity_report` | Dashboard/range proof is untouched; paginated entity reports explicitly fail account-completeness eligibility. |
| #1009 Meta adapter | `backend/snapchat_v2/salla_outcomes.py` | `_match_order_campaign`, `load_salla_campaign_outcomes(provider=...)` | targeted canonical cost context and exact-ID row/summary profit | `snapchat_ads` remains the default and `meta_ads` remains supported; foreign-provider orders fail closed. Both providers now require literal Campaign ID equality for financial attribution; name evidence is diagnostic only. |
| #1011 Decision Intelligence | `backend/decision_intelligence/evidence_adapter.py` | `_report_coverage_complete`, coverage gate, `decision_ready`, candidate blockers | explicit entity-collection completeness check inside `_report_coverage_complete` | The small consumer-side hunk composes with #1011's existing quality checks. Explicit partial-page markers block coverage; older/provider reports without those markers preserve their prior behavior. No #1011 branch or unrelated #1011 function was changed. |

## Acceptance findings

| Finding | Status | Fix and production-path evidence |
| --- | --- | --- |
| A — nested `$facet` | Confirmed, fixed | `build_entity_page_pipeline` now uses one top-level `$facet` with sibling page/count/summary branches. `test_real_mongo_reproduces_legacy_nested_facet_rejection` reproduces Mongo's rejection and `test_real_mongo_runs_production_pipeline_for_all_levels_and_explain` executes the real Campaign/Ad Squad/Ad pipeline. |
| B — full cost catalogue hydration | Confirmed, fixed | `_load_cost_context(..., orders=...)` restricts products by actual product/parent/variant/SKU identities and then restricts profiles/bindings/resources. Its no-`orders` default preserves existing shared consumers. The 25-row unit contract and the real full-request benchmark assert targeted materialization. |
| C — row/summary cost and attribution drift | Confirmed, fixed | `load_salla_campaign_outcomes` and `load_salla_report_summary_aggregate` now use the same literal Campaign ID financial universe for orders, sales, cost, profit, and campaign ROAS. Campaign-name, shared-name, case-mismatched, whitespace-mismatched, and source-only orders remain outside campaign financials and are reported separately. Both paths reuse `_order_cost_and_products` and the same status/refund policy. |
| D — catalogue presence treated as active | Confirmed, fixed | `normalize_entity` exposes observation separately from normalized status/effective status. `_operationally_active_expression` drives filtering and the public `active` field. Real-normalizer tests cover ACTIVE, PAUSED, effective ACTIVE, missing-from-latest-sync, and unknown status. |
| E — API/history/consumer completeness | Confirmed, fixed | Child routes require parent IDs; the identity pipeline unions period facts so historical fact-only rows survive; pagination carries `rows_are_page`, `collection_complete`, and `identity_scope`. The actual `evaluate_decision_evidence` path now consumes those markers in `_report_coverage_complete`: a 25-of-5,000 Campaign page blocks coverage and produces zero recommendations, while a genuinely complete 25-of-25 report remains decision-ready. Real route tests assert HTTP 422 without parents and bounded success with exact parents. |

`active` in the public entity row now means operationally active. Persisted
catalogue `active` remains an observation-compatibility field;
`observed_in_latest_sync`, `missing_from_latest_sync`, `operational_status`, and
`catalogue_present` make the meanings explicit.

## Synthetic benchmark

Reproduce with:

```text
python backend/scripts/benchmark_snapchat_v2_pagination.py
```

Dataset: 5,000 Campaigns, 10,000 Ad Squads, 20,000 Ads, page size 25, seven
trials. The recorded run measures the application materialization/JSON boundary
on this worktree; it is not a live Mongo latency claim.

| Metric | Before | After |
| --- | ---: | ---: |
| Python Campaign rows | 5,000 | 25 |
| Python report summary rows | page-derived/full hydration | 1 |
| Python Salla summary order rows | not exercised | not exercised |
| Response bytes | 2,451,427 | 12,492 |
| Median boundary time | 637.940 ms | 5.046 ms |
| Median peak traced memory | 15,606,272 B | 61,090 B |
| Frontend entity rows | 25 | 25 |
| Settings rows | 500 cap | <=25 |
| Python child rows for Campaign settings | up to 10,000 | 0 |
| Initial Ad Squad entity rows | 0 | 0 |
| Initial Ad entity rows | 0 | 0 |

Reductions: 99.5% Campaign rows, 99.490% response bytes, and 99.609% peak
traced memory in the synthetic application-boundary run.

Known direct command bounds are two Mongo commands for the Campaign core (one
fact-source coverage aggregate plus one page/count/summary facet), two Mongo
aggregates for report-wide Salla attribution plus the bounded canonical-cost
order scope, and zero child entity page commands on initial load. The visible
Campaign settings path uses one exact-ID find plus fixed account/run reads and
one scalar child aggregate. The status regression asserts five total DB read
commands with one run facet instead of per-level N+1 queries.

## Isolated real-Mongo full-request benchmark

Reproduce from `backend` against a disposable MongoDB:

```text
SNAPCHAT_V2_TEST_MONGO_URL=mongodb://127.0.0.1:27017 \
  python scripts/benchmark_snapchat_v2_full_request.py
```

The script creates a UUID-named database, seeds 5,000 Campaigns, 10,000 Ad
Squads, 20,000 Ads, 5,000 products, and 25 exact-ID Salla orders, installs
indexes only in that disposable database, executes the production Campaign
HTTP route plus the visible-ID settings read, emits command counts/rows/bytes/
wall time/Python traced peak, runs `explain` with `executionStats`, then drops
the database. The before path is reported honestly as
`mongo_rejected_before_response`: the reviewed nested `$facet` fails before a
full HTTP response exists, so response bytes are `null` rather than a fabricated
comparison. A separate before probe records the old broad cost-context
materialization on the same dataset.

Green CI evidence: [Snapchat Settings Management V2 run #78](https://github.com/AMASI-SA/AMASI-SA/actions/runs/33995936815).

| Full Campaign request metric | Before: reviewed nested facet | After: production route |
| --- | ---: | ---: |
| Outcome | Mongo rejected before response (`40600`) | HTTP 200, complete |
| Mongo read commands | 1 aggregate | 26 (3 aggregate, 23 find) |
| Campaign rows returned to Python | 0 | 25 of 5,000 |
| Settings rows returned | 0 | 25 exact visible IDs |
| Full response bytes | `null` (no response existed) | 304,023 B |
| Campaign response bytes | `null` | 215,566 B |
| Settings response bytes | `null` | 88,457 B |
| Wall time | 1.801 ms to rejection | 1,443.745 ms complete |
| Python peak traced memory | 21,787 B to rejection | 2,647,207 B complete |

The invalid before pipeline cannot supply a valid latency, byte, or memory
baseline for a completed request. Reporting its tiny rejection cost as an
improvement baseline would be misleading. The separate old broad-cost probe
did complete: 7 read commands (6 find + 1 getMore), 5,000 product documents,
250.358 ms, and 9,040,600 B peak traced memory. In the completed after request,
both row detail and report summary independently restricted the current-cost
context to the 25 product IDs/SKUs referenced by the 25 orders; neither loaded
the remaining catalogue.

The after `executionStats` record contains three production-pipeline nodes,
each with `nReturned=5000` and `totalDocsExamined=5000`: the catalogue cursor,
the historical-fact union cursor, and the post-union identity stream. The final
Mongo facet still returns only the 25 requested page rows to Python. These are
isolated test-database values, not Production latency, Production index advice,
or Production `totalDocsExamined`. The older table remains explicitly a
synthetic application-boundary comparison.

The same real-Mongo job proved canonical row/summary parity on two exact-ID
orders: both paths reported 2 orders, SAR 200 sales, SAR 54 product cost, and
SAR 126 contribution profit. Each order used SAR 10 base, SAR 10 components,
SAR 4 service, and SAR 3 selected option (SAR 27 total); one missing and one
stale stored cost
were diagnostics only, with `stored_total_product_cost_used=false`. The
targeted run recorded 166 unit/regression tests passed, 5 real-Mongo tests
passed, 92 frontend tests across 6 suites passed, and a successful frontend
build.

The acceptance follow-up's fifth HTTP/Mongo case uses three Campaigns
and six Salla orders: one literal exact ID, one unique name only, one shared
name, one case-mismatched ID, one whitespace-mismatched ID, and one source-only
order. Only the exact-ID order contributes to Campaign row/summary orders,
sales, cost, profit, and ROAS. The other five are exposed as source-only totals,
never as campaign-matched totals. This small correctness fixture does not
change the benchmark workload: 5,000 Campaigns, 10,000 Ad Squads, 20,000 Ads,
5,000 products, and 25 Salla orders.

## Verification inventory

Automated coverage includes 5,000-row page 1/page 2 boundaries, stable
no-duplicate pagination, pre-page active/search/sort, report-wide totals,
parent-first filters, bounded visible settings and scalar child aggregation,
bounded Salla detail/summary, idempotent indexes, five-command status,
privacy-safe timing, request coalescing, real-Mongo route/pipeline execution,
lazy child reads, targeted settings
exactly once after ID 500, stale bulk/selection protection, fail-closed
identity checks, financial column order, expandable metrics, budget/bid labels,
on-demand drawer behavior, actual Decision Intelligence partial/complete
collection behavior, literal Campaign ID financial attribution parity, separate
source-only totals, and zero page-load writes/proposals.

## Remaining limitations

- Server sorting intentionally supports only default, spend, and name. Salla
  sales/orders/ROAS/profit sorts are not exposed because doing so correctly
  would require a separate indexed materialized summary or a potentially large
  join/scan.
- Isolated CI Mongo workload is representative synthetic evidence only; it is
  deliberately not a Production latency or index recommendation.
- Source-labelled Salla orders without an exact Campaign ID are included in the
  unfiltered account summary, but are excluded from search/active-filtered
  summaries because assigning them to a filtered Campaign would be a guess.
