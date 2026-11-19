# Visible refresh budget recovery after Aramex publication

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

The current investigation targets repeated whole-cohort scans in native
assignment checks, bundle preparation-piece selection, and saved receipt
return calculation. Each proposed change requires exact-output parity and a
deterministic work-count regression against the current production baseline.
The per-payment audit projector is deliberately unchanged: its synthetic
cost alone did not establish the observed reconciliation bottleneck.

This section is a work-in-progress source checkpoint, not release readiness.
Next: finish focused measurements and independent review, run integrated
financial/cancellation checks, preserve the verified source on the task branch,
then follow a fresh v5 A/B handoff only if ready. Shared-host status must be
checked again before any rehearsal or mutation. After deployment, require a
new saved snapshot and the same-cohort fee/wallet comparison, followed by a
second visible refresh proving no duplicate debit. Private financial records
remain outside Git. Issue1006 is full and its attempted body update did not
persist in fresh reads; PR2000 is the reliable current remote checkpoint.

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
