# Dashboard B: cooperative parser experiment (NOT runtime adoption)

Authorized scope: PR1301, only `orders_to_parsed` and its nested currency pass.
Baseline HEAD: 22f9b7f2dd9142060bb97a6f01f541797bd002d1.
No production modules or accepted computation-only experiment are changed.

The test-only candidate compiles the actual two reducer bodies with checkpoints
at ordered record boundaries. Accumulators, two-pass execution, first-seen
identity semantics, stable sorting, exception behavior and final rounding remain
in place. It does not call independent batch reducers or combine rounded totals.
The nested currency pass shares the parser's budget; unrelated summaries remain
synchronous. A measured CPU budget expires at 1/5/10 ms, then a `call_soon`
continuation yields the loop. There is no fixed delay. Oversized records and
finalization can exceed a budget; record actual slice distributions.

Five controls on each runner: CURRENT runtime; unchanged computation-only;
computation-only plus cooperative parser at 1, 5, 10 ms. This separates the
previous sharing gain from the additional scheduling change. No read sharing,
cross-request cache, thread/process computation, governor or runtime changes.

Targeted Linux/Python3.11/MongoDB8.0.12 replica-set CI cases:
10K multi-same, 100K single-same, 100K multi-different. Each uses the existing
real Uvicorn HTTP fixture, independent-process open-loop 12 RPS probes and
unchanged admission policy. Six measured rounds plus warmup, rotated/reversed
arm order, deterministic per-round arrival offsets. Three Dashboard submissions
are admitted according to the unchanged capacity2/weight2 policy. Light probes
are fixtures, not production operational endpoints. The RSS sampling thread is
instrumentation only, never a computation worker.

Report per-endpoint p50/p95/p99, Dashboard tails, CPU, heartbeat scheduling lag,
Mongo driver latency/command counts, admission/execution, outer framework
encoding and JSON rendering, RSS, throughput and all errors/timeouts. Keep raw
wave data and HTTP response byte digests. Active-window durations vary with
Dashboard completion, so count/throughput and phase sensitivity must be
disclosed. Six/18 Dashboard samples are descriptive, not population p99; no
statistical no-regression claim from a single maximum. Do not add nested or
overlapping phase timings. Async parser CPU uses only synchronous slices.

Correctness: parser rounding/order/malformed/currency/duplicate cases, all
existing mixed filters/permissions/tenant cases, same DB read-boundary mutation
schedules, fresh-request visibility and cancellation. A separate new-checkpoint
diagnostic makes an additional write/read schedule explicit. It proves that
yielding admits extra interleavings, NOT that the actual Dashboard JSON diverges.
Unchanged query definitions are not proof of arbitrary concurrent-write timing
equivalence. No snapshot/lock/read-boundary workaround is introduced.

Acceptance: exact response equivalence for captured inputs and tested schedules;
evaluate each interactive p95/p99, CPU, throughput and Dashboard latency against
both controls. The existing >10% AND >25ms tail screen is a diagnostic flag, not
a user-approved SLO. On material regression stop; do not move to other phases.
Any remaining blocker or ambiguous concurrency evidence prevents adoption.

Rollback: remove only the new test/workflow/doc files. No schema, production
data, runtime toggle or deployment needs reversal. PR remains Draft. No merge,
prepare, deploy or Production test. Production writes=0.
