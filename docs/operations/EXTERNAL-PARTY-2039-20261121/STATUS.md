# External-party completion — PR #2039

## Checkpoint verdict

**CI and the official protocol-v5 A/B release are ready. Merge and publication remain blocked on direct Production index metadata.**

The owner has authorized merging and publishing PR #2039, subject to the established gates. The remaining blocker is missing evidence, not a request for new permission. The Production name index has **not** been observed missing; its definition remains **unverified**.

Production changed by this operation: **no**. No merge, release lease, publication, financial record/index mutation, or APK publication has occurred.

This document and its companion diagnostic belong to the standalone evidence branch `codex/pr2039-evidence-20261121`. **The evidence branch is not a deployable release.** Do not merge its documentation/helper changes into frozen B or check it out in shared `/app`. The original PR branch remains frozen at B.

## Scope and immutable identity

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

## Official release artifact

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

## Final CI evidence bound to B

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
| Direct definition of unique `(user_id, name_lower)` index | **UNVERIFIED** |
| Direct definition of unique `(user_id, id)` index | **UNVERIFIED** |

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

### Current next action

1. Keep PR #2039 at frozen B. Obtain read-only `listIndexes` metadata from an already authorized production-connected operator or Emergent support for database `salla-analytics-test_database`, collection `mz2_external_persons_v2`. Request key order, `unique`, `sparse`, presence of `partialFilterExpression`, collation, confirmation the relevant indexes are fully built, and the inspection timestamp. No document contents, connection string, export, index mutation, restart or publication is needed. No support message has been sent: external correspondence requires the owner's separate authorization.

   The companion helper remains available for a **confirmed supported Production-connected runtime**, not as another preview-terminal retry:

   ```bash
   python -B docs/operations/EXTERNAL-PARTY-2039-20261121/check_production_indexes.py \
     --database salla-analytics-test_database
   ```

   Retain only its sanitized JSON verdict. Do not paste or store a MongoDB URI in chat, Git, logs, command arguments or this record. Do not use the preview environment connection as a substitute.

2. If the helper passes, freshly revalidate PR HEAD equals B, Production equals the expected J, all applicable checks belong to that exact HEAD, mergeability/no conflicts, and live guard `active:false`. Resolve any changed head/base by the official new A/B process; never reuse stale CI or an obsolete candidate.

3. Only after all gates pass, the root may complete the already authorized normal merge preserving A/B and verify that the resulting deployment tree is equivalent to B. Then follow the existing `AGENTS.md` Emergent protocol: exact production SHA, clean tracked workspace, local official adapter rehearsal, matching lease, prepublish verification, one authorized publication, explicit newer deployment success, and the official three consecutive live verification probes ending in `active:false`.

No deploy command is authorized by this evidence record while direct Production index metadata remains unverified. Do not weaken a gate, change financial indexes/data, bypass the release guard, or publish an APK to complete this task.

## Handoff ownership

The root owns final freshness checks, the direct Production index result, the guard timestamp, evidence-branch checkpoint/Issue #1006 handoff, and any gated merge/publication. This document records current evidence; it does not claim a successful deployment. The original feature/release branch and its frozen A/B pair remain separate from this evidence checkpoint.

Issue #1006 has reached GitHub's comment limit. This dedicated evidence branch preserves the continuation record without replacing another task's shared continuation comment.

