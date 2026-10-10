# AMASI_OBSERVABILITY_PHASE1_FINAL_REVIEW

Review-only implementation. No activation, merge, prepare, prepublish, deployment,
restart of any production process, or production business write.

## Scope and identity

Base: `cd554446e3dbdf618521f3e122f9b58b8e4e9035` on
`hotfix/prod-snap-meta-final`. That base already contains merged #1312; this diff
does not implement or modify that PR, #1311, Ready hotfix, Android or financial
logic. Exact final HEAD/TREE are reported in the PR handoff (not self-referenced
inside a commit). Working branch: `codex/observability-phase1`.

## Implemented

* Existing diagnostics now add a bounded `phase1` object. Original keys remain.
  An in-memory fixed-name histogram registry is shared by existing lag monitor,
  Mongo listeners and Governor. No exporter call, database request or log per
  observation. New instrumentation defaults off.
* ASGI API response-end durations, bucket p50/p95/p99, active/oldest requests,
  status categories. Health readiness is not Assembly Ready. Background work
  after the final response body is excluded from API time. Response sent by ASGI
  does not prove receipt/confirmation on Android.
* Mongo commands, pool wait and commit **attempt** latency. No transaction wrapper,
  retry or business outcome changes; no inference that command timing equals
  end-to-end transaction completion. Legacy query-shape field stays compatible
  but is excluded from the collector.
* Governor per-kind/global wait and hold durations without changing admission.
* Process CPU seconds and boot identity; current RSS/cgroup/OOM metrics reuse
  existing memory diagnostics. Boot changes at an explicit worker slot are
  observed restarts, not a durable count of every restart between samples.
* External opt-in stdlib collector: fixed numeric schema, bounded rotating store,
  per-worker private targets, no proxies/redirects, TLS except loopback, sanitized
  errors, one lightweight health request per minute. Not installed/scheduled.
* Inactive nginx upstream timing example. Actual ingress mapping, nginx syntax
  check, bounded shipping/rotation and platform routing remain activation gates.

Histograms are cumulative since worker boot; bucket quantiles are upper bounds,
not exact or rolling percentiles. p95/p99 are null below20/100 events. Compute
bucket deltas over aligned windows externally; reset on worker boot changes.
No dashboard/alert engine is activated or bundled. Active request tracking has
2048 slots; overflow is explicit and means the active gauge is incomplete.
There are finitely many metric names; unknown names become a reject count.

## Fresh verification

Windows 11, Python3.13.15, existing local test venv:

```text
PYTHONPATH=backend python -m pytest \
  backend/tests/test_phase1_runtime.py \
  backend/tests/test_phase1_mongo_governor.py \
  backend/tests/test_phase1_collector.py \
  backend/tests/test_phase1_real_mongo.py \
  backend/tests/test_resource_governor.py \
  backend/tests/test_mongo_observability.py \
  backend/tests/test_server_diagnostics_route_bindings.py -q
52 passed, 2 skipped (explicit disposable Mongo URI absent after cleanup)
```

Separate real Mongo8.0.12 `obsPhase1` replica set run: **2 passed**, raw output
in `real-mongo-tests.txt`. Fresh loopback port27851/dbpath, testCommands enabled
only there. `find` blocked650ms with pool1/wait150ms: actual checkout timeout
observed. `commitTransaction` blocked400ms: duration observed and exactly one
synthetic row persisted. The owned instance was shut down; no other DB/process
was touched. Tests refuse non-loopback, other port/set/version and credentials.

Covered: real loop stall, genuine pool saturation and slow commit, real Governor
held capacity/cancellation, boot-reset logic and collector restart detection,
collector unavailable, disabled fast path/kill file, bounded active slots,
histogram names, payload/storage/expiry, schema redaction, redirect rejection,
TLS enforcement and60s probe cadence. Worker restart coverage uses deterministic
boot changes/reset hooks, not a deployed multi-worker restart experiment.

During review a readiness-route misclassification and post-response background
time contamination were reproduced and fixed; regression passes. Final combined
run has no FAIL. `git diff --check` passes (line-ending notices only).

## Overhead: LIMITATION, not PASS

Reproducible recorder benchmark: `scripts/benchmark_observability_phase1.py`;
raw evidence `benchmark-windows.json`. Five fresh processes per mode,100k
synthetic operations. Median CPU disabled0.0625s/enabled0.671875s. Modeling that
delta at100requests/s on2cores gives0.03047% **for this microbenchmark only**.
Maximum traced allocation peak272017bytes; snapshot approximately9.13KiB;
tracked active slots capped2048. Tracemalloc is not worker RSS; RSS unavailable.

This does NOT prove <=1% total-worker CPU or <=8MiB extra RSS. Linux/container
quota A/B, complete middleware/listener/scrape load, realistic request mixture,
6h soak and actual RSS remain unmeasured. Activation is BLOCKED until those
targets are independently demonstrated or the owner explicitly revises them.

Other SKIP/LIMITATION: nginx binary integration not run; platform worker routing
and actual worker count unknown; no fleet Android timings, external collector
full load or platform I/O/quota measurement. Socket timeout is not an absolute
deadline against a slow-drip peer. Retention cleanup stops when collector stops.
Existing old diagnostics overhead remains when additions are disabled.

## Controls / rollback

`OBS_METRICS_ENABLED=true` is an explicit future opt-in. Optional
`OBS_CONTROL_FILE` must contain exactly `enabled`; missing/unreadable/other value
disables additions. Existing lag task checks every30seconds, so disable is delayed
if the event loop is itself blocked. A running request cleans its active slot
even if metrics become disabled. Startup environment changes may require a
future authorized rollout; no claim of automatic environment reload.

Stop external collector immediately. Switch control file to disabled through an
authorized existing control plane; no restart/business request needed. Do not
disable Governor or Mongo functionality. The new counters stop; the existing
diagnostics continue. Buffers are bounded and need no database cleanup. nginx
example is unreferenced and inactive; if separately activated later, its rollback
must follow platform review. Reverting application code requires a separately
authorized release, never this PR automatically.

## Android extension

`ANDROID_OPERATIONS_DESIGN.md` specifies navigation, Ready, shipping, receiving,
supplier invoice and scanner milestones; random correlation IDs; clock domains;
privacy/cohort cardinality; sampling/battery/network limits and tests. **Design
only**, as requested in the scope update. Correlation propagation, backend stage
spans, mobile ingestion/UI/camera telemetry are not implemented in phase1 here.
Protected scanner scope requires RCA before instrumentation changes; existing
callback gaps remain unavailable, not fabricated measurements. No permissions,
scanner behavior or mobile performance changes.

Decision: draft PR suitable for code review. NOT ready for Production activation.
