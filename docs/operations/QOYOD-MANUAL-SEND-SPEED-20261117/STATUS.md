# QOYOD-MANUAL-SEND-SPEED-20261117

## Scope and source

User requested a direct manual send action for each unsent order in Mezan 2,
faster bulk sending (100 orders per attempt), and the real customer name instead
of a generated customer/order label. Repository: AMASI-SA/AMASI-SA. Task branch:
`codex/qoyod-manual-send-speed-20261117`. Freshly fetched production base:
`8df479ef24b4f8847c26ba18d48e3248f7b673cf`.

Latest user constraint: the update must not make the system heavy or slow.
Verified implementation checkpoint, including the load safeguards below:
`dd1a89dfd939a42a87959da894d31d35ac8473c1`.

Implementation is isolated from the other courier task and the shared /app.
No production data, provider invoices, existing contacts or release identity
were changed. No Release Guard lease, merge or publication was performed.

## Implementation

- Queued send and retry use the existing authoritative unified payment/customer
  projection after exactly one current Salla refresh. Definite local projection
  refusals remain correctable by a later explicit action.
- Current flat buyer identity is normalized at the common new-invoice boundary.
  Missing/known generated names are refused before provider writes. Existing
  trusted invoice observation and payment-only recovery are preserved.
- One task-local HTTP pool per provider identity is reused inside each durable
  job and closed on completion, error or cancellation. Per-write lease/authority
  checks and fresh invoice-absence checks remain in place.
- The manual worker yields immediately after processed work; idle/error polling
  retains its existing backoff. A dedicated Mongo server-clock runner slot now
  permits one valid manual runner across updated workers, while the existing
  automatic sender retains its separate ownership and behavior.
- Before claiming new work, the worker checks the existing memory governor.
  Blocked/critical pressure leaves orders queued. A financial send already in
  progress is not cancelled because of a subsequent memory-pressure signal.
- Frontend adds the direct row action, three queued-ahead job
  observers, a 100-order cap and per-order progress. The final review correction
  excludes reviewed outcomes from subsequent batches while retaining explicit
  row retry/follow-up. Leaving the page stops future admissions; accepted jobs
  remain durable.
- Identical progress responses reuse the current row/notice state, removing
  continuing whole-page render commits while a job's status stays unchanged.

## Final local verification

All provider calls in development tests are mocked; no production load test or
accounting send was performed. Baselines reproduced the missing unified row
action, flattened-name loss, one-second worker gap and missing HTTP reuse.

The final combined backend run passed **294 tests**, exit 0 (6.36 seconds),
across these 15 files under backend/tests:

```
test_qoyod_manual_send_jobs.py
test_qoyod_manual_job_projection.py
test_qoyod_manual_customer_identity.py
test_qoyod_manual_http_scope.py
test_qoyod_manual_client_fail_closed.py
test_qoyod_manual_product_lookup.py
test_qoyod_manual_send_plan_b.py
test_qoyod_manual_currency_guard.py
test_qoyod_manual_payment_method_source.py
test_qoyod_auto_send_authoritative_payment.py
test_qoyod_auto_send_latest_payment.py
test_qoyod_failed_retry.py
test_qoyod_is_cod_scope_regression.py
test_resource_governor.py
test_qoyod_manual_runner_lease.py
```

Invocation: prepared Python 3.12 runtime, cwd backend, PYTHONPATH=.:tests,
pytest --noconftest -p no:cacheprovider -q
-o asyncio_default_fixture_loop_scope=function. Tests used a generated
process-local encryption key, synthetic API origin and provider mocks; proxy
variables were removed because the test environment lacks SOCKS support.

Final frontend verification: **51 tests across four suites**, exit 0
(3.459 seconds):

```
CI=true node node_modules/react-scripts/scripts/test.js \
  --watchAll=false --runInBand --runTestsByPath \
  src/pages/QoyodUnsentOrders.test.jsx \
  src/lib/qoyodManualSendJobs.test.js \
  src/lib/qoyodUnsentOrdersClient.test.js \
  src/components/qoyod/QoyodTotalsDiagnosis.test.jsx --no-cache
```

git diff --check passed. The existing background-worker CI workflow now includes
the four new backend test files and passes its existing disposable Mongo URI to
the manual-runner suite. Remote CI has not run for this task.

Independent review confirmed COD sends create no payment and payment-only
recovery creates no customer/product/invoice, for both send and retry paths.

The controlled 100-order comparison keeps provider processing sequential at
one order/second and client polling at five seconds. Actual promise completion
timestamps measured **500,000 ms before vs 170,000 ms after**. This proves the
removal of browser polling gaps under that workload, not live Qoyod throughput.
The worker also removes the old one-second pause after each processed job, and
transport tests prove four requests in two jobs use two closed pools instead of
four per-request clients. No fresh absence check or provider pacing was removed.

Review regressions were reproduced and corrected before delivery: missing-name
projection must preserve existing-invoice/payment-only paths, with positive real
invoice IDs required for that exemption; subsequent batches must skip review
outcomes so the first 100 refusals cannot starve order 101. Explicit per-row
follow-up retains the original durable key for unknown outcomes.

## Load safeguards and their evidence

The initial load review found that the old single worker was sequential only
within one process. Three queued browser observers could occupy different
existing worker replicas. Memory admission alone would not close that gap.

The new dedicated manual runner uses one document with Mongo server time,
90-second ownership, and the existing 15-second heartbeat. It is separate from
the automatic sender's lease. Ownership is checked before starting provider
work and immediately before each financial write. Runner renewal precedes job
renewal; lease loss preserves the existing conservative review handling. Cleanup
uses the exact token and cannot clear a replacement worker's slot.

Eleven acceptance cases executed against isolated real MongoDB 8.0.12 with two
independent clients and synthetic orders/providers. They confirmed one winner
from 24 simultaneous claims, three different queued orders executing with peak
active execution of one and then all progressing, server-clock ownership,
renewal/expiry/stale-token fencing, storage-error refusal, idle no-slot-write
behavior, memory admission/recovery, provider-write fencing, heartbeat loss,
and delayed claim acknowledgement blocking provider work. The synthetic test
databases were removed and the local Mongo process stopped after verification.

The local runtime lacks /sys/devices/system/cpu/possible, so the stock Mongo
binary failed in its CPU probe before starting. For these local acceptance
checks only, a private copy of the binary redirected that one literal path to
a private CPU-list file derived from process affinity; database logic was not
changed. The original binary and repository do not contain this adaptation.
CI must still execute against its stock MongoDB 8.0.12 replica set. The local
checks establish lease/query behavior, not production CPU performance or
replica-set failover behavior.

Three memory-admission regressions failed before the fix and pass afterward:
blocked/critical pressure leaves jobs unclaimed, normal pressure permits work,
and a pressure change during a financial operation defers the next order while
allowing the current result to settle. The existing governor's default block
threshold is 80% of the visible container memory limit; hysteresis resumes
below 70%. Missing cgroup limits return normal, and this is not a CPU limiter.

Two React Profiler regressions, through actual single/bulk polling behavior,
failed against the previous page and pass with the state-equality fix. They
confirm that repeated queued/running responses stop causing continuing page
commits and that a real state transition remains visible. React may perform
one bailout render after a state transition; no frame-time benchmark is claimed.

The first integrated backend run had 293 passes and one test-only UTC codec
comparison error (Mongo command time was naive UTC; collection time was aware
UTC). After normalizing the test's timestamps, all 294 passed. Product code was
not changed to accommodate the test environment.

### Boundaries of the performance claim

- The manual slot applies to updated workers. Deployment must replace/drain
  older worker processes that do not honor it before claiming the global bound.
- The separate automatic sender can still execute. A lease cannot recall an
  already-started remote request during a network partition.
- Three active observers normally make about 36 narrow status GETs per minute
  per active batch page, rather than about 12 with one observer. These endpoints
  read saved job/authority data and do not scan orders or contact providers.
  This is steady cadence, not a strict rolling-window cap.
- No new manual send/status polling starts just by opening the page. Existing
  recovery polling remains. Unmounting stops observations and new admissions;
  merely hiding the browser tab does not. Accepted jobs remain durable.
- Idle workers use the existing three-second backoff and perform no new runner
  writes or memory sampling when there is no queued work. Active work adds
  bounded ownership reads/writes in exchange for the cross-worker limit.
- HTTP reuse and removal of avoidable waiting improve efficiency; draining a
  backlog faster can still increase continuous resource use. Production CPU,
  memory, Mongo latency and employee-page response times remain unmeasured.

## Publication blocker

Local backend checkpoint: `171f0f98e9c1accd9b78b1d61dc6dda54565f0c4`.
Prior complete feature checkpoint: `920877e59b9e35eda97531ca4134a0835e54cc71`.
Verified feature plus load safeguards: `dd1a89dfd939a42a87959da894d31d35ac8473c1`.
The attempted git push was rejected by automatic approval review: uploading
private source/tests/workflow changes and creating/updating a GitHub branch
requires explicit user authorization for the GitHub destination. The feature
implementation request was not accepted as that authorization. Do not bypass
this rejection with another transport or connector. No task branch or PR was
created remotely; further upload is paused pending the user's permission.

An attempted continuation comment on Issue #1006 returned GitHub 403:
commenting is disabled on issues with more than 2500 comments. This local status
and source commits retain the exact work for the authorized follow-up. No claim
of durable remote checkpoint or GitHub acceptance is made.

## Next safe action

Obtain explicit permission to upload the completed change to AMASI-SA/AMASI-SA,
create the dedicated task branch and PR, then run exact-source checks. If merge
and deployment are also authorized, recheck the current production base and
shared Release Guard status, produce the governed A/B source candidate and new
intent-only commit, require all applicable gates, and perform the ordinary
reviewed merge/publication plus three-probe verification. Never reuse the
inherited production intent for these changed sources.

At authorized rollout, replace the old worker processes, verify that container
memory limits are visible, and observe CPU/memory, Mongo latency and ordinary
employee-page response times while the bounded manual queue is active. Do not
present local synthetic timing as proof of production response time.

Production changed by this task: **no**. Deployment requires the repository's
separate user authorization and Release Guard v5 source/intent procedure.
