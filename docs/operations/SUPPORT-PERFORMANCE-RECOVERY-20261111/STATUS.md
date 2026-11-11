# Support performance recovery — implementation checkpoint

Repository: AMASI-SA/AMASI-SA. Task branch: `codex/support-performance-recovery-20261111`.
Reviewed production base: `f439bed54579fafb79a8abcb43080c8150cb5063`.
Production changed: **no**. No live invoice send, advertising change, scan, lease, merge or publication was performed by this task.

## Owner decisions

- Fix the remaining performance issues described by Emergent Support on 11 November.
- Campaign monitoring is **manual on request**. Page reads return stored results; a separate explicit action creates analysis/recommendations without executing advertising changes.
- Snapchat automatic data refresh remains every **two hours**, honoring the prior owner decision across surviving environment values and restarts.
- **Preparation-file creation is excluded by the owner's later instruction.** All initially drafted preparation changes were removed and the eleven existing preparation files were verified byte-for-byte against the production base. No preparation job module or related new tests remain.

## Implementation

- Qoyod Plan-B records each order disposition before continuing, finalizes interrupted/error audit rows, holds uncertain attempts for review, and applies a fifteen-minute cooldown to repeated/no-progress work. The normal two-hour cycle and invoice/payment/reference gates remain.
- Bounded metadata-only recovery closes abandoned audit records under the existing worker lease and preserves newer successful paid/COD evidence. Manual job rows also fence automatic retries while outcome is pending/unknown.
- Qoyod full scans require an explicit manual request. Saved display generations remain; the automatic full-scan registration is removed and the UI explains manual freshness.
- Manual Qoyod send/retry endpoints acknowledge durable jobs (202), coalesce repeated requests, and provide owner-scoped status. Unknown provider outcomes are never classified as safe retryable refusals. Context-local checks verify the job lease and current owner before each provider write. Cancellation retains invoice locks; metadata hold failures are repaired without resending.
- Campaign comparison and real recommendation jobs use one owner slot and one global subprocess runner, bounded execution and status polling. Existing periodic Campaign AI scheduling is disabled even if an older enabling environment value survives. Data-sync and unrelated separately configured advertising services are not reclassified as manual monitoring.
- Snapchat's existing account lease now also protects a persisted cadence check. Successful, partial and failed completed attempts prevent another automatic cycle for two hours; a recent manual refresh satisfies automatic freshness. Existing abandonment recovery remains.

## Verification so far (working source, not release acceptance)

- Manual-send HTTP regression originally timed out before acknowledgement; new handler returns a queued job without reaching provider work.
- Root manual-send/post-invoice/retry suite: **25 passed**. A missing local SOCKS dependency was installed; the initial environment failures were rerun successfully.
- Root frontend integration: **76 passed in five suites**, including dashboard regressions, job status polling, one-flow admission, unknown outcome key retention and navigation cancellation.
- Root isolated real-Mongo Qoyod cycle/manifest/send-lock/job integration: **31 passed, no skips**, using MongoDB 7.0.17. CI is configured to run the exact source on MongoDB 8.0.12; local 8.0 cannot start in this executor because of its CPU topology interface.
- Focused automatic-run and advertising suites have been run during implementation; exact integrated results will replace provisional evidence before release readiness is claimed.
- `git diff --check` passed. Governed Node v22.23.2 is available.

## Open work at this checkpoint

1. Finish and independently verify the Campaign AI publication fence across core analysis, enrichment, cached result, latest/history and approval visibility. No stale worker may expose an incomplete or superseded snapshot.
2. Finish the new-audit immediate-takeover recovery edge; legacy audits keep conservative age-based cleanup.
3. Run the integrated backend/real-Mongo/frontend gates and inspect the combined diff on a fixed source commit.
4. Push this task branch and keep a Draft PR with exact source/test evidence. Issue #1006 is already comment-limited; its read connector also failed this session, so the task status and PR provide continuity.
5. Prepare the v5 reviewed source/build and intent-only handoff after validation. No production deployment is authorized by this implementation checkpoint. A concrete ready candidate and required guard checks must precede any publication decision.

The repository's other pending financial, preparation and reporting work is outside this change. No old rollback feature or unrelated draft PR was restored.
