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
-> Mongo lookup + filter + stable sort + facet(summary, count, skip/limit 25)
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
- child routes additionally accept their exact parent IDs

The existing outer response remains compatible. The `unified` contract now
contains `rows`, `page`, `page_size`, `total`, `filtered_total`, `pages`,
`has_more`, `sort`, `filters`, and `request_id`; the outer response also
contains `pagination`, `request_id`, and read diagnostics. Stable ordering uses
`external_id` as the final tie-breaker. Unsupported Salla-dependent sorts fail
closed instead of claiming an unbounded server sort.

The entity facet computes spend and Snapchat outcomes across the full filtered
scope, independently of the current page. The separately fetched `/report`
contract remains authoritative for account headline spend and reconciliation.
Salla rows are enriched only for visible IDs. Salla report totals and
profitability use one independent Mongo aggregate and materialize no order rows
in Python. When search/active filters are present, the same filters are pushed
into the campaign lookup; ambiguous source-only orders are excluded from that
filtered subtotal rather than guessed into a campaign.

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
  overlap. #1004 overlaps `backend/snapchat_v2/routes.py`; its Provider TOTAL
  and hourly semantics remain unchanged. #1012 uses shared reporting/table
  code, so this task added `SnapchatFinancialEntityTable` and did not modify
  `UnifiedMarketingEntityTable` or the Ads Manager route.

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
| Python Salla summary order rows | full-detail path possible | 0 |
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
fact-source coverage aggregate plus one page/count/summary facet), one Mongo
aggregate for report-wide Salla totals, and zero child entity page commands on
initial load. The visible Campaign settings path uses one exact-ID find plus
fixed account/run reads and one scalar child aggregate. The status regression
asserts five total DB read commands with one run facet instead of per-level
N+1 queries.

Actual Mongo `totalDocsExamined` is intentionally reported as unavailable:
this task performed no production or external database access. A staging
dataset with the production index shapes is required for
`explain("executionStats")`; no value is inferred from synthetic Python data.

## Verification inventory

Automated coverage includes 5,000-row page 1/page 2 boundaries, stable
no-duplicate pagination, pre-page active/search/sort, report-wide totals,
parent-first filters, bounded visible settings and scalar child aggregation,
bounded Salla detail/summary, idempotent indexes, five-command status,
privacy-safe timing, request coalescing, lazy child reads, targeted settings
exactly once after ID 500, stale bulk/selection protection, fail-closed
identity checks, financial column order, expandable metrics, budget/bid labels,
on-demand drawer behavior, and zero page-load writes/proposals.

## Remaining limitations

- Server sorting intentionally supports only default, spend, and name. Salla
  sales/orders/ROAS/profit sorts are not exposed because doing so correctly
  would require a separate indexed materialized summary or a potentially large
  join/scan.
- Mongo documents examined and real DB/network latency remain to be measured in
  a non-production staging environment with representative data.
- Source-labelled Salla orders without an exact Campaign ID are included in the
  unfiltered account summary, but are excluded from search/active-filtered
  summaries because assigning them to a filtered Campaign would be a guess.
