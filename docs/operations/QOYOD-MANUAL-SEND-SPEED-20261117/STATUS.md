# QOYOD-MANUAL-SEND-SPEED-20261117

## Scope and source

User requested a direct manual send action for each unsent order in Mezan 2,
faster bulk sending (100 orders per attempt), and the real customer name instead
of a generated customer/order label. Repository: AMASI-SA/AMASI-SA. Task branch:
`codex/qoyod-manual-send-release-v2-20261117` (supersedes the preserved first
candidate branch `codex/qoyod-manual-send-speed-20261117`). Current production
base J: `d7c9ed77137bed0948f4d32a34c6bf571d75e6f8`; original base was
`8df479ef24b4f8847c26ba18d48e3248f7b673cf`.

Latest user constraint: the update must not make the system heavy or slow.
The verified implementation checkpoints and current release PR are recorded
below. The final source/intent pair will be recorded on PR #1971 after freezing.

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
the manual-runner suite. Release CI is tracked on PR #1971; final exact-B gates remain required.

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

## Authorized release continuation — PR #1971

The owner requested continuation after the explicit named-destination
upload/merge/publish authorization request. The earlier automatic-approval
rejection was respected until this continuation. Native Git push then failed
because this runtime has no Git credential prompt; the linked GitHub connection
successfully uploaded the verified source tree.

Canonical initial remote checkpoint:
`0bf73122e6ee05757511de345892520b53edb651`, tree
`3045c804cb7e20b52f9ece761311742b5d2e6300`.
PR: https://github.com/AMASI-SA/AMASI-SA/pull/1971
The remotely fetched tree is exactly equal to local complete checkpoint
`bf40f39b16c9fef88218878c8a03686d9aa6c664`; the original local history is retained
on `codex/qoyod-manual-send-speed-20261117-local-history` and in the earlier patch.
The tests above exercise this identical implementation content.

Original local milestones remain available:
`171f0f98e9c1accd9b78b1d61dc6dda54565f0c4`,
`920877e59b9e35eda97531ca4134a0835e54cc71`, and
`dd1a89dfd939a42a87959da894d31d35ac8473c1`.

Production ref freshly remained
`8df479ef24b4f8847c26ba18d48e3248f7b673cf`; official shared /app status was
read again and reported active:false. No shared tracked source, lease or
production runtime has been changed by this task.

Issue #1006 remains at its 2500-comment limit and rejected the previous handoff
comment. This status and PR #1971 are the current continuation records. Use the
PR's exact current SHA and CI state; do not treat this initial uploaded source
as a frozen release pair or reuse the inherited intent.

## Source CI correction and read-only production check

Initial source CI run 44161737690 passed 49 Plan-B tests but exposed a
pre-existing test false positive: the historical-count policy rejected the
seconds-per-minute factor in `_ABANDONED_RUN_AGE_SECONDS = 15 * 60`.
Both the runtime and test were byte-identical to production base 8df479e.
The test-only correction exempts only the right-hand time-unit factor in that
named timeout, independently requires the unchanged 15-minute timeout, and
retains negative controls for forbidden counts and monetary literals. No
production timeout or other runtime behavior was changed to pass the check.
The corrected policy plus controls passed six tests; the two Build20 files
passed separately, 49 Plan-B and 28 unified-backlog tests (77 total, both exit 0).
The local runs used the repository's existing mongomock compatibility shim
without shared /app environment or fixture-file writes. Fresh CI on the final
source and intent-only head is still required.

The live health identity was readable and verified before deployment: intended
current source 80e941b5b990ede691948babe4c46558fa5922a0; identity, critical hashes,
Frontend build, backend runtime source and release control source all verified.
One health request took 529 ms; this is a single observation, not a benchmark.
The shared environment does not expose the existing internal diagnostics token,
so authenticated production memory/admission diagnostics were not called.
No credential was printed, added, rotated or weakened. Production resource
measurements remain unavailable through that path.

## Release-gate dependency remediation

Source checkpoint `ae86bd812e2fca6edecbc3e574bef4c741917c51` corrected Build20;
run 44163686355/job 134323942863 passed 49 Plan-B and 28 unified-backlog tests.
Its real-Mongo run 44163686145/job 134323942177 passed 351 acceptance tests and
88 Salla tests on stock MongoDB 8.0.12 with the no-skips checks satisfied. The
manual UI job 134323941757 passed 105 tests in five suites. Security run
44163686044/job 134323943536 passed 4,230 frontend tests in 417 suites, then
blocked on five dependency paths for GHSA-fc5q-39xc-vjpf. The affected original
picomatch 2.3.2/4.0.7 manifests and lockfile were unchanged from production.

The verified upstream release/advisory evidence identified no official fixed
version. A concurrent repository task already implemented and independently
reviewed transparent local backports in PR #1970. This task adopts only the 28
security-related paths from its exact source
`d9964df9f07cee22806202311712d92e6bd717ba` (security commits 41b510c and d9964df).
No courier implementation or courier task status is imported or modified.

The two package majors remain separate, with honest private `mezan.2` identities,
retained MIT licenses and immutable npm source/integrity provenance. The repair
checks compiled repeated-glob bodies, preserves certified safe forms, and
literalizes unsupported ambiguous repetition; a linear unescaping correction
also addresses the donor's CodeQL findings. The new copy/consumer verifier checks
all installed runtime bytes. The strict audit evaluator, severity thresholds and
dependency groups remain unchanged. A clean registry audit is not used by itself
as proof that the private sources are safe.

Fresh integrated local evidence on Node 22.23.2 and Yarn 1.22.22:
- Frozen installation exited 0.
- All 210 security contracts passed, with no skips/failures/cancellations.
- Complete integrity checks verified three installed copies and eight consumer
  resolutions, preserving the consumers' original major versions.
- The unchanged audit evaluated 409 production dependencies: zero advisories,
  exceptions NONE and blocking 0, exit 0.
- Full integrated application frontend regression passed 4,230 tests in all
  417 suites, exit 0 (177.901 seconds), on the adopted dependency graph.
- The operational dependency check passed from its documented frontend cwd.
  An initial invocation from the repository root matched no source paths; this
  was corrected in the command, not by modifying product code or the test.

Independent provenance verification redownloaded and SHA-512-verified both
published npm tarballs and checked every original and patched runtime hash.
The frozen unescape oracle was independently reproduced from both upstream and
both patched implementations for all 37,449 bounded inputs per implementation.

Compatibility qualification: the released upstream suites were run unmodified
against pristine and patched copies. Pristine v2 passed 1,972/1,972 tests; patched
v2 passed 1,901 with 71 failures. Pristine v4 passed 1,983/1,983; patched v4 passed
1,912 with the same 71 failures. These are real, documented restrictions of
complex repeated extglobs/raw repetitions, recursion opt-outs, rewritten nested
captures and backreferences; they are not represented as an upstream-suite pass.
Ordinary-glob cases passed, including all six ordinary-capture cases per major
and the quoting/escaping cases (20 in v2, 21 in v4). Static search found the
changed exact patterns only in security tests, with no application/config use;
this does not claim coverage of every dynamically generated pattern. The
fresh full application and consumer checks above passed. The imported source
was retained unchanged after provenance, static and compatibility review.

An optional additional adversarial-review agent was stopped by an automated
cybersecurity content screen; that unfinished extra matrix is not credited as
passed. The executed 210 existing security contracts, immutable-source checks,
current consumer/application regressions and fresh exact-source CI are the
acceptance evidence. No complete/general regex-safety claim is made.

The earlier governed source-A build for ae86bd8 completed two clean identical
builds (artifact tree 30909766c3583d9172d3032ffc2632c7d17c3bb2ab089021156be6abe22bab12)
and exact-source verification. It is historical evidence only: the added
security sources require a new final source commit, new governed build and new
intent-only B. No intent has been frozen for this task, and no production lease,
merge or publication has occurred.

## Historical first-candidate handoff

Complete exact-source CI and final source A, generate a fresh governed A/B build
and intent-only B, require all applicable exact-B gates and clean-clone adapter
rehearsals, then recheck production ref/shared inactive guard before the ordinary
merge and configured adapter handoff. Preserve A/B after freezing. The already
authorized publication still requires its own fresh prepare/prepublish lease,
one publish, a newer explicit completion and the official three-probe verify.

At authorized rollout, replace the old worker processes, verify that container
memory limits are visible, and observe CPU/memory, Mongo latency and ordinary
employee-page response times while the bounded manual queue is active. Do not
present local synthetic timing as proof of production response time.

Production changed by this task: **no**. User continuation authorizes the named
release; Release Guard v5, exact-source CI and production verification remain
mandatory before reporting deployment complete.

## Second release candidate: deterministic deadline-test scheduling

The first frozen pair remains intact and remotely recoverable on PR #1971:
source A `55773a216d9cf33087bb8d817bdfedef6ece966c`, intent-only B
`ba44b7f0fbcc659acd46844dc8e535cdd5aa28b9`, B tree
`7ad35590a051942b4e24cdbc5bbd5061b3c9e4a7`. Its governed two-pass build matched
the official source-A candidate artifact byte-for-byte. Both final-B clean-clone
adapter rehearsals passed on host Node 20.20.2 and 24.19.0. Final-B worker/UI,
memory, Build20 and security gates passed; the actual CodeQL scan-result check
134342260858 succeeded with zero new alerts and zero annotations. Review
submissions and inline threads were both empty; no external approval is claimed.

The final-B Operational Balance browser job initially failed at a registry
download (lru-cache HTTP 503) before running tests. Its one unchanged-head rerun
passed. Stock Eligibility run 44168594706 failed in both attempt 1/job
134341391833 and the one rerun/job 134347909856: 4,806 passes, seven subtests and
one failure in each. The repeated failure was the existing recorded-courier-fee
history test's `[deadline]` case waiting for its worker checkpoint. No further
unchanged-head retry was attempted, and no failed gate was waived.

The test and its five relevant operational runtime modules were byte-identical
to production base. The same exact case passed in the broader Operational
Balance job on B (artifact 14365944608): 0.211 seconds, all 116 cases in that
module passed, and the full report contained 5,126 tests with zero failures,
errors or skips. That gate uses Python 3.11/pytest 9.1.1; Stock uses Python
3.12/pytest 9.0.3, so the cross-pass is qualified rather than treated as an
identical-environment reproduction.

The test's 150 ms timer surrounded the complete collector, including earlier
reads/preparation, although its intended subject was cancellation after the
history worker had started. A deterministic local regression inserted 200 ms
of setup before the actual collector. Before correction, this reproduced the
same `entered.wait` assertion failure (exit 1, 3.20 seconds). The first local
invocation had an incomplete PYTHONPATH and stopped at an import; the corrected
command added the repository parent and produced the meaningful red result.

Only the test's scheduling is corrected. It follows the repository's existing
armed-timeout test pattern: a proxy local to `operational_demand_refresh.asyncio`
captures one real `asyncio.Timeout`, then reschedules it after the controlled
history checkpoint has been reached. The actual timeout must expire and the
request must still return HTTP 408, wait for its worker, stop at the tenth
checkpoint, preserve all input data, release only its own lease and leave no
detached tasks. Production REQUEST_SECONDS remains 85. The original 2/3-second
watchdogs, source-read budgets and unrelated asyncio timers are unchanged.
Failure cleanup releases the worker and awaits the request task. A new delayed
setup parameter keeps the reproduced regression covered.

Fresh verification on the read-only prepared Python 3.12 environment:

```
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=..:.:tests python -B -m pytest \
  --noconftest -p no:cacheprovider -q --tb=short --asyncio-mode=auto \
  -o asyncio_default_fixture_loop_scope=function \
  tests/test_operational_balance_recorded_courier_fee_index.py \
  tests/test_operational_balance_source_conversion_lifetime.py \
  tests/test_operational_balance_demand_refresh.py
```

Result: **140 passed**, exit 0, 4.52 seconds. Independent review passed the scoped
timer design. Two independent in-memory negative controls both failed at the
intended assertions: suppressing lease release failed the lease-empty check;
suppressing the preparation join failed the blocked-request check. These were
meaningful red controls, not import failures or a timeout of the test harness.
No runtime source was modified for either control, and no mutations were kept.
`git diff --check` passed.

## Current continuation and next safe action

The second candidate branch starts from the original source A, not from B, so
it retains the reviewed base J's intent without rewriting the first frozen pair.
The only source changes after original A are this status and the deterministic
deadline test above. All Qoyod feature code and security backports are unchanged.
The new canonical source A, PR, fresh two-pass build and intent-only B will be
recorded on the successor PR. The old PR is draft and must not be merged.

Complete the new source checkpoint, freeze a fresh A/B pair, require every
applicable final-B gate and both clean-clone adapter rehearsals, then recheck the
production branch and official shared inactive guard before an ordinary
tree-equivalent merge. Run the configured adapter in /app before creating an
owner lease; then prepare/prepublish, publish once, observe a newer explicit
success, require the official three-probe verification and final active:false.

The latest shared check remained tracked-clean on production base 8df479e,
with 120 untracked platform files untouched, host Node24.19.0/Yarn1.22.22,
and official Release Guard active:false. No shared source mutation, lease,
merge or publish has occurred. No production accounting send or load test was
performed. User release authorization persists; no repeat permission is needed.

Production changed by this task: **no**.

## Production advanced before the second candidate was frozen

The test-only second-source checkpoint was uploaded as
`4cd6865a32e30f38bc3067d7893d991bb82870b2`, tree
`e255db9310b0d4fc77e9daf9307e5b65fc730b53`, and PR #1972 was opened. The old
PR #1971 was closed without merging. Before any second intent was frozen,
fresh fetch showed that the independently authorized courier PR #1970 had
merged to production as `d7c9ed77137bed0948f4d32a34c6bf571d75e6f8`. Its reviewed
source is d9964df9f07cee22806202311712d92e6bd717ba, intent-only B is
4f79a85cb6fb80e50103d23d5b8538febaf88508, and merge tree is
97cab3cf1d73b39223a4f611be1ff4e638831048. All of that release's final-B gates
were accepted in its own PR. Its production verification remains its owner's
responsibility.

Official shared guard now reports active:true for actor
`courier-history-1970-20261117`, prepared at 2026-11-17T20:49:17.844027+00:00,
deployment d7c9ed7 and runtime identity
rg5-6ebdaece1a49b074f899db8024e68a790da8cc641b66206a2e4abd18d4be6d9b.
This task does not alter, clear, reuse or publish that lease. No local release
rehearsal or shared source mutation may start until a fresh official status is
inactive. Isolated source integration and tests can continue.

The unfrozen 4cd6865 checkpoint is preserved remotely on
`codex/qoyod-manual-v2-before-courier-20261117` and locally. A hypothetical
ordinary merge was inspected without updating refs: although its intent bytes
equaled new J, full intent-path history contained the merge, and the official
source-base resolver correctly rejected it. Therefore the new source is made
directly on J with one parent, transferring only this task's 19 differing paths.
Its combined tree before this status update was
750583f3289ee637a47ecbbb25176a74bec4ce81, exactly equal to the independently
computed merge result. All courier code/status and security backports remain
byte-for-byte inherited from J; the reviewed J intent is unchanged. No frozen
A/B pair was rebased or rewritten. The owner task branch may be advanced from
the preserved, still-unfrozen 4cd6865 checkpoint with an exact expected-SHA lease.
The same three focused suites were rerun on this combined production base:
140 passed, exit 0, 4.98 seconds. No new dependency installation was needed.

Current continuation: PR https://github.com/AMASI-SA/AMASI-SA/pull/1972.
Record the new canonical source A and new J there, verify empty intent history
after J and run the official candidate source-base resolver. Require fresh
combined-source CI. After the other owner's lease is closed, perform the new
governed build/freeze and final-B gates, then follow the ordinary merge and own
guarded publication protocol. Never overwrite or bypass the courier release.

Production changed by this task: **no**. No Qoyod accounting action was taken.
