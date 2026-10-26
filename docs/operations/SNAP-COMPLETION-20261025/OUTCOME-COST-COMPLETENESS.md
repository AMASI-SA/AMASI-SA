# Snapchat decision outcome cost completeness — R3 source

Base: `2d275221ba351dd31fc6d8106cfad3db186f448b`, production-branch merge
#1572 above courier recovery #1570. Branch: `codex/snap-outcomes-r3-20261027`.
This is source A preparation only; it does not assert a production deployment.

## Behavior

The evaluator previously rejected a scope only when `cost_complete is False`.
Missing/null/numeric/string flags could pass as complete, and cost/profit amounts
were not required. A sales-increase expectation could be recorded permanently as
successful without complete financial evidence.

Each campaign/account/store comparison now requires boolean `True`, finite
nonnegative product cost and gross sales, and a nonnegative integer order count.
Campaign/account also require nonnegative spend and finite contribution profit;
store requires finite gross profit before marketing. Negative profit and valid
complete zero amounts remain measurable. Numeric amount strings remain compatible;
bools, NaN and infinity are not numeric evidence. These are gross cohort totals,
not signed accounting/refund movements.

Unknown or invalid financial evidence stays inconclusive and retryable without
appending a final evaluation. This does not create spending authority, infer
causality or prove commerce freshness. Existing campaign expansion freshness
and authorization gates remain in force.

## Source and compatibility boundary

The outcome runtime, regression suite and Reporting V2 workflow are copied
byte-for-byte from reviewed source `4a210d163715780f9344dfbdd48c93333c91dbc9`
(previously reviewed as `3916aefda9dd577c1e295e7a25bada23b8d06bff`). Documentation
is refreshed for this base. Exactly four task paths change. There is no path
overlap with #1572 or #1570. Their pagination/courier code, tests and workflows
remain byte-identical to this base.

The source retains this base's exact `release/release-intent-v5.json` bytes.
Neither previous intent B `23d4cdf8b988a7572c32e30a271eb5afeab80828` nor
`6c800d65319cb074a0da3e4f71c1c0f950214a69` is carried forward. New exact-head
source CI must pass before a new reviewed intent-only B can be frozen.

## Verification

Original regression reproduction: 42 unknown-cost cases failed against the
original evaluator because it returned successful instead of inconclusive.
Reviewed R2 focused verification passed 203 tests, with 2 inapplicable whole-store
spend cases skipped. Its source and intent CI and Host20 rehearsal passed; those
results are historical and do not replace fresh R3 verification.

Reporting V2 CI explicitly selects outcome, metrics and ledger suites, including
both baseline/post windows, scope isolation, invalid cost/amount/count types,
negative valid profit, complete zero outcomes and stable decision-ledger behavior.

No `/app`, Preview, release lease, production database, advertising/provider,
merge or publication actions were performed by this task. The coordinator owns
serialized publication and must respect other conversations' release leases.

Fresh R3 verification from `backend`: `python -m pytest -p
snapchat_v2_test_bootstrap tests/test_snapchat_decision_outcomes_v1.py
tests/test_snapchat_decision_metrics_v1.py tests/test_snapchat_decision_ledger_v1.py
tests/test_campaign_ai_unified_execution_evidence.py
tests/test_courier_label_recovery.py tests/test_store_delivery_self_assignment.py
tests/test_salla_orders_sync_pagination_evidence.py -q --tb=short`:
**224 passed, 2 inapplicable whole-store spend skips, 4 inherited warnings**, exit
0 in 3.49s. `git diff --check` passed. Exact comparison confirmed #1572/#1570
paths and the base intent unchanged, and the three task code/test/workflow
files equal the previously reviewed source. Exact-head R3 CI is pending.
