# Support performance recovery — authorized release integration

Repository: AMASI-SA/AMASI-SA. Task branch: `codex/support-performance-release-20261111`. Original reviewed implementation remains in #1878 on `codex/support-performance-recovery-20261111`.
Current reviewed production Git base: `3f60c1a08263ff1eb2412c0f9224467a198ac278` (merged prerequisite #1880). The original source acceptance below used prior base `f439bed54579fafb79a8abcb43080c8150cb5063`.
Original implementation PR: https://github.com/AMASI-SA/AMASI-SA/pull/1878. The final release PR is linked there and records the exact new source.
Previous verified implementation source: `b4da6e337e4794a3f6aa5094502c84d38bf18976`, tree `c69ab1b4c41b5bff62f5ca77059190020b1fdd1e`. The current exact source is recorded in the final release PR linked from #1878.
This record is now carried into the implementation source. Earlier evidence-only handoffs remain on `codex/support-performance-handoff-20261111`; continue from the exact final release head linked from PR #1878.
Scope-preserving prerequisite PR: https://github.com/AMASI-SA/AMASI-SA/pull/1880.
Prerequisite source: `7028093e03999191860b91757c8c6246d93a6cd4`, branch `codex/campaign-snapshot-visibility-20261111`, tree `63bb41401bf39dfdd299960dc1be880067f3d56b`.
Previous remote checkpoint: `8e775226f031074e77f28ea2b3dad94205c8fd90`.
The PR records the exact latest candidate SHA, tree, CI runs and continuation state. Read its current head before resuming; do not deploy the older checkpoint.
Live application/data changed: **no**. Production Git now includes prerequisite #1880; no live invoice send, advertising change, scan, release lease or publication was performed by this task.

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

## Earlier release boundary — superseded by the continuation entries below

Implementation and independent reviews are complete. Inspect the final exact-head PR checks and generated source-candidate intent artifacts before release work. Source/test changes remain on dedicated task branches; no intent-only deployment commit has been frozen yet.

The existing Snapchat settings workflow intentionally rejects mixed PR scopes when `campaign_ai_execution_quality_gate.py` changes. The broad PR is therefore not merge-ready until prerequisite #1880 is reviewed and integrated. That prerequisite changes only the two already-allowlisted quality-gate/evidence-test files, uses a self-contained visibility query, and works independently on production. The main source includes the same blobs and prerequisite ancestry. After the reviewed prerequisite is integrated into production, merge the updated production ancestry into #1878, verify its triple-dot diff no longer contains that scope trigger, and run final exact-head gates. Preserve the existing guard unchanged; do not retarget the PR or skip checks to make it appear green. Both PRs can be assembled for one eventual production publication.

Publication is a separate step under `AGENTS.md`. The Emergent browser showed its sign-in page. Automatic approval review rejected marking that page for a deployment handoff, stating that the current authorization covers source/test/review work and does not authorize production access or deployment handoff. No further authentication or production action was attempted. Obtain explicit authorization for that next phase before retrying it.

After authorization: authenticate through the supported secure flow, inspect shared `/app` Release Guard status and require `active: false`, refresh the production base, integrate the prerequisite in the reviewed v5 sequence above, and finish the combined governed source A / intent-only B and clean-clone rehearsal. A changed source/base requires a new build and intent. Publishing and final three-probe production verification remain mandatory; this task makes no claim that the existing 1,087 production audit rows have already been repaired or that production latency has improved.

Issue #1006 is already comment-limited and its read connector failed in this session. The remotely recoverable task branch, this document, and PR #1878 provide the continuation record. The next safe action without new authorization is read-only inspection of the exact PR head, CI and candidate artifact.

The repository's other pending financial, preparation and reporting work is outside this change. No old rollback feature or unrelated draft PR was restored.

## Previous exact-source acceptance — 2026-11-11 14:42 UTC

All observed runs have completed.

| Candidate | Exact-source CI result | Remaining gate |
| --- | --- | --- |
| Main #1878, b4da6e337e4794a3f6aa5094502c84d38bf18976 | 36 successful workflows out of 37 | The one failing Snapchat settings scope guard intentionally blocks the mixed diff until prerequisite integration |
| Prerequisite #1880, 7028093e03999191860b91757c8c6246d93a6cd4 | All 10 workflows successful | Human review/integration and the separately authorized release procedure |

There are no pending CI runs and no remaining test/code failure reported by these exact-source runs. The broad PR remains **not merge-ready** because its scope gate is still enforced. The successful prerequisite does not by itself make that broad check green.

Main background-worker acceptance run 43039360749 used MongoDB 8.0.12 and passed:
- 293 backend tests, zero skipped acceptance cases, 66.03 seconds;
- 48 Salla synchronization/preservation tests, 8.80 seconds;
- 76 frontend tests across five suites.
Its job IDs are 130586692786 (backend) and 130586693360 (frontend).
The corrected Snapchat autonomy and reporting workflows also succeeded.
Main source-candidate release-readiness run 43039361196 succeeded.

Prerequisite scope guard, backend settings/no-write tests, and frontend tests/build all succeeded in run 43039154097. Its source-candidate release-readiness run 43039154235 succeeded. Independent isolated production-base verification had already passed 152 tests for only the two prerequisite files.

Both candidate archives were downloaded as read-only evidence, their ZIP digests were verified, and their schema-2 intents were checked against the exact source SHA and production base. Each contains matching clean-build passes using Node 22.23.2, Yarn 1.22.22 and Vite 8.2.4. No adapter rehearsal in shared /app, intent-only deployment commit, lease, merge or publication was performed.

| Candidate | CI artifact ID | Intent JSON SHA-256 |
| --- | --- | --- |
| Main |13865401114|af2d7f4fa0fc3ca23afe9b312d9aad628fab49d95662d682a633f338ec3489cb|
| Prerequisite |13863668442|c6db9e0191e4c2d8047a974b5845b33e4d0d9c6bf1bec4cfc494428881fd7751|

The CI candidate archives expire on 13 November 2026. They are source-candidate evidence, not final deployment artifacts. Rebuild if an artifact is unavailable or the source/base changes; do not silently reuse an old intent.

### Exact continuation boundary

1. Obtain explicit authorization for the production-access/deployment phase rejected by automatic review. Source/test/review work is complete and remotely recoverable; the sign-in handoff must not be retried without that authorization.
2. After authorization, use the supported secure authentication flow and require shared /app Release Guard active=false before release rehearsal or shared changes.
3. Complete the reviewed v5 prerequisite A/B sequence for #1880 and integrate it without publishing the application at that intermediate step.
4. Merge the updated production ancestry into the main task branch, preserve the identical prerequisite blobs, confirm the quality-gate path is absent from the main triple-dot diff, and require every final exact-head gate to pass.
5. Build/freeze the new combined source A and intent-only B, run the required rehearsal, prepare/prepublish, publish once, and require Deployment Succeeded plus the three successful guard probes and active=false.

Production changed by this task: **no**. Preparation-file creation remains excluded. The 1,087 legacy production audit rows have not been directly changed; bounded recovery takes effect only after the authorized deployment. No invoice, payment, budget, bid, campaign status, target cost, scan or provider synchronization was executed against production.

No unrelated worktree or branch was reset. The main worktree has no tracked pending changes; .test-runtime contains disposable local verification tools/evidence and must not be staged.

## Authorized release resumed — 2026-11-11

The owner explicitly requested continuation, merge and publication, and stated the Emergent session is signed in. Refresh confirmed the authenticated Salla Analytics project. The previous production-authorization blocker is resolved. The preparation exclusion remains binding.

Prerequisite source A remains 7028093e03999191860b91757c8c6246d93a6cd4. Its verified candidate intent is now frozen in intent-only B 02e9c5f25953e87e04ba969b9df44028eecc88c0, tree 0634c14c47af35dd81bbb9292ae62db3f6e785d6. Remote blob 2760c273b39e1620d93382492d0f088a76c4ab2e exactly matches the 1,260,920-byte verified artifact; only release/release-intent-v5.json changes after A. Exact B checks and clean-clone rehearsals are pending.

Production branch still f439bed54579fafb79a8abcb43080c8150cb5063; main task source still b4da6e337e4794a3f6aa5094502c84d38bf18976. No merge, shared /app change, lease or publication has occurred. Next: complete secure terminal access and require guard active=false, inspect successful B checks, mark prerequisite ready and merge with expected-head protection. Then merge updated production ancestry into main, preserving inherited intent, freeze a new combined A/B, require final gates and rehearsal, and publish once under v5.

## Prerequisite integrated; combined source A in preparation

Prerequisite #1880 was marked ready and merged by regular merge, preserving source A1 7028093e03999191860b91757c8c6246d93a6cd4 and intent-only B1 02e9c5f25953e87e04ba969b9df44028eecc88c0. New production Git base J1 is 3f60c1a08263ff1eb2412c0f9224467a198ac278; its tree is identical to B1.

Exact B1 checks completed: ten successful workflows and one conditional TikTok workflow skipped. Release-readiness run 43043656133 passed the actual clean-clone adapter rehearsals on Host Node 20.20.2 and 24.19.0, including verified artifact/proof/identity/package boundaries and JSON metadata serving. The unchanged Snapchat scope and suite passed in run 43043655858. The merge used expected-head protection and no squash/rebase.

The main implementation now incorporates J1 ancestry using a normal merge, carrying J1's intent unchanged. No feature/test code is changed by this integration. This status update is included before the new source A is frozen. The exact new source/tree and candidate build will be recorded in PR #1878. Require the quality-gate trigger to be absent from the J1 triple-dot diff, all preparation files unchanged, and zero intent changes in J1..A full-history.

The Emergent session is authenticated. VS Code still shows a separate sign-in page after the secure credential form was canceled; no credential value was read or entered. A prior attempt to print matching terminal-link/password labels was rejected by automatic review; subsequent inspection used only button labels and the supported secure authentication form. No alternate credential path was used. Do not retry authentication until the user requests it or completes the supported sign-in. Continue the authorized GitHub integration/build work.

Production Git branch changed: yes, to J1 above. Live application/data changed by this task: no. Shared /app was not changed, no release lease was created, and no publication was performed. Next: publish the combined source A on the task branch, require final source checks, freeze its exact reviewed intent-only B, require B clean-clone rehearsals, and merge normally. When terminal access is available, require active=false, fast-forward /app, rehearse, prepare/prepublish, publish once, then verify three probes and active=false. No new publication permission is needed; the owner's explicit authorization remains valid.

## Final source rebuilt directly on the reviewed prerequisite base

The proposed normal merge ab5dd65afdd7b7b76cd8f2eb8b7eea63d227c223 preserved the exact intended files, but root's actual v5 preflight found that `git rev-list --full-history 3f60c1a08263ff1eb2412c0f9224467a198ac278..ab5dd65afdd7b7b76cd8f2eb8b7eea63d227c223 -- release/release-intent-v5.json` returns that merge commit. A byte-identical intent diff was not sufficient. This candidate must not be released.

The original branch and PR are preserved. The final source branch codex/support-performance-release-20261111 starts directly from reviewed J1=3f60c1a08263ff1eb2412c0f9224467a198ac278. Root applied the exact reviewed implementation diff with Git, verified the resulting tree was identical to ab5dd65a, and then updated only this status document. No guard, allowlist or workflow was weakened; no prior A/B pair was squashed, rebased or force-pushed. The final source has J1 as its sole parent, while the original reviewed commits remain recoverable on their original branch.

Feature and test files are identical to the prior fully tested implementation. The inherited intent is byte-identical to J1; the final source's J1..A intent history must be empty. The execution-quality trigger is already present in the reviewed base and is absent from the new PR diff. All eleven preparation exclusions remain byte-identical to J1.

Next: require all new exact-source CI checks and a fresh candidate intent, freeze a new intent-only B, require reviewed-B clean-clone rehearsals, and merge the final release PR normally. Terminal login remains the only known access prerequisite for shared /app rehearsal and actual publication. User merge/publication authorization remains valid; do not request it again. Live deployment has not occurred.
