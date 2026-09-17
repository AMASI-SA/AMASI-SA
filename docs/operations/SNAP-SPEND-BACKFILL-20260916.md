# Snapchat canonical spend backfill — 2026-09-16

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
historical gap is NOT verified repaired yet. No new job should be submitted
while the accepted job remains active. Next: read its terminal outcome, then
refresh the Dashboard and verify the four missing daily values and full range.

This docs-only continuation uses `ops/snap-spend-backfill-20260917` based on
production branch commit `836d36b831c1cd8aa9915f543f159b4e160ea438`; it must not
be deployed or treated as a new release contract. That commit belongs to the
separate PR #1058 deployment. Its active lease and shared `/app` must not be
changed by this task. The old cloud workspace was recreated; do not assume
September 16 `/tmp` evidence or terminals still exist. No terminal/database
mutation or new source change was used to start this accepted live job.

Current approval continues to cover source push, merge, deployment and this
backfill. No budget, bid, accounting or Qoyod write is authorized by this task.
The accepted analytical backfill is the only current live data operation.

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
