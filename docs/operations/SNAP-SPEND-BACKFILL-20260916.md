# Snapchat canonical spend backfill — 2026-09-16

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
