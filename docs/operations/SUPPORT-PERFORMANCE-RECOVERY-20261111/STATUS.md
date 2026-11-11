# Final performance release — merged; live publication pending

**Current authoritative checkpoint: 2026-11-11.** This is an evidence-only branch. Deploy from reviewed production `hotfix/prod-snap-meta-final`, not this documentation branch.

## Completed release integration

- Final PR [#1881](https://github.com/AMASI-SA/AMASI-SA/pull/1881) is merged by normal merge with expected-head protection.
- Production merge commit: `77e687e6964254617b4aa9eb0e3164cc968cff3b`.
- Reviewed source A: `8f11a73a8e53262ed938505f39a141eca3dfab8f`.
- Reviewed intent-only B: `173cc5ca7ceae8e50aae3f4bde13e972a2a053bd`.
- Reviewed base J: `3f60c1a08263ff1eb2412c0f9224467a198ac278`.
- Merge/B tree: `31763cd6cf814f0809376a707c7af2de2ebf23f0`.
- Runtime release ID: `rg5-5dca51b366b459cf1d62df9686fb97ce6385f29b652df7545cfe90270aecb8f8`.

Exact-B CI completed with **37 successful workflows, one expected conditional TikTok Creator Release Candidate skip, and no failed or pending workflows**. Release-readiness [43050051800](https://github.com/AMASI-SA/AMASI-SA/actions/runs/43050051800) passed actual Node20/24 clean-clone adapter rehearsals. Background-worker [43050052320](https://github.com/AMASI-SA/AMASI-SA/actions/runs/43050052320) passed 293 backend tests, 48 Salla synchronization tests and 76 frontend tests. Final Review Completion Resumable [43050051345](https://github.com/AMASI-SA/AMASI-SA/actions/runs/43050051345) also passed. Cancelled/superseded source-A runs are not counted as successes.

Root freshly fetched the production branch and verified the exact merge SHA/tree, parents J and B, A/B ancestry, and that A-to-deployment changes only the intent file. The unchanged v5 `load_release_intent` and `verify_release_intent_git` accepted the merged production commit in the clean validation worktree. The source-only import warning about absent generated release_identity.json is expected; validation exited zero. An independent reviewer confirmed the same tree/ancestry and byte-identical blobs for all eleven excluded preparation files. No source or intent was changed after B.

## Remaining access and publication boundary

The owner explicitly authorized both merge and publication: “كمل وادحج وانشر حدث صفحة المنصه لانه انا مسجلها دخول”. This authorization remains valid; do not request it again.

The requested Emergent refresh succeeded and the Salla Analytics project was authenticated. Opening its VS Code terminal led to a separate sign-in page. Automatic approval review rejected an earlier attempt to inspect matching terminal/password/link labels because it could expose authentication material. Root used the safer supported browserAuth secure form instead; that request was cancelled by the user. The terminal was then observed still at login. No secret was read or entered, no clipboard/session-token extraction occurred, and no alternative credential route was attempted. Do not retry authentication until the user requests it or completes the supported sign-in.

**Live publication is NOT complete.** No shared `/app` change, release lease, actual publication, invoice send, provider synchronization or advertising mutation was performed by this task. Production latency and the 1,087 historic audit rows have not been remeasured or directly repaired. Merged code provides the recovery logic; it does not prove live recovery already ran.

Once supported terminal access is available:

1. Read current shared instructions and run `python scripts/production_release_guard.py status` in `/app`; require `active:false` before any shared changes or rehearsal. Never clear another task's lease.
2. Freshly fetch the production branch and require the reviewed deployment tree/intent above. If the branch has advanced, assess that change; do not deploy this documentation branch or reset unrelated work.
3. Fast-forward `/app` to the reviewed deployment commit while preserving unrelated files. Run the v5 package adapter rehearsal through `cd frontend` then `yarn build` with the reviewed public origin `https://mezansalla.com` and governed toolchain.
4. Only after the rehearsal succeeds, run Release Guard `prepare` for this task, then `prepublish` immediately before one UI publication through Manage Publishing → Re-publish changes. Wait for a new Deployment Succeeded result.
5. Run guard `verify --url https://mezansalla.com` and `status`. Require three consecutive identity/build-metadata probes, exact A/runtime release ID above, verified identity and critical hashes, frontend verification, exact canonical/cache-busted JSON metadata, and final `verified:true` / `active:false`.
6. Perform only the read-only acceptance checks listed below. Do not generate live test invoices, scans, analyses, syncs or ad changes.

No paid Emergent chat was used. The publication authorization is complete; the remaining access dependency is the separate terminal login.

---

## Prior checkpoints — historical context

The entries below preserve implementation evidence and earlier intermediate states. The current checkpoint above supersedes earlier “pending”, “unmerged” or authorization-blocker statements.

# Final performance release — evidence and continuation

**Evidence-only branch. Resume deployment from `codex/support-performance-release-20261111` and PR #1881, not this documentation branch.**

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

## Frozen final A/B — current continuation

Final PR: https://github.com/AMASI-SA/AMASI-SA/pull/1881.
Source A: 8f11a73a8e53262ed938505f39a141eca3dfab8f; source tree: 1ce136cfe6c4bf6f12155b95d547b76767ba52b7.
Intent-only B: 173cc5ca7ceae8e50aae3f4bde13e972a2a053bd; deployment tree: 31763cd6cf814f0809376a707c7af2de2ebf23f0.
Reviewed base J: 3f60c1a08263ff1eb2412c0f9224467a198ac278.
Runtime ID: rg5-5dca51b366b459cf1d62df9686fb97ce6385f29b652df7545cfe90270aecb8f8.

Source-candidate build run 43049065057 passed. Artifact 13867343869 archive SHA-256: 801bfc6a6ea82f124f676e1b8b1e1a57ed393c2c20e342f7f952d5b8a4c6ec80. Intent SHA-256: 3b580317093129ae534f26f35b4f213ec5c5d0c934188fc0da07ae93d85ccbbe, Git blob f263787a2da9b33b209e221ae0c45d58506e9b81, 1,263,919 bytes. Artifact tree: 8bc4ff27a8d4cedc85d5613789176c0eda9100ad5d7b7538aba33756999d8fdf. Build-meta SHA-256: 60d36db7392328f82523379e2ee1b964f15e6c5858258189e9828bbdc51b62ef. Toolchain Node22.23.2/Yarn1.22.22/Vite8.2.4.

The original mixed checkout contains test bytecode and is not a clean release validation root. Root used a clean detached worktree under /workspace/scratch/e2ab5db77417/release-validation/source-a with PYTHONDONTWRITEBYTECODE=1. The unchanged candidate loader, derived Backend/control Git manifests, matching clean-build passes, load_release_intent and verify_release_intent_git all passed there. Missing generated release_identity.json at import time is expected for source-only validation; no ignored release output was substituted.

Final-B CI is pending. Its reviewed release-readiness run is 43050051800. A had 33 successful workflows before B superseded three residual runs under the unchanged concurrency rules; one A run was still running. The final B checks, including actual clean-clone Node20/24 rehearsals, are the merge acceptance authority. No cancelled run is counted as successful.

PR #1878 is closed as superseded and remains review evidence. #1880 is merged. #1881 is still draft/unmerged at this checkpoint. No shared /app changes, lease or publication occurred. Live application/data remain unchanged. The explicit owner merge/publication authorization remains valid. The VS Code secure credential request was cancelled and the terminal remains at sign-in; respect that state and do not extract or replay credentials through another route.

After final B checks pass: mark #1881 ready, refresh the production base, merge normally with expected B protection, record the merge SHA, and require the merged production tree to equal B. Once supported terminal access is completed, run /app guard status and require active=false before any shared changes or rehearsal; fast-forward to that reviewed production commit, run the package adapter, prepare/prepublish, publish once, wait for newer Deployment Succeeded, and run verify plus status. No additional authorization for merge/publication is required.

## Read-only production acceptance after successful guard verification

These source-derived checks are prepared, not yet run against production. Do not create test invoices, scans, analyses, provider synchronizations or ad changes.

- Use the required guard probes for /health, /ready and canonical/cache-busted /build-meta.json and the exact A/runtime ID above.
- Authenticated GET /api/ads-manager/ai-monitor/manual and /api/ads-manager/ai-monitor/unified-shadow?days=1 must return stored job/results, kind monitor/comparison, and provider_writes_performed=false without changing request identity. Latest/history must hide building snapshots. Full /dashboard-advanced also reads a separate GA4 widget, so direct GETs are the provider-free acceptance surface.
- GET /api/integrations/qoyod/worker/status and /api/integrations/qoyod/auto-send/cycle: observe background execution, qoyod-manual-send-jobs, campaign-ai-shadow-jobs and automatic Plan-B worker; no qoyod-full-scan. Cadence 7200 seconds and no-progress cooldown 900 seconds.
- GET /api/integrations/qoyod/manual/health; only observe a manual/jobs/<existing id> if already known. Do not enqueue a live test job.
- GET /api/integrations/qoyod/reconciliation-report: scan.mode manual_only and scan.schedule_riyadh empty. Saved stale data is permitted until an explicit manual scan.
- Snapchat's configured interval helper must return at least7200 and periodic Campaign AI scheduler_enabled must return false. Prefer existing read-only account status and narrowly projected persisted terminal-run timestamps for cadence evidence. There is no HTTP shadow_scheduler_status route; do not invent one. Immediate verification cannot prove two hours of future runtime behavior.
