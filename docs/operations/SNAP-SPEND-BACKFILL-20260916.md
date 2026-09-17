# Snapchat canonical spend backfill — 2026-09-16

## Restore normal synchronization window — September 17

The user accepted the completed August backfill and explicitly requested
restoring synchronization to the latest 30 days, verifying it, then closing
the task. Branch `fix/snapchat-restore-30-day-sync-20260917` starts from current
production `836d36b831c1cd8aa9915f543f159b4e160ea438`.

Implementation: native/async and canonical sync accept at most the latest 30
inclusive Riyadh dates (today plus 29 preceding dates), reject older/future
dates before synchronization, and preserve the existing scheduler cadence.
Read validation is separated from sync validation in both data planes so
the saved August reports remain accessible. No records are deleted. Snapchat
page adds a last-30-days preset and disables sync outside that window, with
an Arabic explanation. Existing date selection still reads historical facts.

Evidence: the new regression suite initially failed 6 and passed 2 on baseline;
after implementation, 63 affected backend tests pass (window, async mapping,
native sync, scheduler, heartbeat, campaign report, account-timezone report).
Standalone Node window checks pass including Riyadh midnight. `git diff
--check` passes. React page test, exact-head CI, governed v5 release and live
30-day acceptance remain pending. Do not close the task or claim deployment
until those pass. The read-only preflight in own Terminal 4 found `/app` clean
at the production SHA above and Release Guard `active:false`; PR1058's owner
has separately verified and closed that deployment. No Emergent chat used.

## Current operational checkpoint — 2026-09-17

This section supersedes the historical pending-release notes below. PR #1055
was merged and published as platform version `d63531b`, with explicit platform
completion on September 16 at 22:01 UTC. Release Guard passed three probes and
closed its own lease (`active: false`):

- source A: `41e71a0e567aebf83d9794100bccda7b804bf1dd`;
- intent B: `64658be5b12bc281b600899cae447732937e2281`;
- deployment merge: `8ad1576eb8605e6ac332498a5118d4efd2f72de4`;
- runtime: `rg5-1725638d57bdb08697f0721d4037834e5ecbe6b9c7fd133f5878d948b38dbce2`;
- boot: `2026-09-16T22:01:07.022141+00:00`.

The user asked to continue on September 17. The requested end date now includes
September 17, preserving the original instruction to synchronize through today.
Authenticated live UI confirms the selected primary USD account and the applied
range `2026-08-01` through `2026-09-17` (48 inclusive dates).

Manual attempts during automatic refresh did not create a job; the error toast
renders `[object Object]`. The deployed admission code rejects an active
`analytics_refresh` run. At 12:49 UTC, fresh activity readback showed the prior
automatic run complete and no Snapchat job running. The next manual submission
was accepted: at 12:50 UTC (15:50 Riyadh), Integrations activity showed a new
`مهمة مزامنة Snapchat الخلفية` as running, while the Snapchat page displayed
`جاري المزامنة`. Its run ID is not exposed by these UI elements. Do not invent
one or infer completion from the pre-existing Financial: complete badge.

Before this accepted job, freshly loaded Dashboard August 18–21 still showed
Snapchat as no data/waiting. Meta was 2,322.42 SAR and Google 465.71 SAR. The
accepted job reached terminal `partial` at 13:04 UTC (16:04 Riyadh), confirmed
again in Integrations activity at 13:13 UTC. No second historical job was
submitted. A subsequent live Dashboard read confirmed the historical gap is
repaired: Snapchat August 18–21 totals **24,558.51 SAR**, with original daily
data. The individual visible chart tooltips showed:

| Riyadh date | Snapchat spend (SAR) |
| --- | ---: |
| 2026-08-18 | 5,173.08 |
| 2026-08-19 | 5,854.99 |
| 2026-08-20 | 7,396.73 |
| 2026-08-21 | 6,133.71 |

At 13:13–13:14 UTC, the live Dashboard applied August 1–September 17 and
displayed Snapchat **398,777.06 SAR**, original daily data. Keyboard navigation
through the rendered chart tooltips verified **48 distinct consecutive dates,
zero missing Snapchat daily values**. September 17 showed 2,191.82 SAR at
readback and remains an open day. The rounded daily labels sum to 398,777.02
SAR, 0.04 below the displayed aggregate; retain the displayed aggregate and
do not claim the rounded labels sum to it exactly. The complete observed daily
labels are retained below. Meta/TikTok/Google displayed 53,554.07 / 7,133.25 /
2,901.74 SAR; no campaign settings were changed.

After reapplying the same dates in the Snapchat page, its headline changed
from 81,106.18 to **105,976.10 USD**. This page uses America/Los_Angeles account
time, unlike the Riyadh Dashboard; these totals must not be directly equated.
It showed `Financial: complete` and a 10.31 USD amount not yet distributed
across hours. This is evidence of recovered daily financial data, not a claim
that every detailed performance or hourly reconciliation stage is complete.
The pipeline source only emits overall `partial` after financial completion
when another level remains incomplete. The precise incomplete level and job
ID are not exposed in the inspected activity UI.

Remaining UI limitation: selecting the full 48-day Dashboard range attempts
an all-platform refresh and displays HTTP 422, then successfully reads saved
facts for the complete range. Source confirms refresh permits at most 31 days
(`dashboard_ads_platform_refresh.MAX_REFRESH_DAYS`), while saved reads permit
90. This warning does not negate the 48 observed daily values. No extra
historical sync, source patch, or deployment was attempted to hide the warning.
The requested spend backfill and missing-day restoration are verified; do not
repeat them merely because the broader job status is partial. A separate
follow-up can address long-range refresh UX and detailed performance status.

This docs-only continuation uses `ops/snap-spend-backfill-20260917` based on
production branch commit `836d36b831c1cd8aa9915f543f159b4e160ea438`; it must not
be deployed or treated as a new release contract. That commit belongs to the
separate PR #1058 deployment. Its active lease and shared `/app` must not be
changed by this task. The old cloud workspace was recreated; do not assume
September 16 `/tmp` evidence or terminals still exist. No terminal/database
mutation or new source change was used to start this accepted live job.

Current approval continues to cover source push, merge, deployment and this
backfill. No budget, bid, accounting or Qoyod write is authorized by this task.
The accepted analytical backfill was the only manually initiated historical
live data operation. Production analytical data changed and was read back;
this continuation made no source/runtime or database-terminal mutation.

### Verified daily chart labels — SAR, Riyadh

Observed through authenticated live Dashboard tooltips, not hidden application
state or a database query. Figures are as of September 17 at approximately
13:14 UTC; the current day continues to change.

| Date | Snapchat SAR | Date | Snapchat SAR |
| --- | ---: | --- | ---: |
| 2026-08-01 | 2,449.18 | 2026-08-25 | 5,588.57 |
| 2026-08-02 | 5,703.95 | 2026-08-26 | 8,160.94 |
| 2026-08-03 | 8,198.03 | 2026-08-27 | 9,848.17 |
| 2026-08-04 | 6,678.03 | 2026-08-28 | 14,887.96 |
| 2026-08-05 | 6,366.34 | 2026-08-29 | 13,987.73 |
| 2026-08-06 | 5,391.73 | 2026-08-30 | 11,537.01 |
| 2026-08-07 | 2,952.27 | 2026-08-31 | 12,799.61 |
| 2026-08-08 | 2,837.46 | 2026-09-01 | 16,599.10 |
| 2026-08-09 | 3,046.92 | 2026-09-02 | 16,368.43 |
| 2026-08-10 | 2,484.47 | 2026-09-03 | 16,139.19 |
| 2026-08-11 | 3,138.34 | 2026-09-04 | 18,661.66 |
| 2026-08-12 | 3,671.43 | 2026-09-05 | 16,268.19 |
| 2026-08-13 | 3,626.00 | 2026-09-06 | 13,590.52 |
| 2026-08-14 | 2,523.75 | 2026-09-07 | 16,468.75 |
| 2026-08-15 | 3,234.45 | 2026-09-08 | 9,704.56 |
| 2026-08-16 | 3,103.76 | 2026-09-09 | 10,661.41 |
| 2026-08-17 | 2,371.05 | 2026-09-10 | 8,393.19 |
| 2026-08-18 | 5,173.08 | 2026-09-11 | 10,327.79 |
| 2026-08-19 | 5,854.99 | 2026-09-12 | 11,940.79 |
| 2026-08-20 | 7,396.73 | 2026-09-13 | 10,225.07 |
| 2026-08-21 | 6,133.71 | 2026-09-14 | 11,909.44 |
| 2026-08-22 | 5,980.53 | 2026-09-15 | 8,898.49 |
| 2026-08-23 | 8,206.29 | 2026-09-16 | 5,518.40 |
| 2026-08-24 | 11,577.74 | 2026-09-17 | 2,191.82 |

## Historical checkpoints

Task: synchronize 2026-08-01 through 2026-09-16 (47 inclusive dates), then
verify Dashboard advertising spend, particularly August 18–21.

Continue from PR #1055, branch `fix/snapchat-dashboard-30d-canonical-20260916`.
Reviewed production base: `9f8b16998f62cbdb08dc9361bc2e443223d27d17`.
Prior source checkpoint: `9ecc1da2ec61e46fab1ea141cbfced5a9fae0da5`.

The original fix makes the Integrations async action write canonical Reporting
V2 facts instead of the legacy/native plane. The operational resume found that
the Snapchat V2 page still posts a synchronous request. Two UI attempts (the
full range and August 18–21) ended without verified completion or restored
Dashboard values. Their exact HTTP outcome was not retained; do not call them
successful or claim a specific transport error from this observation alone.

This follow-up routes the V2 page's manual conversion request through the
existing persisted async job, preserving its explicit dates and account.
Other action-report modes retain their API path. Conflicts may join only the
same date/account request. Ambiguous transport recovery cannot substitute a
recent scheduler run, and failed jobs throw an error instead of being shown as
partial financial success. No reporting calculation, scheduler, account
selection implementation, campaign/budget/bid, accounting or Qoyod change.

Verification before release: focused backend suite 11 passed and the seven
affected backend suites 61 passed; standalone Node
contract check first failed on the old synchronous mapping and passed after
the change (range/account, repeat rewrite, transport/failure boundaries).
Full CI and governed source/intent build remain required. No deployment of
this follow-up yet. Automatic approval review rejected the attempted GitHub
push: it interpreted this turn as authorizing synchronization, not source
publication. Do not retry or use a connector workaround without resolving
that approval. Remote branch remains at the prior source checkpoint above.
Local-only commit 5f52d9f7247711e02982cfb636a2101fcb2c1427 plus the final
uncommitted boundary refinements are preserved in the exported patch.
No credentials/customer data are recorded here.

The user subsequently explicitly approved pushing this fix to AMASI-SA/AMASI-SA,
completing PR #1055, merging after checks, deploying, and performing the requested
backfill. The prior source-publication approval block is resolved. Fresh remote
checks still show the same production base and task checkpoint; concurrent
PR #1056 release preparation is separate and must be preserved.

Next: push the verified source checkpoint under that explicit authorization;
inspect exact-head CI; create the intent-only B using the governed build
for the final source A, rehearse and follow AGENTS.md release gates under the
existing task authorization. After publication, initiate one exact-range
async job, retain its run ID and terminal outcome, and verify Dashboard daily
coverage and August 18–21 values before declaring the task complete.

Canonical continuation ledger: GitHub Issue #1006. Preserve concurrent
Snapchat scroll/sort work and Preview/MFA, Salla and Qoyod work.

## Current-base release continuation

All 13 workflows passed for source 83ad9f2d24064d265e367709bac449705888c5fc,
including 61 backend tests, 23 frontend tests, and governed build readiness.
The local governed two-build freeze produced intent-only
bc164f6fb1c25abbcfe8635751e953246cf3df70. Before this contract was merged or
deployed, PR #1056 advanced Production to
757ea90c65236943a9ad5a2593c4eef5d8bd8eb9 and began its own deployment rehearsal.
That supersedes the old spend-sync release contract; it must not be published.

The current source reapplies only the verified spend-sync source delta atop
757ea90c65236943a9ad5a2593c4eef5d8bd8eb9 and preserves the nine-row scroll/sort
changes. The old source/intent history remains in a recovery branch before the
task branch is updated. A new governed freeze and intent-only commit are
required, followed by exact-head checks and a fresh clean-clone rehearsal.
Do not touch shared /app while the #1056 deployment is active. Re-read Issue
#1006, the current Production head and Release Guard before any deployment.
The requested Aug1–Sep16 backfill and daily coverage verification are pending.
