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
