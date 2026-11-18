# SNAP-UNASSIGNED-PROFIT-20261118 — current production integration

## Current state

Current PR: [#1993](https://github.com/AMASI-SA/AMASI-SA/pull/1993), **merged**, branch `codex/snap-unassigned-profit-20261118-r2`.

PR #1992 was closed without merging because production advanced during the preceding release's verification. Its frozen source/intent pair remains preserved and must not be deployed. The previous complete record is [07322bbdb2c8c58d8f276b3176cdd942faba1df6](https://github.com/AMASI-SA/AMASI-SA/blob/07322bbdb2c8c58d8f276b3176cdd942faba1df6/docs/operations/snap-unassigned-profit-20261118/STATUS.md).

**Production GitHub branch changed: yes**, merge M `8761f7b2f80faec29638e1438b8fedefcec64a28`. Live publication completed: new version **`ed5f35c`**, green **Deployment finished Nov 18, 09:56 PM** (Asia/Riyadh), newer than `8a0d442`. Official three-round verification is next; this task still owns its original lease. Production is published but not yet fully verified.

## Scope and integration

The user retained the operating-expense and final-net footer and requested a numeric contribution for “سناب غير محدد” instead of “بانتظار توزيع الصرف”.

The contribution is the cohort's own verified sales minus product, shipping and customer-payment costs. It supports positive, zero and negative amounts in daily and half-day views. Missing evidence displays “بيانات الربح غير مكتملة”.

Advertising spend/bank fees remain on actual accounts and are counted once. Full account profit stays unavailable for the unknown cohort. Canonical aggregate, operating expenses and final net are unchanged; no residual or second addition is used.

Current production PR #1991 introduced 17 changed/added paths. An independent Git review verified every one is byte-identical in this candidate, with no overlapping path. All 10 dashboard source/test blobs remain byte-identical to the previously reviewed change. Backend and AdvancedDashboard source are unchanged versus the new production base.

## Current source candidate

- Production base J2: `ce94ce01b0e631a6373ee3524119f26959bf89a4`
- Base production release: PR #1991 (approved Aramex status-fee work)
- Source A2: `84ae882f78ae954449cc4f3380b17a8ff808cb3a`
- Source tree: `da532bd626f20662fa7840963d4f183f7f3c2918`
- Intent-only B2: `7268ab80e0781c288943f290ea020b1b27f1e57d`
- Deployment B2 tree: `b8bfb1aece57f3f89282216e4171398bd8854c9d`
- Production merge M: `8761f7b2f80faec29638e1438b8fedefcec64a28`
- M tree equals B2; A2 → M changes exactly the release intent. Fresh production ref was verified at M.
- Candidate runtime ID: `rg5-c8f8108755b4a5d612b50e91902fbe83b014b80ce076b3372408f1bf41fc286b`
- Candidate frontend artifact tree: `1f7da282b51120ea6954d2a799092e07464dd6576e0e9abb994107ec1b3e19be`
- Candidate build-meta SHA-256: `2c9dd4e546c6ead76090b0afe1d74b8d190aeff51cba1437597616797980f306`
- Candidate intent SHA-256: `6f1297fd5a11d96c2a2fee094f542b4f51f121a4b681d70ec3f3d50d07ec1165`

A2 retained J2's tracked intent; B2 changes only that intent file to the validated candidate. Local reviewed-intent validation at exact B2 passed (exit 0), its digest matches, and the worktree is clean. Do not add source changes after A2, squash/rebase the frozen pair, or copy the superseded PR #1992 intent.

## Verification

- Regression reproduced the defect before implementation.
- Five focused frontend suites: **328 tests passed, exit 0**, both before and after integration on J2. The current-A2 rerun completed in 6.54 seconds.
- Independent integration/financial-contract review: no blockers.
- Exact Git checks: all 10 dashboard blobs preserved; all 17 newer production paths preserved; no overlap; `git diff --check` passed.
- Source A2 release readiness [44368961758](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44368961758) passed.
- Source A2 dashboard checks [44368961767](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44368961767) passed.
- Candidate artifact `14457802088`, archive digest `5bdc2a62ab3629f519c67ab3e398c4b16590b5ed17254be5e93b34364927a65a`.
- `load_candidate_release_intent` at exact A2 verified archive digest, source/base, schema and full manifests, exit 0.
- Required source-A2 security/build checks passed before B2 was frozen. Exact-B2 release readiness [44369862714](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44369862714), both host adapter rehearsals, required security/build checks, focused dashboard acceptance and review passed before merging.
- Latest exact-B2 check read at 18:37 UTC: 44 checks, 33 success, 10 skipped, 1 neutral, none pending or failing.

Focused command from `frontend`:

```bash
CI=true node node_modules/react-scripts/bin/react-scripts.js test --watchAll=false --runInBand --runTestsByPath src/lib/dashboardAdsAccountResults.test.js src/components/DashboardAdsSpendCard.test.jsx src/components/DashboardAdsAccountRow.test.jsx src/components/DashboardAdsPeriodsOverview.test.jsx src/components/DashboardAdsFinalProfit.test.jsx
```

## Shared release coordination

The preceding PR #1968 verification used this owned lease, retained here for traceability:
- Owner: `bulk-permissions-1968-20261118`
- Source: `2707f66fd0fa701cc904496c176304b1a373c8c3`
- Deployment: `19527acf5ad97976e1d5f773a95bf9ff6dc4cca8`
- Runtime: `rg5-4ab5ccc21f961f4ab6a50d9d9f60f966706f8565dd2d2fd529c418b851bf68dc`

The manager shows its 100th publication/version `8a0d442`, finished Nov18 21:06 Asia/Riyadh. Attempt1 timed out fetching one asset, with no reported identity/artifact mismatch. A bounded diagnostic succeeded, then the owner restarted the unchanged official verifier without reprepare, abort or republish. PR #1968 is the authoritative ongoing release record.

The official filtered status call returned **active:false**, observed around 2026-11-18 18:28 UTC. This is a point-in-time observation; recheck it before any live staging because PR #1991 is also waiting to publish. Do not clear, reuse or alter another lease or interrupt another rehearsal. After fresh inactive-guard and idle-host checks, this task fast-forwarded `/app` to exact M at 2026-11-18 18:38:46 UTC. The receipt confirms tree `b8bfb1aece57f3f89282216e4171398bd8854c9d`, a clean tracked worktree, and all 120 unrelated untracked paths preserved (inventory SHA-256 `2a0200fc4ebb665ebc1fec275ef8db46870809282ef7fcc40a931e631f74eb04`). No lease or publication has occurred yet. Preserve unrelated workspace files and never use `git reset --hard`.

## Latest release milestone

- `/app` staging succeeded at M `8761f7b2f80faec29638e1438b8fedefcec64a28`; receipt `/tmp/snap-unassigned-1993-release/staging.json` records 18:38:46 UTC. All unrelated work is preserved.
- Post-merge M checks: **11/11 success**, none pending or failing.
- Actual-host adapter rehearsal, owned lease, UI publication and official verification remain pending. The live site is still the previous published version.
- Next: fresh official guard status must be inactive, then run the configured adapter with `REACT_APP_BACKEND_URL=https://mezansalla.com yarn --cwd frontend build` and retain sanitized output. Only after it matches the frozen intent may this task prepare an owned v5 lease.

## Actual-host rehearsal attempt 1

Configured command started at 18:42:52 UTC and exited 1 at 18:42:54 UTC before a governed build: `EMERGENT_DEPLOYMENT_REFUSED: ... Backend runtime source contains prepackaged bytecode: __pycache__`. Tracked source and all 120 unrelated untracked paths remain unchanged. No lease/publication occurred. Read-only inspection found exactly `backend/__pycache__`, 2 generated ignored bytecode files, 120563 bytes. The preceding plain-Python guard imports can generate those caches. Next safe action: require fresh inactive status via Python `-B`, preserve those verified ignored generated caches in the task-owned `/tmp` quarantine, and retry the unchanged configured adapter with bytecode generation disabled. Preserve attempt-1 logs and do not change reviewed source or validation rules.

## Actual-host rehearsal attempt 2 — passed

- 2026-11-18 18:44:18–18:45:23 UTC; configured `REACT_APP_BACKEND_URL=https://mezansalla.com yarn --cwd frontend build`; exit **0**.
- Only the 2 verified ignored generated bytecode files were preserved outside `/app` at `/tmp/snap-unassigned-1993-release/generated-bytecode-quarantine`; no reviewed source/validator change. Python bytecode generation disabled for the retry and subsequent guard commands.
- Tracked worktree clean and all 120 unrelated untracked paths preserved.
- `built:true`, protocol 5, exact A2/runtime/artifact listed above.
- Backend package: 1242 files; isolated identity and health verified without Git or sibling frontend; package tree `9d196f24fffcb3aadffacbb6f4edcaa4db900a8097866f97cecfd5eb439849d2`; identity 446702 bytes, SHA-256 `1217b0bcdc17a13d37dfa40fef586f62573638ad8a15445b517c23b1c7dbf624`.
- Frontend package: 1540 files; isolated runtime artifact verified; package tree `d14f725c5f4954c0a59bc726369aaf278af2de30002f935375731713716e90ee`. Build-meta 494297 bytes, SHA-256 `2c9dd4e546c6ead76090b0afe1d74b8d190aeff51cba1437597616797980f306`, application/json contract.
- Sanitized adapter log SHA-256 `3b615204d31d168901f78697b5288611ec77ac64cfc43bac6fe58280e32efabe`; receipt/proof retained in `/tmp/snap-unassigned-1993-release/adapter-attempt2-result.json` and `adapter-verified-proof.json`. This durable checkpoint records the material evidence.
- No lease or publication yet; next `python -B scripts/production_release_guard.py prepare --actor snap-unassigned-1993-20261118`, only after another fresh inactive status. Then `prepublish` immediately before one ordinary authorized publication.

## Owned release lease

`python -B scripts/production_release_guard.py prepare --actor snap-unassigned-1993-20261118` passed, exit 0, after fresh inactive status. Prepared at **2026-11-18T18:46:36.083348+00:00**. The lease binds exact source A2, deployment M, protocol 5 and runtime listed above. Both reproducibility passes have artifact tree `1f7da282b51120ea6954d2a799092e07464dd6576e0e9abb994107ec1b3e19be` and build-meta SHA `2c9dd4e546c6ead76090b0afe1d74b8d190aeff51cba1437597616797980f306`, 494297 bytes. Prepare receipt SHA-256 `6e92a4b8784b070f965e1d446d61b6e8953c8109ea6c958cdf8eb03dcfe1cb68`.

**This task owns the active lease. Do not prepare again or change source.** Prepublish and UI publication are next; no publication yet. Existing manager version remains `8a0d442` (100th publication, Nov18 21:06 Riyadh).

## Publication started

Prepublish passed at **2026-11-18T18:47:07.944577+00:00**, exit 0, `ready_to_publish:true`, exact A2/M/runtime/protocol5. The existing user publish approval remains applicable. The Emergent UI showed ordinary **Free republishes**, and this task clicked **Publish changes once**. The next observed state is **Publishing…**; no newer success/version is confirmed yet. No paid chat, purchase, Preview, provider sync or financial write was used. Do not submit another publication.

Next: wait for explicit newer success/version, then run the unchanged official `python -B scripts/production_release_guard.py verify --url https://mezansalla.com` on this owned lease. Save original stdout/stderr/exit status and start/end timestamps. Require `verified:true`, `checks:3`, correct identities and separate final `status active:false`; then verify numeric unknown-row contribution and unchanged operating/final footer in the live dashboard.

## New deployment observed

At approximately 18:57:49 UTC, Manage showed new published version **`ed5f35c`** and the green **Deployment finished Nov 18, 09:56 PM** entry. This is the current UI's explicit successful completion label. The publish control returned to Republish. View Logs exposes recent runtime logs (100 entries), not a Cloud Build transcript; the observed health requests were 200 and UI reported 0 error/0 warning entries. Do not infer build argv or snapshot order from runtime logs or retain unrelated request/customer data.

Next immediate command: unchanged official `python -B scripts/production_release_guard.py verify --url https://mezansalla.com` for the original owned lease, recording full output, exit status, all three checks and final inactive status. Do not republish or reprepare. Dashboard confirmation remains pending.

## Exact next safe actions

1. Read AGENTS.md and current PR #1993, fetch and verify exact A2 and current production base J2.
2. Verify exact B2 above and its local intent-only acceptance; never reconstruct it from the superseded pair.
3. Collect exact-B2 checks and both host adapter rehearsals. If production advanced again, preserve the frozen pair and produce a new source/intent pair on the new base.
4. Merge is complete. Recheck production M above and its tree/intent-only contract before any publication; do not merge or rebuild a superseded pair.
5. Before any change or rehearsal in `/app`, require official guard `active:false`, confirm the current production merge/tree and preserve unrelated work. Never interrupt the preceding release owner.
6. Run the configured governed adapter, prepare an owned lease only after rehearsal, then prepublish and publish through the ordinary Emergent management controls. Do not use paid chat or Preview.
7. Require a newer deployment success, official three-round verification and matching lease closure; inspect the live unknown contribution and unchanged operating/final footer.
8. Update this document and current PR with full identities, CI, merge, guard and publication evidence.

This is a documentation-only continuation branch, not a deployment candidate. Issue #1006 is full; use this record and the current PR without overwriting that issue.
