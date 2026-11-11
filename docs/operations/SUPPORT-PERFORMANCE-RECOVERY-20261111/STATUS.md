# Support performance recovery — source candidate

Repository: AMASI-SA/AMASI-SA. Task branch: `codex/support-performance-recovery-20261111`.
Reviewed production base: `f439bed54579fafb79a8abcb43080c8150cb5063`.
Draft PR: https://github.com/AMASI-SA/AMASI-SA/pull/1878.
Scope-preserving prerequisite PR: https://github.com/AMASI-SA/AMASI-SA/pull/1880.
Prerequisite source: `7028093e03999191860b91757c8c6246d93a6cd4`, branch `codex/campaign-snapshot-visibility-20261111`, tree `63bb41401bf39dfdd299960dc1be880067f3d56b`.
Previous remote checkpoint: `8e775226f031074e77f28ea2b3dad94205c8fd90`.
The PR records the exact latest candidate SHA, tree, CI runs and continuation state. Read its current head before resuming; do not deploy the older checkpoint.
Production changed: **no**. No live invoice send, advertising change, scan, lease, merge or publication was performed by this task.

## Owner decisions

- Fix the remaining performance issues described by Emergent Support on 11 November.
- Campaign monitoring is **manual on request**. Page reads return stored results; a separate explicit action creates analysis/recommendations without executing advertising changes.
- Snapchat automatic data refresh defaults to **two hours**, never runs more frequently, and preserves an explicitly longer interval across surviving environment values and restarts.
- **Preparation-file creation is excluded by the owner's later instruction.** All initially drafted preparation changes were removed and the eleven existing preparation files were verified byte-for-byte against the production base. No preparation job module or related new tests remain.

## Implementation

- Qoyod Plan-B records each order disposition before continuing, finalizes interrupted/error audit rows, holds uncertain attempts for review, and applies a fifteen-minute cooldown to repeated/no-progress work. The normal two-hour cycle and invoice/payment/reference gates remain.
- Bounded metadata-only recovery closes abandoned audit records under the existing worker lease and preserves newer successful paid/COD evidence. Prior-worker audits are recovered immediately and before the legacy backlog; ownerless legacy rows keep a fifteen-minute age threshold. The current worker's audit is never recovered. Two nonunique partial startup indexes support bounded twenty-row pages. Manual job rows also fence automatic retries while outcome is pending/unknown.
- Qoyod full scans require an explicit manual request. Saved display generations remain; the automatic full-scan registration is removed and the UI explains manual freshness.
- Manual Qoyod send/retry endpoints acknowledge durable jobs (202), coalesce repeated requests, and provide owner-scoped status. Unknown provider outcomes are never classified as safe retryable refusals. Context-local checks verify the job lease and current owner before each provider write. Cancellation retains invoice locks; metadata hold failures are repaired without resending. A new explicit request may retry a definitely pre-write refusal after its cause is corrected, including ordinary send rows without separate retry metadata; successful and uncertain attempts cannot be rearmed this way.
- Campaign comparison and real recommendation jobs use one owner slot and one global subprocess runner, bounded execution and status polling. Existing periodic Campaign AI scheduling is disabled even if an older enabling environment value survives. Data-sync and unrelated separately configured advertising services are not reclassified as manual monitoring.
- Campaign core analysis stays hidden as `building` until enrichment finishes. The final transaction publishes canonical visibility and the cached result together, checking current owner authority and both leases. Latest/history, direct-ID approval and execution preflight exclude building snapshots. Recovery copies saved core analysis to a new snapshot, so a stale child can never mutate the published replacement; partial recovery is labeled in the UI.
- Snapchat's existing account lease now also protects a persisted cadence check. Successful, partial and failed completed attempts prevent another automatic cycle for two hours; a recent manual refresh satisfies automatic freshness. Existing abandonment recovery remains.

## Explicit work limits

| Work | Admission / bound |
| --- | --- |
| Campaign AI monitoring | Explicit owner request, three-day core performance window, maximum eight minutes |
| Diagnostic source comparison | Explicit owner request, one-to-fourteen-day window, maximum five minutes |
| Campaign concurrency | One owner slot across both job kinds/windows and one global runner across replicas |
| Campaign browser polling | Every five seconds while active, maximum five minutes, then explicit status resume; no automatic resubmission |
| Automatic full Qoyod scan | Disabled; existing manual scan action retained |
| Snapchat automatic refresh | Minimum/default two hours, with persisted account freshness |

## Verification

- Focused manual-send review: **32 passed**; the corrected-prewrite-refusal HTTP regression failed before its fix. Provider uncertainty remains held and cannot be rearmed.
- Focused automatic-run/cycle/index review: **98 passed, no skips** on isolated Mongo. A takeover regression initially repeated the first provider call and now advances without repeating it. Index explain checks used 1,002 synthetic audits.
- Focused campaign publication/approval review: **80 passed**, including nine real-Mongo cases. An independent reviewer also reran the loss-after-core case successfully: replacement publishes first, stale child finishes later, visible advice/cache remain unchanged and hidden-source approval is rejected.
- Snapchat cadence: **18 passed**, including persisted cooldown and preservation of an explicit 48-hour interval.
- Final root frontend integration: **76 passed in five suites**, including polling, one-flow admission, unknown outcome key retention, navigation cancellation and dashboard regressions.
- Final root integrated backend matrix: **293 passed, zero failures/errors/skips across 23 suites**, exit 0, 66.67 seconds. This includes the real-Mongo jobs, audit recovery/indexes, payment barriers, current permission checks, canonical child boot and authentication startup. The command selects the same 23 test files as the updated background-worker CI gate. Test source-file paths were made independent of the caller's working directory where necessary.
- The local canonical worker boot passed after installing the repository-pinned OpenAI and WebAuthn packages into the task's partial test environment. It reports the new manual/advertising workers, no automatic full-scan worker, no financial work in an empty test database, and clean shutdown.
- Initial checkpoint CI had 35 successful workflows and one obsolete full-scan-worker assertion. That assertion now verifies the new required worker set. The final exact-head CI is the acceptance authority; its links and results are maintained in PR #1878.
- CI on intermediate source `17a393d28b5ab686dc91e9bf71780dbcce167ac8` exposed one old exact-query test double in unified execution evidence. The fixture now interprets the visibility predicate, rejects a building snapshot and accepts its completed version while retaining owner/digest/source-revision checks. The evidence/quality suites passed 105 cases and real-Mongo campaign jobs passed nine cases after this correction.
- The standalone prerequisite was independently tested on the unchanged production base with only its two files: **152 passed, no skips**. Its existing Snapchat scope classification is `run_tests=true`, `rejected=0`; no allowlist or gate was changed.
- Local real-Mongo tests use MongoDB **7.0.17** because the executor cannot start 8.0.12 with its CPU topology interface; CI uses **8.0.12**. All data and provider effects in these tests are synthetic.
- Changed Python files parse; `git diff --check` is clean. All eleven excluded preparation files were rechecked byte-for-byte against the production base. Governed Node v22.23.2 is available.

Focused suite counts overlap and must not be added together.

## Release and continuation state

Implementation and independent reviews are complete. Inspect the final exact-head PR checks and generated source-candidate intent artifacts before release work. Source/test changes remain on dedicated task branches; no intent-only deployment commit has been frozen yet.

The existing Snapchat settings workflow intentionally rejects mixed PR scopes when `campaign_ai_execution_quality_gate.py` changes. The broad PR is therefore not merge-ready until prerequisite #1880 is reviewed and integrated. That prerequisite changes only the two already-allowlisted quality-gate/evidence-test files, uses a self-contained visibility query, and works independently on production. The main source includes the same blobs and prerequisite ancestry. After the reviewed prerequisite is integrated into production, merge the updated production ancestry into #1878, verify its triple-dot diff no longer contains that scope trigger, and run final exact-head gates. Preserve the existing guard unchanged; do not retarget the PR or skip checks to make it appear green. Both PRs can be assembled for one eventual production publication.

Publication is a separate step under `AGENTS.md`. The Emergent browser showed its sign-in page. Automatic approval review rejected marking that page for a deployment handoff, stating that the current authorization covers source/test/review work and does not authorize production access or deployment handoff. No further authentication or production action was attempted. Obtain explicit authorization for that next phase before retrying it.

After authorization: authenticate through the supported secure flow, inspect shared `/app` Release Guard status and require `active: false`, refresh the production base, integrate the prerequisite in the reviewed v5 sequence above, and finish the combined governed source A / intent-only B and clean-clone rehearsal. A changed source/base requires a new build and intent. Publishing and final three-probe production verification remain mandatory; this task makes no claim that the existing 1,087 production audit rows have already been repaired or that production latency has improved.

Issue #1006 is already comment-limited and its read connector failed in this session. The remotely recoverable task branch, this document, and PR #1878 provide the continuation record. The next safe action without new authorization is read-only inspection of the exact PR head, CI and candidate artifact.

The repository's other pending financial, preparation and reporting work is outside this change. No old rollback feature or unrelated draft PR was restored.
