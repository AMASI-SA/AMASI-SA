# Dashboard B computation-only experiment — pending evidence

User rejected sharing DB reads after the concurrency gate. This experiment keeps
all runtime source and all Legacy/V2 database observation boundaries unchanged.
No runtime adoption is authorized, regardless of benchmark result.

## Candidate

- Legacy: reuse parsed_all only when electronic_orders_included has exactly the
  same length and ordered object references as all_orders. Both originate from
  the same already-read, projected, hydrated and filtered Legacy dataset. A
  differing subset/order/object recomputes independently. Copy the derived result
  to preserve separate mutable result containers. No cross-path dataset reuse.
- V2: reuse month_sales only when month_orders is orders, the existing runtime
  alias for the identical selected/current-month range. Overlap is insufficient.
- No cost/index sharing is introduced: no repeated identical input boundary has
  been established for these calculations. Instrument existing work unchanged.
- No database read replacement, cache, fingerprint, transaction/snapshot,
  formula/filter/limit/status/date/governor or API change.

## Acceptance evidence

Linux/Python3.11 + isolated loopback MongoDB8.0.12 replica set. Existing mixed
fixtures include malformed/duplicate records, currencies, costs, tenant isolation
and real permission checks. All ten former mutation schedules must now retain
both read boundaries and produce exactly identical full JSON.

Paired Current/computation-only 10K/50K/100K, month and overlapping period, one
warmup + ten alternating-order pairs. Assert identical Mongo command counts and
returned documents per collection and full JSON every pair. Record parsing and
derived computation counts/timing, main-thread CPU, handler p50/p95, serialization,
heartbeat lag and sampled RSS. Phase timings overlap and must not be summed.
Large data is synthetic; these are not Production measurements. No assertion
that this resolves the remaining event-loop stall.

Evidence pending. Keep Draft; no runtime, C/E, merge, prepare or deployment.
Production access/test/writes = 0.
