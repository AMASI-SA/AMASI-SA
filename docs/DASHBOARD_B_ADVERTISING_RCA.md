# Advertising breakdown RCA: instrumentation only

Baseline ee412b9325bef9a6bb2f27a0ca55dec2a6c31604. PR1301 stays Draft.
Parser5ms and cost5ms are frozen prerequisites in every arm. Their files are
unchanged. There is no advertising cooperative candidate in this round.

Private AST compilation retains every statement, provider order, float addition,
rounding, duplicate handling and error behavior. Coarse timers split input
accumulators, order traversal, provider preparation/finalization, total
calculations and output DTO construction. Fine timers split attribution
matching (canonical then fallback), currency resolution, order aggregation,
provider calculations, provider DTO and provider total accumulation. Nested
spans overlap and are never added together. Sorting and deep-copy are absent
inside the original function; do not invent phases with these names.

A separate profile captures actual input objects from an isolated Dashboard
request. Capture copies ads before its later executive_breakdown assignment;
this test-only copy and input JSON sizing are OUTSIDE timed samples. Original,
coarse and fine arms run warmup plus three measured repeats at10K/100K; heartbeat
observes direct synchronous blocking. No DB commands or serialization run
inside the measured breakdown. Fine observer overhead is calibrated explicitly.

The HTTP matrix compares original advertising against coarse instrumentation
only, using the unchanged independent-process interactive client, six paired
rounds plus warmup, Linux/Python3.11/MongoDB8.0.12 replica set:
10K multi-same;100K single-same;100K multi-different. Exact response bytes and
Dashboard Mongo command counts must match. All budgets/behaviors in Governor,
parser and cost remain fixed. This is measurement, not an optimization A/B.

The function is synchronous and reads only materialized orders/ads. Its call is
after the cost/ads/recurring gather; no later await exists on the current route.
Independent prior DB reads remain independent. JSON encoding/rendering happens
after endpoint return and is measured separately by the HTTP harness.

After results, identify the largest measured section and propose (do not
implement) an ordered outer-order accumulator experiment at1/5/10ms if justified.
Never merge independently rounded batch totals or deduplicate orders. Unknown
unattributed currency can invalidate overall sales; retain that behavior.
A future checkpoint would not bound one pathological nested attribution record.

No runtime adoption, process/worker, cache, shared DB read, Governor/formula
change, merge, prepare, deploy or Production test. Production writes=0.
