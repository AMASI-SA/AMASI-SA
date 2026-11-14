# Operational order-stage repair — source freeze

## Observed problem

The verified PR1917 release (deployment `2db0edc9edb315026eee4b6edd43b420a569c37d`, platform `120e0fa`) still returned the saved financial snapshot. Its single visible refresh on 2026-11-14 cancelled inside `source_orders` after 61.803 seconds and returned HTTP 408. Reconciliation was not reached. This follow-up starts from that exact production merge J.

Synthetic operation counts identified repeated whole-cohort scans in delivery assignment selection, prior courier payment holds, product cost catalogue selection, and growing diagnostic-list membership. They establish avoidable quadratic work, not the production distribution or the proportion of live time attributable to each helper.

## Source changes

- Build request-owned assignment, courier hold and cost catalogue indexes once per `collect_sources` invocation, using the existing joined preparation worker. Rebuild them on every retry; retain original row references and ordering.
- Preserve strict delivery identity matching separately from the older raw order-number filter. Keep the original assignment reader unchanged for other callers and unsupported inputs.
- Select courier history by party, currency and exact obligation while preserving movement order, allocation short circuits and lazy Decimal evaluation. Keep the original hold reader unchanged as fallback; do not cache hold decisions.
- Reuse the existing resource uniqueness contract and ordered cost bindings. Prices, option interpretation and cost policy remain evaluated by the existing per-item code.
- Use an explicitly owned diagnostic index while retaining complete-dictionary equality, duplicate/enrichment behavior and ordinary-list output. Remove it before returning the collector result.
- Add fixed `source_order_indexes` and `source_bank_catalog` timing labels. The existing bank reads and error behavior remain unchanged.

No source limits, request deadlines, authorization rules, financial write paths or background refresh behavior change. Original operator files and unsaved editor content remain outside this source change.

## Verification before source freeze

- Final integrated local selection: **1,467 passed, actual exit 0**, 23 test files; source hashes stayed unchanged throughout the run. Four real-Mongo cases are explicitly deferred to exact-source CI because the local replica set is unavailable. The initial combined run's one connection-refused failure is retained in the handoff evidence.
- Focused suites: assignment 130, cost catalogue 113, courier hold 177, diagnostic index 21. Counts overlap the integrated selection and must not be added to it.
- Growth regressions cover construction plus lookups and compare exact legacy behavior. Negative controls for assignment, cost and diagnostic membership fail the intended repeated-work assertions when indexing is disabled. Synthetic timings do not establish a production speedup.
- Independent reviews checked full collector/report parity, immutable inputs, first exceptions, Decimal behavior, duplicate/order semantics, fresh attempts, visible-request cancellation and worker joining. Concrete unusual-key/metaclass fallbacks were corrected before the final integrated run.
- The next exact-source CI review must verify all four new suites, four added timing cases, and all four deferred Mongo cases in both official backend XML artifacts.

## Remaining release and acceptance gates

Freeze source A, verify exact-A CI and candidate provenance, then create intent-only B. Require exact-B checks, a normal merge with unchanged source tree, the original operator's actual governed `yarn build`, guarded publish, a newer explicit platform completion, and official production verification with a closed lease.

Only then run one visible financial refresh and require a new report `as_of`. Check table pagination and pending-order search/page/close without financial confirmations. The previous release remains a known live failure; this source freeze is not a claim that production is repaired.

The documentation-only handoff branch is `codex/operational-order-stage-handoff-20261114`, at this same path. It preserves current evidence and subsequent release identities and must not be merged or deployed. Issue 1006 has reached the service's comment limit.
