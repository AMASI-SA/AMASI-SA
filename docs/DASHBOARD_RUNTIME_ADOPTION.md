# Dashboard runtime adoption candidate

Status: implementation checkpoint; release gates pending. Not deployment approval.

Production branch base: `0abf4517afd10e9e331fc10dbbca1c60b981c505`
(tree `986e59ccaf95c767bafb507585927cf1071d2f33`). This is a Git source base,
not a claim that this task inspected or changed the live runtime.

Frozen experiment: PR #1301, HEAD
`05b159dc4183f14adc7c370bfbd8014d8858e6b8`, tree
`118d9f146c4202eebda2fe906327f2f173ef7345`. That Draft PR is unchanged.

## Scope

Adopt only the measured ordered cooperative reducers:

| Reducer | Active CPU budget | Boundary |
|---|---:|---|
| Parser | 5 ms | Complete order, in both currency and parser passes, one budget |
| Cost/profit | 5 ms | Complete order in the cost/profit outer loop |
| Advertising | 1 ms | Complete order in the ordered outer loop |
| Product indexing | 5 ms | Complete product, then complete alias group after all products |

The measured candidate also requires its existing computation-only reuse:
deep-copy parsed results only for identical ordered input object references;
deep-copy the month summary only when the month and selected list are the same
object. These do not share database reads. Nonidentical inputs recompute.

The shared synchronous parser, currency, catalog and advertising APIs remain
unchanged. The original async product-cost builder remains unchanged for
Profit Engine callers. Dashboard calls explicit async companions. Source-parity
tests strip only the scheduling scaffolding and compare the entire reducer AST
with the Production base, including accumulator statements and finalizers.

No test AST compilation is shipped into runtime. No threads/processes, cache,
new snapshot/transaction, changed Mongo query, Resource Governor policy,
rounding, ordering, filters, limits, permission or tenant policy is introduced.
Financial writers, Shipping, Review and Android are outside the diff.

## Correctness and release evidence plan

`dashboard-runtime-adoption.yml` checks out the immutable experiment separately
and runs it in a separate Python process. Its mixed fixtures and controlled
mutation schedules produce frozen response bytes, command counts and document
counts. A second process exercises actual runtime functions against those
records, with assertions that every new runtime helper was invoked. Runtime
source is never compiled through the experiment's AST transformers.

The gate uses Linux/Python 3.11 and a real, loopback-only MongoDB 8.0.12 replica
set. Fixtures reject nonlocal Mongo URIs and network connections, and wrap
request collections in a read-only adapter. Independent-writer schedules include
status/payment/shipping/date/cost/advertising changes and post-catalog mutations
during real index checkpoints. Additional gates cover aliases/references,
malformed inputs, cancellation, private budgets and existing regression tests.

Release Readiness, Security Gate and Python/JavaScript CodeQL must pass on the
candidate. Protocol v5 additionally requires a source A build/reproducibility
proof, its generated intent-only B commit and the clean-clone package/runtime
identity rehearsal. No prepare, prepublish, lease or Production probe is allowed
by this task. Results and exact SHAs belong in the final PR and Issue #1006.

## Preserved performance comparison (not a new benchmark)

The user forbids another performance experiment. These are the approved
synthetic Linux stage comparisons, not new runtime measurements and not an
additive cumulative improvement against original Production.

| Selected candidate | API-thread CPU change | Dashboard p95 change | Interactive p95 improvement |
|---|---:|---:|---:|
| Parser 5 ms | +1.07% to +3.84% | +0.32% to +1.56% | 12.19% to 14.92% |
| Cost/profit 5 ms | -0.69% to +2.99% | +0.60% to +1.43% | 41.59% to 48.20% |
| Advertising 1 ms | -0.58% to +0.45% | +0.59% to +1.73% | 32.38% to 35.75% |
| Product indexing 5 ms | -1.34% to +1.17% | +0.57% to +2.96% | 4.01% to 9.34% |

Comparators: parser vs computation-only; cost fixes parser 5; advertising fixes
parser 5/cost 5; indexing fixes parser 5/cost 5/advertising 1. CPU includes the
interactive work. Last indexing round: 136 PASS, no failures/skips and no HTTP
errors/timeouts. Indexing Dashboard throughput declined 0.48% to 1.50%.

Sources: [parser CI](https://github.com/AMASI-SA/AMASI-SA/actions/runs/37944939216),
[cost CI](https://github.com/AMASI-SA/AMASI-SA/actions/runs/37953065959),
[advertising CI](https://github.com/AMASI-SA/AMASI-SA/actions/runs/37964673368),
[indexing CI](https://github.com/AMASI-SA/AMASI-SA/actions/runs/37973002895),
[final evidence summary](https://github.com/AMASI-SA/AMASI-SA/pull/1301#issuecomment-6087736263).

## Limits and rollback

CPU budgets are not maximum wall-latency bounds. Whole work units, finalizers,
GC and JSON serialization can still block. Last 100K multi-different selected
arm: heartbeat 3364.24 ms; interactive p99 3854.60 ms; profit finalization median
1307.01 ms/max 1383.82 ms; JSON render median 612.38 ms. These overlapping values
must not be summed. A 5 ms index slice reached 659.37 ms in the single case.
This does not establish a Production SLO or solve every responsiveness issue.

Exact equivalence covers captured inputs and controlled matching mutation/read
schedules. Introducing yields cannot promise identical results under every
arbitrary wall-clock race. No new DB snapshot boundary is claimed.

Rollback is a reviewed source revert of this isolated adoption and a fresh
protocol-v5 intent build; no data migration or database restoration is needed.
This document does not authorize performing that rollback or any deployment.

Production unchanged; Production writes = 0.
