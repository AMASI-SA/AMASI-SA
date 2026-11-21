# External-party completion — PR #2039

## Checkpoint verdict

**PREPARED_PUBLICATION_PENDING. PR #2039 is normally merged, its official Emergent rehearsal and shared `/app` materialization have passed, and its own protocol-v5 lease is now active after successful `prepare`. Exact-M push CI is fully successful. No publication has been attempted at this checkpoint; fresh `prepublish` and post-publication verification remain required.**

The owner has authorized merging and publishing PR #2039, subject to the established gates. Emergent Support supplied the previously missing Production index definitions after a read-only inspection. Terminal access has now been restored. Production advanced through PR #2046, so the former A/B pair and its successful CI are historical and cannot authorize the new deployment.

### Latest milestone: shared handoff and own lease prepared

The root observed shared `/app` materialization **PASS at 2026-11-21T19:44:54.157752Z** (22:44:54 Asia/Riyadh). The destination is exact deployment M `6e41bf25b936e8c4047df75bb170610dfc71b7f7`, whose tree `c3c66e2f15138523a9a72ee0910706f91f9b47e4` equals frozen B. Exactly the three generated outputs were materialized: `frontend/build/`, `frontend/.release/reproducible-build.json`, and `backend/release_identity.json`, totaling **356 regular files / 11,463,353 bytes**. Previous outputs were preserved at `/app/.git/mezan-pr2039-materialize-a7la6r4v/previous`; observed remaining `/app` capacity was **53,198,848 bytes**. Normal destination frontend verification with expected source A passed.

The official `production_release_guard.py prepare --actor codex-pr2039-owner-authorized` then passed at **2026-11-21T19:46:13.522595Z** (22:46:13 Asia/Riyadh), binding protocol 5, deployment M, source A `e08d0e489eded39961d374e4eb7bfcc0544b6e90`, and runtime release `rg5-45eb8f2d79e0912a1277e1e14816e9d9c5e3a40d0a6d784834c6fbb54996974f`. **The active lease is this task's own lease**, actor `codex-pr2039-owner-authorized`; it has no automatic expiry. Do not create a replacement lease, assume an inactive guard, or clear it on another task's behalf. See [materialization-prepare.json](materialization-prepare.json).

The final exact-M GitHub audit at **2026-11-21 19:46:03 UTC / 22:46:03 Asia/Riyadh**, independently read by root and the CI reviewer, found **all 3 workflows and all 11 checks successful**, with zero pending, failed or skipped checks and no legacy statuses. Pagination was complete and every run/check belonged to M. The Production ref remained M; its ordered parents were J and B and its tree equaled B. See [M-ci-evidence.json](M-ci-evidence.json).

| Exact-M push workflow | Conclusion | Run |
| --- | --- | --- |
| Security Gate | success | [44941832737](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44941832737) |
| CodeQL | success | [44941832749](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44941832749) |
| Mezan Release Readiness | success | [44941832677](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44941832677) |

**Next safe action:** confirm the existing lease still belongs to this task and the local/remote deployment SHA remains exact M, then run the official `prepublish` immediately before one authorized publication. Do not repeat `prepare`. Acceptance requires a newer explicit `Deployment Succeeded`, the official three matching live probes, and final `active:false`. Stop on identity/SHA drift, a different lease owner, a new failed check, or any guard refusal. Prepared state is not evidence that Emergent has published or that Production serves the intended release.

Production changed by this task through this checkpoint: **Git branch yes (normal merge M); shared `/app` yes (reviewed handoff); live runtime no**. No financial/index mutation or APK publication occurred.

### Previous milestone: official Emergent rehearsal passed

The root observed the official rehearsal in a task-owned normal clone at `/tmp/mezan-pr2039-release-0sv39fp_`, from **2026-11-21T19:40:23.380281Z** to **2026-11-21T19:41:54.752195Z** (22:40:23 to 22:41:54 Asia/Riyadh). The actual `cd frontend && yarn build` adapter invocation passed in **91.37 seconds**, followed by a successful strict `verify_frontend_build.py --expected-git-sha e08d0e489eded39961d374e4eb7bfcc0544b6e90 --reviewed-intent-v5` check. This is observed Emergent rehearsal evidence, recorded from the root's UI result; it is not a claim that this evidence writer reran the commands.

The rehearsal used deployment M `6e41bf25b936e8c4047df75bb170610dfc71b7f7`, with tree `c3c66e2f15138523a9a72ee0910706f91f9b47e4` equal to frozen B `3628efc4215ee95e6196fca4807104a87398e255`. Its source remains A `e08d0e489eded39961d374e4eb7bfcc0544b6e90` and its runtime release remains `rg5-45eb8f2d79e0912a1277e1e14816e9d9c5e3a40d0a6d784834c6fbb54996974f`.

| Retained output | Observed SHA256 |
| --- | --- |
| `backend/release_identity.json` | `e01d069eda633f2c617677f2d13a06b54d8aba954d89d5fa06c916bc5987040e` |
| `frontend/build/build-meta.json` | `e2ad2261a00b555105f8800ad875b92a9c406e9c03b19a93cea945b987755ba2` |

Both hashes match the reviewed exact-B adapter evidence. The rehearsal changed no shared source, created no lease, and attempted no publication. See [emergent-rehearsal.json](emergent-rehearsal.json) for the sanitized machine-readable observation. Generated outputs remain outside this evidence branch.

**Next safe action at the rehearsal checkpoint (shared handoff and prepare are now completed above):** refresh the live shared guard and actual Production SHA; continue only with `active:false` and unchanged M. Fast-forward shared `/app` only to exact M, preserve unrelated work and previous generated artifacts, and materialize only the three verified generated outputs from this rehearsal. Revalidate destination source/bytes, prepare this task's own lease, and run `prepublish` immediately before one authorized publication. Acceptance still requires a newer explicit `Deployment Succeeded`, the official three matching live verification probes, and final `active:false`. An isolated rehearsal cannot prove the later Cloud Build handoff or a live deployment. Production push CI on M remains subject to freshness monitoring.

Production changed by this task through this checkpoint: **Git branch yes (the recorded normal merge M); live runtime no**. No financial/index mutation or APK publication occurred.

### Previous milestone: normal merge completed

PR #2039 was merged at **2026-11-21 19:36:18 UTC / 22:36:18 Asia/Riyadh** using `merge_method=merge` and expected HEAD B. GitHub returned merge **M `6e41bf25b936e8c4047df75bb170610dfc71b7f7`**. Root separately verified the actual Production ref equals M, M's ordered parents are J `a9d0aec2939a706dcc16f9948e7a81cfe019e66f` and B `3628efc4215ee95e6196fca4807104a87398e255`, and M's tree is exactly B's tree `c3c66e2f15138523a9a72ee0910706f91f9b47e4`. No squash, rebase or protection bypass was used.

The final exact-B audit at **22:34:56 Asia/Riyadh** includes the two additional workflows triggered when the PR became Ready: **14 runs = 13 success + 1 expected TikTok skip; 35 latest checks = 25 success + 10 conditional skips; zero failed, pending or neutral**. All four visible required protected contexts passed under GitHub Actions app 15368. Root independently reread unfiltered runs, checks, actual Production, PR mergeability and commit statuses immediately before the merge. Full administrative protection visibility remains limited, so the native merge route enforced any additional requirements.

Actual artifact results: Operational Backend **6,186 tests + 90 subtests** (6,276 JUnit entries), separate shipping-preservation **16 + 18 subtests**, Operational Frontend **1,258 / 83 suites**, Security Frontend **4,815 / 449 suites**, Stock **5,502 + 97 subtests**, all passing. Root independently parsed the actual backend XML and frontend JSON, including the external-party regression cases. Both actual Node20/24 adapter jobs passed; an independent reviewer checked all four artifact ZIP digests and all 357 generated files per host. See [new-b-ci-evidence.json](new-b-ci-evidence.json) and [new-b-adapter-verification.json](new-b-adapter-verification.json).

PR #2046's owner completed publication and three-probe verification and closed its lease; its canonical Issue #1006 block was updated at **19:32:36 UTC**. Root's live shared checks at **19:32:58 UTC** and **19:35:39 UTC** independently returned exactly `active:false`, branch `hotfix/prod-snap-meta-final`, HEAD J, and no tracked changes. Before our own rehearsal or `/app` mutation, read the guard again. No lease has yet been created by this task.

Next safe action at the merge checkpoint (the rehearsal portion is now completed above): fresh inactive shared guard and unchanged actual Production M; rehearse the unchanged official adapter in a normal task-owned clone under `/tmp` pinned to M with tree equal to B, using public origin `https://mezansalla.com` and an isolated external cache. Then fast-forward shared `/app` only to exact M, preserving unrelated work and safely materializing only this rehearsal's verified generated outputs. Recheck destination bytes/source/guard, prepare the task's own lease, prepublish immediately before one authorized publication, observe newer explicit success, and require the official three matching live probes with final `active:false`. Do not substitute another task's artifacts or lease. Production push CI on M is also being monitored for failures or drift.

Production changed by this task: **Git branch yes (M); live runtime no at this checkpoint**. No financial/index mutation or APK publication occurred.

### Recovery and renewed A/B evidence

The user requested another connection attempt. Reloading the existing code-server window restored the terminal. A fresh read at **2026-11-21 19:08:12 UTC / 22:08:12 Asia/Riyadh** found the shared workspace on `hotfix/prod-snap-meta-final`, HEAD `a9d0aec2939a706dcc16f9948e7a81cfe019e66f`, with no tracked changes. The live v5 guard was **active**, owned by `codex-pr2046-owner-authorized`, prepared at `2026-11-21T19:02:54.427154+00:00`, with source `eeef3913ae2e1b8fa4b58f3835478e1ed17b2988` and release `rg5-ddbdc7c7bfa083d85224a0f0275318ca7dacfdd05d3f628e8f3dc05c278be895`.

The latest relevant [Issue #1006 handoff](https://github.com/AMASI-SA/AMASI-SA/issues/1006#issuecomment-6238987123), updated at 19:04:27 UTC, records one publication click for PR #2046 and verification still pending. This task has not changed shared `/app`, cleared/reused that lease, or clicked publication. `/app` had only 67 MB available while `/tmp` had 227 GB; recheck capacity before any future shared rehearsal and preserve unrelated work.

Production advanced by eight commits and 47 files. GitHub consequently marked the old PR #2039 pair conflicting. None of the four external-party feature files changed in that Production delta, so the original four-file source patch was replayed exactly in an isolated worktree. Root review confirmed all four resulting blobs match the previously reviewed feature, all new Production changes remain inherited, and the current Production intent was left unchanged. `git apply --check`, `git diff --check`, and Python AST checks passed. Full local backend/frontend execution was unavailable because the isolated environment lacked its test dependencies and MongoDB; fresh GitHub CI remains required.

| Current role | Git commit | Git tree |
| --- | --- | --- |
| Current Production base J | `a9d0aec2939a706dcc16f9948e7a81cfe019e66f` | `7bb7011017bab4fb7367cbd217bc97ed47dcb78c` |
| New source A | `e08d0e489eded39961d374e4eb7bfcc0544b6e90` | `fdd3567f46f7c0597673fd61516ec712ede74465` |
| New frozen intent B / current PR HEAD | `3628efc4215ee95e6196fca4807104a87398e255` | `c3c66e2f15138523a9a72ee0910706f91f9b47e4` |

The former B is preserved remotely on `codex/pr2039-archived-b688995ce-20261121`. PR #2039 was first renewed at A with an expected-old-HEAD lease, then fast-forwarded to B with expected A. It remained Draft during validation and was subsequently marked Ready and normally merged as recorded above. Do not reuse the old candidate or CI.

The new [source-A readiness run](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44939561224) produced artifact `14721325582`. Root and an independent reviewer checked the ZIP digest against GitHub, exact candidate bytes, the official candidate loader, and source/base/worktree/Git manifests. Root then froze B changing only `release/release-intent-v5.json`, and both reviewers ran the official reviewed-intent loader and Git verifier successfully on B. The generated-runtime identity file is intentionally absent in these isolated source checkouts; these checks are not a shared-host materialization rehearsal.

| Renewed release evidence | Value |
| --- | --- |
| Candidate ZIP SHA256 | `d9f1a47d2b99e0b3834f3b73627163aa453e0e44bbfc9302fe43739c71c32c72` |
| Intent blob | `e27918ddea60f1f8ca038ae25f0a4e6ea0e92cb6` |
| Intent bytes / SHA256 | 1,444,323 / `bbc9727bbcc8b10c9cefa8c830877f7768999fbec77a358e0b9d3f0b09bd614d` |
| Runtime release ID | `rg5-45eb8f2d79e0912a1277e1e14816e9d9c5e3a40d0a6d784834c6fbb54996974f` |
| Frontend artifact tree SHA256 | `9bf4c7bfad899f2b011d16811ca3bb1bbc82c3565488e63e48ea4e00945fdd18` |
| build-meta.json SHA256 | `e2ad2261a00b555105f8800ad875b92a9c406e9c03b19a93cea945b987755ba2` |
| Manifest files | Frontend 1,345; Backend runtime 1,288; release control 322 |

At B freeze, A's Security Gate, CodeQL and readiness had passed; Operational Balance Backend and Stock were still pending. No all-A success is claimed or carried forward. The final authority is the new complete CI cycle on B, including real Node20/24 adapter rehearsals. No old B result substitutes for it.

The exact-B CI and foreign-publication completion steps described during recovery have now passed. The latest milestone above governs the remaining Emergent procedure. Any drift invalidates the corresponding snapshot. The Production-index attestation remains accepted; no new URI entry or financial/index changes are warranted.

### Accepted Production index evidence

Emergent Support inspected the exact Production database and collection at **2026-11-21 18:21:48 UTC / 21:21:48 Asia/Riyadh**. Its reply arrived at **18:27:46 UTC / 21:27:46 Asia/Riyadh** from `support@emergent.sh` in the original request thread. Gmail-reported DKIM for `emergent.sh`, SPF, and DMARC passed. The root read the actual reply; this is platform-operator evidence, not a successful rerun of the workspace helper.

| Production index | Verified definition |
| --- | --- |
| `user_id_1_name_lower_1` | Keys `user_id:1`, then `name_lower:1`; `unique:true`; sparse not set; no partial filter; collation absent/default; fully built |
| `user_id_1_id_1` | Keys `user_id:1`, then `id:1`; `unique:true`; sparse not set; no partial filter; collation absent/default; fully built |

Support confirmed exactly three indexes including `_id_`, with no build in progress. It performed no index/document mutation, restart, or deployment. It also confirmed that direct workspace-terminal access to Production is unsupported and that the observed timeout is expected on that route. Another URI entry or preview-terminal retry is unnecessary. The sanitized evidence is recorded in [production-index-attestation.json](production-index-attestation.json).

The current Production-index gate is **PASS**. Earlier `UNVERIFIED` and timeout sections below are historical and superseded by this reply; do not ask for another URI entry or repeat the preview-terminal check.

Historical GitHub audit at **2026-11-21 21:53:11 Asia/Riyadh** found the former B and J unchanged, eleven successful workflows plus the expected TikTok skip, 23 successful checks/six conditional skips, and zero failed or unfinished checks. Those results apply only to that superseded pair.

In the earlier attempt, the root found code-server disconnected and could not obtain a fresh guard result. That access problem was resolved by the later window reload described above. Neither the historical inactive-guard snapshot nor the old CI authorizes execution.

At the earlier support/recovery checkpoint, Production had not changed through this task. The later normal merge is recorded above; this task still has not published or changed financial records/indexes or APKs.

This document and its companion diagnostic belong to the standalone evidence branch `codex/pr2039-evidence-20261121`. **The evidence branch is not a deployable release.** Do not merge its documentation/helper changes into a frozen A/B pair or check it out in shared `/app`. Current source and release identities are listed in the latest update above.

## Historical scope and superseded immutable identity

- Task: `EXTERNAL-PARTY-2039-20261121`.
- Repository: [AMASI-SA/AMASI-SA](https://github.com/AMASI-SA/AMASI-SA).
- Pull request: [#2039](https://github.com/AMASI-SA/AMASI-SA/pull/2039).
- Feature/release branch: `codex/external-party-completion-20261121`.
- Production branch: `hotfix/prod-snap-meta-final`.
- Earlier source `d25c249e258c3ee9edcb88cb71b9f74999698cc2` and its CI/candidate are historical; they were superseded after Production advanced.
- The prior source was preserved on `codex/external-party-currentbase-20261121-v2`.

| Role | Git commit | Git tree |
| --- | --- | --- |
| Reviewed Production base J | `66aa13967156e8e279b5297caf697c155f720fd1` | `e01b51ceb8e2f39566080b99da1b5802728ae858` |
| Source A | `55c37fe701eb377e60f6c39c40ed3d8aed2a338c` | `4610685a07a1cf561965bbe6ca416380014976d9` |
| Frozen intent B / reviewed PR HEAD | `688995ce7fa958356f2ed7bb4c3e15c97ef51afb` | `9b95ee96f80d1e9261b52b2abe320ebf5680b6a3` |

Source A's parent is J. B's parent is A. GitHub comparison independently showed A → B ahead 1 / behind 0, merge base A, and exactly one changed file: `release/release-intent-v5.json`.

J → A changes only:

- `backend/operational_balance_routes.py`
- `backend/tests/test_operational_balance_app_permissions.py`
- `frontend/src/pages/operational/AddOperationalEntity.jsx`
- `frontend/src/pages/operational/OperationalBalances.test.jsx`

These four feature blobs are byte-identical to the previously tested feature, replayed onto J. The feature adds optional external-party classification, phone and notes, normalized duplicate-name rejection, and retry compatibility. Existing permissions remain enforced; party creation adds no movement or opening balance.

Do not squash, rebase, amend, or append source/documentation changes to frozen A/B. If the source or relevant Production base changes, establish a new legitimate source A, build and validate a new candidate, freeze a new intent-only B, and rerun the applicable gates.

## Historical official release artifact (superseded)

| Field | Value |
| --- | --- |
| Protocol / intent schema | v5 / schema 2 |
| Tracked intent | `release/release-intent-v5.json` |
| Intent Git blob | `dabdac50e3a11bacace209ac3f356ea1d043e425` |
| Intent JSON bytes | 1,441,068 |
| Intent JSON SHA256 | `c8ec1c92b52143dfb2c0d4faa5a028999492263bc29510d8b474e53c4f9cbafc` |
| Deterministic runtime release ID | `rg5-5728ed2c97542fc0ae55957c803e4b6db4b6b09a004ad8498c81f43f53fc9cb3` |
| Source-A readiness run | [44929584362](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44929584362) |
| Source-A candidate artifact | [14716853085](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44929584362/artifacts/14716853085) |
| Candidate ZIP SHA256 | `67ec715f34de6ccacb681e072ff6952704e2309b64ca9f4ae0e63395e72abc04` |
| Frontend artifact tree SHA256 | `2c208bf47d7609c7b4ed77d6c9b5705eaadf3d4d418a8c6d7d1dd1ea0c9e450a` |
| `build-meta.json` SHA256 | `71282c9d7582be95f59f10521c14c5d49812f42b651fd0d709d7d03f1e3ed344` |

The candidate ZIP digest was checked against GitHub's artifact digest. In an isolated clean clone at exact A, the official `load_candidate_release_intent` validator exited 0 after validating source/base ancestry, source transition, runtime identity, frontend contract, critical hashes, and the Backend/control manifests rederived from Git J → A. Manifest counts were 1,283 Backend runtime files, 322 release-control files and 1,343 Frontend source files.

The committed B intent blob independently matches the validated candidate bytes. The root also ran the official reviewed-B loader successfully.

An empty `fetch_file` response for this large intent is not evidence of an empty file. The tracked intent is larger than 1 MB; blob retrieval and local Git hashing establish its content.

## Historical final CI evidence bound to former B

CI snapshot: **2026-11-21 17:50:43 UTC / 20:50:43 Asia/Riyadh**, from the independently collected `ci-review/B-summary.json`. Every result in this section belongs to B `688995ce7fa958356f2ed7bb4c3e15c97ef51afb`, not an earlier commit.

- Workflows: **11 success, 1 scoped skip**, 0 failure/in-progress at the snapshot.
- Checks: **23 success, 6 scoped skips**, 0 other conclusions.
- PR at snapshot: open, draft, unmerged, `mergeable: true`, `mergeable_state: clean`.
- Operational Balance Web, Security Gate, Mezan Release Readiness, CodeQL, Operational Stock Eligibility, and the other applicable workflows succeeded.
- The skipped workflow is `TikTok Creator Release Candidate`. Its `reviewed-candidate` job is restricted to branch `codex/tiktok-content-ci-20261011`; it does not apply to this external-party PR.
- The six skipped checks were `Emergent manual redeploy required`, `Publication frontend and existing media flow`, `Publication backend and existing write guards`, `Frontend Snapchat settings tests and build`, `Backend settings and zero-write regressions`, and `reviewed-candidate`. Skips are recorded explicitly and are not counted as executed tests.

The root's closeout refresh again found all twelve runs completed with these conclusions. Fresh Production → B comparison remained ahead 2 / behind 0 with merge base J and exactly the four feature files plus the intent file.

| Workflow | Conclusion on B | Run |
| --- | --- | --- |
| Operational Balance Web | success | [44931316386](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44931316386) |
| Security Gate | success | [44931316204](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44931316204) |
| Mezan Release Readiness | success | [44931316160](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44931316160) |
| CodeQL | success | [44931316264](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44931316264) |
| Operational Stock Eligibility | success | [44931316352](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44931316352) |
| G47 Focused Integration | success | [44931316157](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44931316157) |
| Mobile Order Tracking | success | [44931316345](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44931316345) |
| MZ2 Employee Salary and Password | success | [44931316182](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44931316182) |
| Qoyod Payment Freshness | success | [44931316189](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44931316189) |
| Snapchat Settings Management V2 | success | [44931316258](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44931316258) |
| Snapchat Direct Active Publication | success | [44931316381](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44931316381) |
| TikTok Creator Release Candidate | skipped; separate branch scope | [44931316311](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44931316311) |

| Suite | Result on B | Retained artifact |
| --- | --- | --- |
| Operational Backend | 5,955 tests and 90 subtests (6,045 XML entries); 0 failures/errors/skips; both new external-party cases passed | [14717698104](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44931316386/artifacts/14717698104) |
| Operational Frontend | 1,258 tests in 83 suites; 0 failures/pending/todo; new external-party form case passed | [14717543900](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44931316386/artifacts/14717543900) |
| Security Frontend | 4,785 tests in 448 suites | Security Gate on B |
| Operational Stock | 5,486 tests and 97 subtests | Operational Stock Eligibility on B |

Operational Backend artifact ZIP SHA256: `c627c0351988abc9001b4648ed607f45bc92479f340a5a699c6567ac7167493a`.

Operational Frontend artifact ZIP SHA256: `3d540e6e40e805dfb6e06035ff9ec1aababb11391f89d1a968f2273dd7baa009`.

### Actual reviewed-B adapter rehearsals

[Mezan Release Readiness run 44931316160](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44931316160) completed successfully. Both actual clean-clone adapter rehearsals executed and passed; these were not the source-A skips.

| Host | Successful rehearsal job | Sanitized evidence artifact | ZIP SHA256 |
| --- | --- | --- | --- |
| Node 20.20.2 | [136928695912](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44931316160/job/136928695912) | [14718155921](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44931316160/artifacts/14718155921) | `6cab66c719def0cc0f26150905feb03bccfdcec9ce59beac6a383a41badbebae` |
| Node 24.19.0 | [136928695768](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44931316160/job/136928695768) | [14718195836](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44931316160/artifacts/14718195836) | `90817faa11cc55c40a8e3197baa50fff006125a8db10b9cc7ef5bebbf0301755` |

Both evidence ZIP digests were verified. Their package-boundary proofs are identical as parsed and bind the exact source A and runtime release ID above. The Backend proof reports `isolated_verified=true` and `health_verified=true` without Git or a sibling Frontend package. The Frontend proof reports `isolated_runtime_artifact_verified=true` and an `application/json` build-metadata content-type contract.

These are CI/package proofs, not observations of a live Production publication.

## Production database and environment evidence

The root selected **Production** through the supported Emergent MongoViewer UI and positively identified database `salla-analytics-test_database`, collection `mz2_external_persons_v2`.

The root's read-only Production metadata inspection found:

| Observation | Result |
| --- | --- |
| External-party documents | 7 |
| Missing names / normalized names | 0 |
| Legacy normalization mismatches | 0 |
| Duplicate normalized names within an owner | 0 |
| Direct definition of unique `(user_id, name_lower)` index | Initially unverified in the UI; **PASS in the later authenticated Support inspection** |
| Direct definition of unique `(user_id, id)` index | Initially unverified in the UI; **PASS in the later authenticated Support inspection** |

No individual names, owner identifiers, phone numbers, notes, MongoDB URI, credentials or financial values are retained in this record.

The earlier `ModuleNotFoundError` occurred before an index query. Missing audit dependencies were installed only in an isolated temporary virtual environment; Production requirements were not changed. The connection loaded from the shared `/app` backend environment pointed to an empty **preview** database. That result must not be used as evidence that a Production index is absent or that Production contains zero parties.

The supported Production MongoViewer read path did not establish the index definitions. Clean name metadata cannot replace direct unique-index evidence because race safety relies on server-enforced uniqueness.

## Shared workspace and release guard

Fresh root observation in the current session, from a new isolated Emergent Terminal 2 after renderer reload:

- `python scripts/production_release_guard.py status` returned exactly `{"active": false}`.
- `git rev-parse HEAD` returned J `66aa13967156e8e279b5297caf697c155f720fd1`.
- `git status --porcelain --untracked-files=no` emitted no entries.
- No tracked `/app` edits were made by this task.

**Guard observation:** approximately **2026-11-21 17:55 UTC / 20:55 Asia/Riyadh** in the current session. The root's clock read **17:56:07 UTC** shortly afterward; the terminal observation itself did not include a more precise timestamp.

This guard observation is a point-in-time result. Reconfirm the live guard immediately before any shared-workspace release operation. Do not clear, reuse or override a lease owned by another operation.

## Companion read-only diagnostic

Companion file: [check_production_indexes.py](check_production_indexes.py).

Reviewed helper SHA256: `9bce7cbf3416380ecb4b36052b5dbb9519ec204cf7c822889c03ac80b9b8c464`. Root verification: AST parse and `--help` passed. This is not evidence that the helper has completed its actual Production database check.

The helper requires isolated `pymongo==4.18.3` and `dnspython==2.9.2`. It imports no application module, reads no dotenv file, and accepts the independently confirmed actual Production URI only through hidden terminal input. It never accepts the URI in command arguments or environment variables. If a non-echoing terminal is unavailable, it stops.

It reads only index definitions and bounded party identity/name metadata. It does not create/drop indexes, insert/update/delete documents, write financial records, create a lease or publish. Its output contains counts and verdicts only; exceptions are reduced to error types.

The full unique-name and unique-ID indexes must have the exact ascending compound keys, `unique=true`, no sparse or partial filter, and simple collation. The helper checks missing/invalid owners and names, stored and normalized duplicate groups, and normalization agreement using `" ".join(name.split()).casefold()`.

### Limits of this diagnostic

- The URI filter rejects obvious local/preview targets; it does **not** prove that an otherwise accepted remote URI is Production. Confirm the target independently in the Emergent Production administration surface before hidden entry.
- The scan is capped at 10,000 documents. Exceeding the cap, empty results, dependency mismatch, unsupported secure input, query/index access errors or failed invariants block the check.
- Before/after counts and index definitions are compared, but separate majority reads are not an atomic snapshot. Matching counts do not prove the absence of concurrent in-place document changes.
- The check covers this collection's name/index prerequisites. It does not validate all financial state, authorize a release, prove current GitHub head/base, prove an inactive lease, or substitute for post-publication probes.
- Exit 0 means this bounded preflight passed. Exit 2 means blocked or unverified. A failure must be investigated; do not automatically repair names, indexes or financial data.

## Exact next safe action

### Follow-up: Production connection timeout, 21 November 2026

The owner ran the immutable helper from evidence commit `58f0d13c23716467afa3e5f4a99440cba27bf2a1` in the Emergent workspace. Both pinned dependencies installed successfully, `DIAGNOSTIC_INTEGRITY: PASS` was printed, and the actual database attempt returned only:

```json
{"error_type": "ServerSelectionTimeoutError", "status": "ERROR"}
```

This establishes a failed server-selection attempt, not an absent index, wrong password, failed application test, or completed financial preflight. No secret was supplied in chat. The root's current browser attachment to the terminal also timed out (`Page.enable`); the earlier `active:false` guard observation has not been refreshed in this follow-up and is not current execution authority.

Emergent's [Database guide](https://help.emergent.sh/database-mongodb) documents separate Preview and Production databases and restricts direct external access to the managed Production cluster. Workspace-terminal export is documented for Preview. The [FAQ](https://help.emergent.sh/faqs) and [export guide](https://help.emergent.sh/export-your-app) point to the in-platform Database Manager or platform support for Production. These facts and earlier recorded 10/15-second failures from this workspace support an access-path limitation; the one exception type does not identify its exact DNS/TCP/TLS/topology cause. Do not ask the owner to enter the same URI again or blindly increase timeouts.

The existing production source has no general external-party index-metadata HTTP route. The only production `listIndexes` diagnostic found is explicitly scoped to Google Ads namespaces and must not be repurposed. The public health/readiness routes do not query MongoDB and cannot prove the live index definitions.

Fresh GitHub audit at **2026-11-21 21:11:38 Asia/Riyadh**: HEAD remains B, Production remains J, all twelve runs remain attempt 1 (eleven successes and the scoped TikTok skip), and all 29 checks retain 23 successes/six conditional skips. No CI failure or changed source. PR remains draft/unmerged and mergeable/clean. The four visible required contexts pass. Full administrative branch-protection details were not accessible to the integration (403), so their absence is not claimed; any eventual merge must use the normal protected route without bypass.

### Historical next action after the support reply (superseded)

1. Keep PR #2039 at frozen B. The owner separately authorized the support request, it was sent once, and the requested read-only index evidence has now arrived and passed review. Restore the shared Emergent terminal connection and obtain a fresh guard/status/HEAD/clean-tracked-tree snapshot. Preserve all unrelated terminals, ignored artifacts, staging directories, and untracked work. Do not request the Mongo URI again or alter financial records/indexes.

   The companion helper remains available for a **confirmed supported Production-connected runtime**, not as another preview-terminal retry:

   ```bash
   python -B docs/operations/EXTERNAL-PARTY-2039-20261121/check_production_indexes.py \
     --database salla-analytics-test_database
   ```

   Retain only its sanitized JSON verdict. Do not paste or store a MongoDB URI in chat, Git, logs, command arguments or this record. Do not use the preview environment connection as a substitute.

2. With the platform-operator index evidence accepted, freshly revalidate PR HEAD equals B, Production equals the expected J, all applicable checks belong to that exact HEAD, mergeability/no conflicts, and live guard `active:false`. Resolve any changed head/base by the official new A/B process; never reuse stale CI or an obsolete candidate.

3. Only after all gates pass, the root may complete the already authorized normal merge preserving A/B and verify that the resulting deployment tree is equivalent to B. Then follow the existing `AGENTS.md` Emergent protocol: exact production SHA, clean tracked workspace, local official adapter rehearsal, matching lease, prepublish verification, one authorized publication, explicit newer deployment success, and the official three consecutive live verification probes ending in `active:false`.

Do not execute a deploy command until current live guard/workspace checks and the remaining protocol steps pass. The Production index evidence is now verified; the terminal's old scrollback is not a fresh release-lock check. Do not weaken a gate, change financial indexes/data, bypass the release guard, or publish an APK to complete this task.

## Handoff ownership

The root owns final freshness checks, the direct Production index result, the guard timestamp, evidence-branch checkpoint/Issue #1006 handoff, and any gated merge/publication. This document records current evidence; it does not claim a successful deployment. The original feature/release branch and its frozen A/B pair remain separate from this evidence checkpoint.

Issue #1006 has reached GitHub's comment limit. This dedicated evidence branch preserves the continuation record without replacing another task's shared continuation comment.

