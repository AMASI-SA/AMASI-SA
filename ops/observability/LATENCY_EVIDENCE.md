# Limited latency evidence (Draft; no Production activation)

This patch is independent of held PR1316: no watcher/thread, Collector, metrics
activation, Mongo listener change, Governor policy change, or performance fix.
It uses the existing one-second runtime lag monitor. The new channel is closed
by default and does not enable OBS_METRICS_ENABLED.

## Control, privacy and limits

Both LATENCY_EVIDENCE_ENABLED=1 and an explicit LATENCY_EVIDENCE_PERMIT_FILE
are required. The latter must be a regular owner-only file owned by the process
user, modified within the last 900 seconds. Contents are never opened. Symlinks,
FIFO, group/world permissions, missing/expired files fail closed. Use a private
local directory outside /app. Environment capability requires a separately
approved deployment/configuration change; this PR authorizes neither.

After separate activation authorization, an operator may create the permit with
mode 0600. Remove it to disable, or let its 15-minute lifetime expire. The
existing monitor rechecks every second; no independent watchdog exists. During
an event-loop stall removal takes effect only when the loop resumes. This is
explicitly not the independent disable guarantee proposed in PR1316. No new
endpoint or distributed worker selection is added. A shared permit can affect
several workers and must not be represented as single-worker targeting.

Each worker emits at most one lag record per 30 seconds at lag >=250ms, and
samples at most one new Dashboard and one Snapchat operation per 30 seconds,
with at most one sample active per stage. Long samples emit on completion or
cancellation, so an indefinitely stalled process cannot emit a completion.
Maximum steady-state rate is six records/minute/worker (plus bounded boundary
effects). Existing application logs are unchanged. Records contain UTC times,
PID, random worker instance/trace, boot/source/deployment/release identity,
fixed phase labels and numeric timing aggregates only. No URLs, user IDs,
account IDs, queries, documents, tokens, exception text or request bodies.
Container mapping requires the hosting platform's existing log metadata.
Retention/rotation remains the host's existing policy; no new disk store.

## Interpretation: wall time, not CPU

- `governor_wait`: actual async admission wait, not the whole admitted body.
- `mongo_cursor_await`: selected `_to_list` calls in Dashboard and scheduler;
  includes driver/pool/network/materialization/scheduling, not server time.
- `mongo_write_await`: Snapchat performance fact `update_one` call only.
- `provider_http_await`: actual Snapchat context `client.get` call, including
  HTTP transport/body wait; excludes its preceding throttle and token refresh.
- `computation_wall`: synchronous currency summary and Snapchat row extraction/
  aggregations; not measured CPU and excludes other computations.
- `cooperative_computation_wall`: Dashboard executive reducer including yields.
- `account_refresh_mixed`: inclusive account refresh, overlaps subphases.

Sums of concurrent/nested spans are NOT a partition of total elapsed time.
Uninstrumented remainder must not be called CPU. This is selected-boundary
evidence, not full tracing. Compare timestamps/worker/trace with existing stage
logs and health incidents; temporal overlap alone does not prove causality.

## Fixed isolated validation plan

Linux/Python 3.11; existing monitor with a deliberate 450ms synthetic loop stall;
privacy, cancellation, bounded logs, unsafe permits, disable, and real Snapchat
client method through isolated HTTP transport. No commercial startup/providers.
Affected existing Dashboard/Snapchat tests must keep their assertions unchanged.

One overhead session: nine separate processes, fixed orders baseline/off/on,
on/baseline/off, off/on/baseline. Each: 0.5s warmup, 3s at 300 operations/s,
1s throughput with 8 concurrent tasks; synthetic 2ms wait and fixed computation.
Raw latencies, per-cell p95/p99, process CPU (% one core), RSS, throughput,
errors and paired differences retained. Baseline is this synthetic workload
without hooks, not a simulation of Production. These are diagnostic costs,
not a release/performance PASS gate. The bounded sample/log is emitted during
the measured window. No automatic second round to seek a better result.

Known pre-existing regression collection blocker: test_dashboard_runtime_reducers
imports absent test_dashboard_b_index_profile_correctness. Do not rewrite tests
to hide it. Other existing affected tests run independently.

## Rollback

Before merge: close/hold this Draft without affecting live runtime. After any
separately approved rollout: remove the permit to stop new evidence on loop
resumption; remove the opt-in configuration for persistent default-off. A code
revert is a separately reviewed release, never automatic. No data migration,
business backfill or Collector changes are required.
