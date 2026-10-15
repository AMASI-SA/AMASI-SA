# PR1313 final-hardening checkpoint

Base freshly fetched: cd554446e3dbdf618521f3e122f9b58b8e4e9035 (contains #1312).
No inference about Production deployment. No Production requests, writes,
activation, merge, prepare, prepublish or deploy are part of this checkpoint.

The new Ubuntu24.04 workflow checks out the PR head SHA explicitly and retains
HEAD/TREE with tests, packages, Linux CPU/RSS measurement and isolated nginx
rehearsal. Mongo8.0.12 replica set runs only on runner loopback. No credentials
or deployment permissions; contents:read only. Final results must be read from
that exact run before acceptance; committing this file does not mark them PASS.

New process tests distinguish EventLoop/API/Mongo/Governor metrics by worker,
restart/reset and no default-enabled recording. Collector connection refusal and
storage failure happen before workers successfully serve synthetic APIs. Actual
Mongo pool/commit tests remain separate from callback-based worker coverage.

Linux benchmark: five alternating fresh-process legacy/disabled/enabled triplets,10s
measurement +1s warmup each,100 requests/s. ASGI/listener/Governor/lag/snapshot
costs included; Mongo events synthetic. CPU measured from process time and RSS
from /proc + getrusage; worst paired delta tested against1% of the explicitly
reported CPU denominator and8MiB. Incomplete/unpaced measurements cannot PASS.
Even a bounded-workload PASS does not establish a full Production workload or
six-hour-soak budget. A separate default-disabled versus pre-PR module baseline
is measured using exact base source; it is still a bounded synthetic workload,
not the complete pre-PR server. Disabled tests prove no histograms or network.

nginx rehearsal uses temporary loopback ports/config/logs, actual nginx -t and
requests exercising200 with upstream delay and504 with read timeout. Access log
schema rejects query/header secret/customer/invoice canaries. Only own subprocess
is terminated; no Production nginx configuration is touched.

The inherited #1312 source contract originally froze Governor byte-for-byte.
PR1313 necessarily adds telemetry to that file. The test now allows only the
exact old or reviewed telemetry-only normalized SHA256; all other protected
files remain byte-identical. No reducers, budgets, accounting or runtime policy
are changed. Existing Governor cancellation/admission tests still run. This is
an explicit narrow contract update, not a skipped test.

Local Windows preliminary verification:66 passed,5 subtests passed. Linux
results are pending the exact-head workflow; prior Windows microbenchmark is
historical and must not be substituted for new Linux evidence.

Rollback unchanged: collector stop; optional control file disabled, checked by
existing loop every30seconds (longer if loop blocked); no business behavior
change. Instrumentation remains default-off. Android design stays unchanged and
is reserved for a separate task; no Android instrumentation here.

Next safe action: push this checkpoint, wait for all exact-head checks, inspect
artifacts and report all FAIL/SKIP/LIMITATION. Stop for review, never activate.
