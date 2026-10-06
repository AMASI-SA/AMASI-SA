# P1253 paired Linux diagnostic

Diagnostic branch only; application branch remains frozen at
15f00ff0d072cf8e00b8d642a6390d2ef71c11ac. Baseline is
bd8e1a4c377e61544b5cfe0f71f573c71abca89f.

One Linux job uses detached source worktrees, one frozen harness/generator from
15f00ff0, pinned diagnostic Python packages and Mongo8.0.12. No application or
repository dependency changes. Only loopback synthetic databases are used.
Fresh Python workers; same dataset/index digest before/after each worker; identical
bounded Mongo warm scan before timing; baseline/current/baseline repeat controls,
then separate profiled companions. Profiling timings are not used as control latency.
Exclusive sync CPU and inclusive stages/driver time must never be added together.
Same-key concurrency and independent tenants are explicitly labeled; tenants3/c4
repeats tenant0 and is not four independent tenants. Missing cache hit/miss counters
are null, not fabricated. Full signatures recorded even when different; financial
mismatch fails closed. No runtime code, production, release, Security changes.

Local syntax AST PASS. Linux smoke precedes large matrix. Results pending; no RCA
claimed. Workflow artifact is the evidence destination. No merge/deploy authorized.

## Diagnostic contract update -- execution plan pending

Added only pure diagnostic scheduling/statistics code and8 unit tests. Equal AB/BA
source repetitions; stable tenant-0 across A/A+B/A+B+C/A+B+C+D; separate same-key
workloads; cold0/warm1 warmup plan; rejects pooled/mixed/profiled/duplicate samples.
Tail statistics with insufficient samples are null, not claimed reliable; empirical
quantiles do not by themselves prove precision.8tests PASS.
This is NOT completed Linux cold-reset verification or a runnable updated matrix.
Existing runner remains untouched and MUST NOT be rerun as the new contract.
GitHub-hosted6hour maximum is insufficient for a full high-sample cold/warm matrix
at historical100k runtimes. User asked to choose long-lived isolated Linux capacity
versus explicitly exploratory lower-sample evidence. No choice inferred.
No application, source SHA, dependencies, dataset/schema, Production or release changes.
Next: obtain execution-budget/runner decision, integrate real cold reset and matched
warmup with observed verification, then small Linux smoke before final matrix.
