# Visible refresh budget recovery after Aramex publication

## Current continuation: profit preparation during source collection —19 November2026

Task branch: `codex/aramex-profit-preparation-overlap-20261119`.
Verified production base: `f91fc7bfbb60b47e5954f4178f9b7dea6eb7fa46` (PR2007).
The source commit containing this checkpoint is the new source candidate A;
its exact remote SHA will be recorded in the task PR before intent freeze.
The prior source/intent pairs and unrelated compact-statements release remain
unchanged and remotely recoverable.

### Published predecessor and lease state

PR2007 was merged normally, built through the configured adapter and published
once. The UI displayed newer green `Deployment finished`, version17b9b08.
The unchanged original production verifier completed exit0, verified:true,
checks:3 with source `b983035c56b69c509d0e1895a5546cb4544d462a`, deployment
`f91fc7bfbb60b47e5954f4178f9b7dea6eb7fa46`, runtime
`rg5-cf3e6092b384d585d2dc4478f1f9a00591e237d1955ba2947dc47fa7df846a34`
and boot `2026-11-19T01:06:39.793185+00:00`. The next original guard status
returned active:false, actor:null. Our matching lease was closed by successful
verification; no other actor's lease was cleared. Production changed:yes for
the predecessor release; this follow-up has not changed shared `/app` or
production and holds no lease.

A normal visible refresh started at `2026-11-19T01:11:48.477Z` and reached the
unchanged85-second deadline. The UI explicitly retained the last saved report.
Sanitized timings: native evidence6.958s, snapshot load13.959s, source
collection42.080s, reconciliation12.734s; profit was cancelled after21.898s.
The latter is time until cancellation, not proof of completed profit latency.
Nested stages must not be summed as independent durations. No new financial
snapshot, changed cohort, wallet result or idempotency acceptance is claimed.

### Narrow repair and independent verification

The same fixed-period profit reader now starts beside source collection in
an owned visible request. Collision checking, collection checkpoint/error
mapping, return attachment and reconciliation stay ordered within the other
child. The existing shield/cancel/join barrier must settle both children
before bank claims, catalogs, aggregate mutation or CAS. Ordinary and detected
snapshot-session callers retain the serial source→reconciliation→profit path.
No deadline/read budget, financial arithmetic, source authority, tariff,
wallet binding, history, route, scheduler or financial writer is changed.

Changed application file: `backend/operational_balance_service.py`.
Changed existing tests: `test_operational_refresh_parallel_profit.py`,
`test_operational_balance_refresh_stage_timing.py`, and
`test_operational_balance_source_stage_timing.py` under `backend/tests/`.
The two diagnostic fixtures retain strict source query/projection oracles and
explicitly hold and join the now-concurrent profit reader on source failure.

Final affected ten-module suite:355 passed,0 failed,0 errors,0 skipped,
exit0,13.97s; two pre-existing Pydantic warnings. Independent reviewer ran17
ownership/overlap cases and15 changed timing cases, all passing, and verified
final JUnit counts. Frozen-production negative control fails both new overlap
orders specifically at the barrier waiting for early profit. `git diff --check`
passes. Four-file reviewed binary diff SHA256:
`cc6c9bb80abbf0d5130c98dad42d88038bc46ee41be11cd184a370274b01c3a4`.
Reviewed service SHA256:
`0cf3bb0585a266bf9deefaca7ff33a0b26665130dc4d12a70c4951949ef1c97d`.
No blocking independent review finding remains.

### Limits and exact next safe action

Combined reads can reach8 across two separately bounded four-read groups;
no additional reads are introduced. Earlier profit observation does not
create a database snapshot. The existing raw Motor to_list and hidden-session
detection limitations remain; the tests prove coroutine/cooperative-thread
cleanup at this barrier, not every underlying Motor executor operation.
The concrete visible production route introduces no hidden session here.
The observed prior critical path leaves about9.27s for post-join work if profit
finishes early and resource contention does not slow collection; only a live
saved refresh can establish that this is sufficient. Normal incomplete-profit
source results retain their existing explicit quality flags.

Preserve source A remotely, run the governed two-pass A build and freeze a new
intent-only B from this exact current production base. Run required exact-B
CI and independent artifact review. Before any shared-host rehearsal or source
mutation, inspect original Release Guard status and honor any new owner's
lease. Follow the normal merge, configured adapter, own prepare/prepublish,
one authorized publish, original three-probe verification and inactive-status
sequence. Then perform two visible saved-report refresh acceptances with
snapshot-bound private courier/accounts/movements evidence. Keep customer,
order and financial data outside Git. The exact next command after source
checkpoint is the governed toolchain ensure/install/build:release sequence;
there is no current publish reservation.

---

## Integrated with compact statements production — 19 November 2026

Production advanced to PR2005 merge `27e29d3ab5c6c08548780e3dd1ac177021be65f1`
while PR2006's original source/intent pair was being verified. The original
PR2006 A `c8e59474aff34ce2bb80b7f13c3b6e4d5238e32c` and B
`3b7f2aab21d9142f514494f8a5dce32f682e2a45` remain immutable and recoverable.
No exact-B CI ran on that conflicted PR; none is credited as passing.

This continuation uses new branch `codex/aramex-refresh-integrated-20261119`,
based directly on the new production merge. It copies the exact tested source,
regressions and benchmark evidence from the preceding A without old intent-only
history. The only additional change is this task record. The current production
intent remains unchanged in bytes and ancestry until a newly built intent-only
commit is created. No published source or frozen commit is rewritten.

Independent review found no code-path overlap: all six changed refresh modules
and their direct source/admission/runtime dependencies are unchanged by PR2005.
Its compact statements, scoped review permissions, frontend and added workflow
coverage are retained. Fresh coupled verification passed **283 tests**, exit 0,
in 30.20 seconds across 11 statement, workspace, access, review, shipping and
lookup modules, including all 111 bounded-lookup regressions. The JUnit report
contains no skipped or failed case. `git diff --check` also passed. Exact current-head CI
with isolated Mongo remains required; the earlier local 930/81/10 result below
is not a green database gate. Build proof and artifact identity must be regenerated
for the new frontend source; old PR2006 artifacts are not reused.

The PR2005 owner has its own active publication in progress. This task creates
no shared-host change, rehearsal, lease or financial write during that release.
Continue local preparation, then require a fresh original shared status showing
`active:false` before any shared-host rehearsal or source change. The new PR
records source A, intent-only B, exact verification results and next actions.
Live refresh, saved-snapshot acceptance and duplicate-debit checks remain pending.
Production changed independently through PR2005; this task has not deployed again.
Issue1006 remains saturated and its attempted body update did not persist; this
status and the new task PR retain continuation evidence.

## Continued from the real visible timeout — 19 November 2026

PR2000 was merged normally as
`77d824a780df582b5c32f4dda87a959e35e1ed1e`, published, and verified by the
unchanged official guard with `verified: true`, `checks: 3`. Its own lease
closed; the following original `status` returned `active: false`. The original
source A `c0ce20a1273574be833261218335a4b6e527d658` and intent-only B
`3965128df57168b3ce69a7f6f0cd0ce6fc16d8c5`
remain immutable and are not used as a new release input.

The independently authorized PR2001 deployment now serves source
`213a284901fb49b8c79948669cf67cc37cabb409`, with all Aramex and refresh
application files unchanged. Its normal production merge
`c3c1cd26d93ab24d6e08003278b9910526055cc7` is the base of the isolated task
branch `codex/aramex-refresh-source-cost-20261119`. The PR2001 owner has also
recorded official three-probe verification and an inactive lease. No other
conversation's lease or source is changed by this follow-up.

The user explicitly requested that the assistant press the financial refresh.
A visible request started at `2026-11-18T23:42:37.992Z`. Its UI and sanitized
console trace at `23:44:05.160Z` confirmed the existing backend timeout before
the aggregate save. The report remained at its previous saved timestamp.
No newly charged cohort, wallet balance, or duplicate-debit acceptance is
claimed. An earlier generic failed attempt is not attributed to visibility
or the preceding deployment without evidence.

| Stage | Milliseconds | Outcome |
| --- | ---: | --- |
| Native evidence | 9,103.782 | Complete |
| Snapshot load | 12,752.149 | Complete |
| Primary source reads | 14,633.703 | Complete |
| Bundle sources | 7,823.014 | Complete |
| Order sources | 17,207.289 | Complete |
| Source collection | 42,797.508 | Complete |
| Profit snapshot | 20,216.016 | Complete |
| Reconciliation | 20,382.566 | Cancelled |
| Aggregate | 76,009.020 | Cancelled |

Stages are nested and the two preparations overlap; these durations must not
be added as independent work. The existing 85-second request budget,
visibility/lifecycle checks, source coverage, immutable money evidence and
atomic aggregate save remain requirements.

The source repair now removes three repeated whole-cohort scans:

1. Both fee producers reuse the collector's existing delivery assignment
   index, keeping exact raw-number matching and the first-match predicate.
2. The bundle adapter groups preparation pieces by its own raw receipt keys,
   retaining original row references/order and the existing receipt mapper.
3. The reducer groups source returns and saved return facts once per attempt.
   Successful insertions and rollback advance that same lookup; amount
   validation, receipt admission and summation order are unchanged.

Each optional index falls back to the original scan for unsupported shapes.
The lookups remain request-owned and observe cancellation; none enters saved
state. Independent reviews found and verified corrections for two lazy-error
compatibility cases before source completion. There are no outstanding review
findings. The per-payment audit projector is deliberately unchanged: its
synthetic cost alone did not establish the observed live bottleneck.

The tested assignment/bundle milestone is remotely recoverable at
`f972feda3f509d1fe6adc8bf41cd5d62871d8f09`, tree
`49025f540ab1e23158e3c92b7ca9315d8fd79c33`, in
[PR2006](https://github.com/AMASI-SA/AMASI-SA/pull/2006). The commit containing
this completed source record is candidate A; its exact SHA is recorded in the
PR before any intent-only B is frozen. The preceding WIP is not a deployment
input. No source changes are made to the already frozen PR2000/2001 releases.

### Fresh source evidence

- Root integrated run: 1,021 cases collected in 33 affected modules;
  **930 passed, 81 skipped, 10 environment failures**, exit 1. All ten failures
  are the unchanged real-Mongo completed-shipping review cases: their explicit
  isolated local target refused connection before assertions and during
  cleanup. The 81 skips also require isolated Mongo. Do not treat these as
  passes or silently exclude them; exact-B real-Mongo CI remains a release
  gate. All 111 new regressions passed without skips, as did parallel-profit,
  cancellation, fee/wallet, replay and source-coverage tests.
- Independent review reran both original compatibility repros against the
  frozen production code and confirmed their corrections. Source snapshots
  and ordered full outputs remain equal; malformed values retain their
  original validation/error boundary.
- AST comparison against production confirms prepaid `apply_events`, deferred
  `apply_deferred_events`, `reconcile_credits`, `_piece_map`, and
  `map_financial_receipts` are unchanged. `git diff --check` passes.
- The assignment benchmark compares full original producer modules, includes
  index construction and checks entire events plus input immutability. For
  5,000 calls / 20,000 unrelated assignments, three-run medians were
  11.0340 to 0.6450 seconds (prepaid) and 8.7895 to 0.9469 seconds (deferred).
  Separate 128-by-128 work counts fell from 16,384 to 1,152 row accesses.
- The bundle regression uses actual frozen authority and receipt mapping:
  80 lines / 320 pieces fell from 25,600 to 320 mapper row visits with identical
  entire adapter output and unchanged synthetic database/input rows.
- Full reducer replay with 2,048 saved receipts and 14,336 facts measured
  3.506115 to 0.660890 seconds. Prefix checks fell from 29,505,536 to 161,792;
  the candidate work doubles exactly as the cohort doubles. An additional
  20,981,206-byte synthetic saved-history case retained the complete ordered
  JSON and ran in 0.627878 to 0.447833 seconds.
- Reproducible scripts, source hashes, ordinary timings and separate work
  profiles are retained under `scripts/testing/operational_refresh_budget/`.
  These measurements establish removed repeated work; they are not a claim
  that the live full refresh now completes within 85 seconds.

A fresh original shared `/app` guard returned `active: false` during this
continuation. This task has made no new shared-host source change, lease,
publication or financial snapshot save. Next: retain candidate A, build/freeze
a fresh intent-only B, require all exact-B CI including real Mongo and the
governed clean-clone adapter, then use the authorized v5 handoff. Shared-host
status must be checked again before any rehearsal or mutation. After deployment, require a
new saved snapshot and the same-cohort fee/wallet comparison, followed by a
second visible refresh proving no duplicate debit. Private financial records
remain outside Git. Issue1006 is full and its attempted body update did not
persist in fresh reads; PR2006 and this task record are the current remote
checkpoint. PR2000 retains the earlier publication and lease-closure evidence.

## Resumed on current production — 19 November 2026

The owner requested continuation of the pending release and live accounting
acceptance. Fresh remote review found PR1995 still Draft at
`da0d76fcbca3722e072129ac912b2368ef4f0566`; its tree matches the earlier local
intent commit `70fa8a93f1b27026a72ab971028e09c99daeab1e`. PR1997 has since
been merged and published. Its current production commit is
`80b43ca2f4699fecd895f34a24f77fc997234417`, source
`81da7ef6ed7a7e200d844aaf0bdd7acc5ce60e85`. The actual shared Release Guard
reported `active: false` before this continuation. No old lease is reused.

The fresh task branch is `codex/aramex-refresh-current-20261119`, based directly
on that production commit. It preserves PR1997 and applies the exact reviewed
source diff from `d3632a33843e076dd7824933528dcc7d897bc32a` through
`b724b1d09f78e66e718e101f0d3c9eeec91c3170`, without carrying the previous
intent-only history. This is required by v5's full-history J-to-A contract;
the original source and PR1995 remain unchanged and recoverable.

The existing operational CI collector also now explicitly runs the two new
`test_operational_refresh_*` modules and rejects their absence. They were
covered locally before, but the existing `test_operational_balance*.py` glob
did not collect them. No application behavior was changed in this resumption.

Issue1006 was read and is saturated at 2,500 comments; its current body ends
with older November12 work and contains no PR1995 continuation. This task
record and the new PR are the bounded continuation evidence. Fresh integrated
tests, a new source A and intent-only B, current-head CI and a shared-host
rehearsal are required before publication. Live saved-snapshot, Aramex cohort,
wallet and repeat-update acceptance are still pending; no new financial result
or production change is claimed at this source checkpoint.

Fresh verification on this integrated tree: 167 backend cases passed, including
both new preparation/issue modules, Aramex prepaid and reporting invariants,
visible-request cancellation and the current live-source reader; 15 cases in
the two changed live dashboard component/mobile suites passed. Both commands
exited 0. The frontend suite emitted pre-existing `act` warnings in the campaign
hover fixture. The workflow parses and `git diff --check` passes. All five
application/regression files are byte-identical to reviewed source
`b724b1d09f78e66e718e101f0d3c9eeec91c3170`.

## Active checkpoint — source complete; release verification pending

The owner authorized the Aramex current-status rule, implementation, merge,
publication and live reconciliation. The accounting rule is published and its
tested application files are unchanged in the current production descendants.
Financial acceptance remains incomplete because two explicit visible refreshes
reached the existing 85-second deadline before the aggregate snapshot commit.
No new financial result is inferred from completed intermediate calculations.

- Repository: `AMASI-SA/AMASI-SA`.
- Task branch: `codex/aramex-refresh-budget-20261118`.
- Verified starting production commit:
  `d3632a33843e076dd7824933528dcc7d897bc32a` (PR1994).
- Earlier Aramex source PR1991 merged as
  `ce94ce01b0e631a6373ee3524119f26959bf89a4` and was published through PR1993.
- Verified published descendant:
  `8761f7b2f80faec29638e1438b8fedefcec64a28`, source
  `84ae882f78ae954449cc4f3380b17a8ff808cb3a`, runtime
  `rg5-c8f8108755b4a5d612b50e91902fbe83b014b80ce076b3372408f1bf41fc286b`.
- The unchanged official verifier completed three rounds at
  `2026-11-18T19:11:43Z`. Fresh live health/source hashes matched before the
  two refresh attempts. PR1994 has an independent release owner; this task has
  not changed the shared host or its release lease.
- The preceding WIP checkpoint is remotely recoverable as
  `22ba0a50bbac59f664156e0ffe04690cd8424421`.
- The commit containing this updated record is the completed source candidate A.
  Its exact remote SHA is recorded in the task PR before any intent is frozen.
  Source A alone is not a deployment input.

## Observed failure boundary

The first request completed source collection, pure reconciliation and profit
preparation, then timed out before snapshot comparison/packing/commit. The
targeted second request reduced native evidence acquisition but still timed
out during profit preparation. These were normal authenticated UI requests;
the second one did not bypass full financial reconciliation.

The sanitized timings show substantial independent reconciliation and profit
work. They do not establish the live new-fee cohort or a per-order cost. The
prepared prepaid charges and wallet movements remain in memory until the
ordinary atomic aggregate save. Independently acquired source proofs may have
been saved by their existing adapter CAS; they are not a saved financial result.

## Narrow implementation

1. Reuse the existing exact-equality diagnostic index in the pure reducer and
   final party-name projection. Each wrapper belongs to one calculation attempt
   and is never stored in the financial state. Unusual rows retain ordinary list
   equality and exception behavior, rich rows and original ordering.
2. Overlap pure reconciliation with independent profit source preparation only
   inside an owned visible request without a snapshot session. Profit receives
   only the fixed owner/opening period and the same observation timestamp.
3. Shield the preparation group; on failure, timeout or cancellation, cancel
   unfinished children once and join the group and all children with the
   existing cancellation-resistant helper. Both results are required before
   financial claims, catalogs or the aggregate CAS continue.
4. Ordinary callers and snapshot-session readers keep sequential behavior.
   Authorization, 85-second deadline, source/read budgets, financial admission,
   immutability and aggregate commit gates remain unchanged. No new scheduler,
   polling loop, financial API or cross-request cache is introduced.

The repeated prepaid audit projection was measured separately but is not
changed: its balance/metadata semantics are outside this narrower repair.
Thread and profit CPU work may still contend for the GIL, so structural overlap
is not a promise to save the entire shorter stage. Live acceptance must inspect
profit quality/source errors as well as the new saved timestamp.

## Verification at this checkpoint

- Final integrated suite: **632 passed**, zero failures/errors/skips, exit 0.
  This includes the earlier 67/392 service slices, the affected reducer/provider
  coverage, Aramex prepaid/report invariants, profit projection/source/salary,
  request lifetimes, source cancellation, archive batches and all new regressions.
  Two pre-existing Pydantic deprecation warnings were emitted.
- Nine new reducer tests preserve full issue-list behavior. The deterministic
  workload test failed on the frozen base with 1,499,500 containment-row visits
  for 1,000 existing plus 1,000 new issues; the candidate passes its 40,000-work
  budget. There is no elapsed-time assertion in the test.
- Seven party-name cases preserve exact names, duplicate/rich/unusual review
  rows, money/facts, linear index work and cooperative cancellation.
- Fifteen parallel-preparation cases pass: both completion orders, exact copied
  period, either fatal failure and secondary cleanup errors, deadline/disconnect,
  repeated parent cancellation, revoked permission, fresh preparations per CAS
  retry, unchanged ordinary/session serial behavior and explicit incomplete
  profit output on its existing soft source timeout.
- An independent reviewer identified a repeated-cancellation risk in an initial
  unshielded group and it was corrected before source freeze.
- Process-local negative controls prove the original sequential path fails the
  overlap test and removing the new shield fails repeated-cancellation cleanup.
- Independent static review of both app files found no remaining blocker.
- Synthetic measurements and their reproducible runner are retained under
  `scripts/testing/operational_refresh_budget/`. With 12,000 seed issues and
  12,000 new issues, baseline/candidate whole reducer CPU was 4.463/0.202 seconds
  with entire output equality and unchanged inputs. This is not live latency.
- No deployment of this follow-up has occurred. No fee/payment arithmetic,
  tariff, wallet binding or historical pending classification was changed.

## Next safe actions

Preserve the tested source A, then build/freeze a fresh v5 intent-only B on the
current reviewed production base. Run all required exact-source gates. Before any shared-host
work inspect Release Guard and honor the current owner's active lease.
After an authorized deployment and unchanged official verification, perform
the explicit live refresh, compare the same private order cohort and fees, and
verify repeated-update idempotency. Customer/order data and financial totals
remain outside Git in the private verification report.
