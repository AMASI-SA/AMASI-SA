# SNAP-UNASSIGNED-PROFIT-20261118 — current production integration

## Current state

Current PR: [#1993](https://github.com/AMASI-SA/AMASI-SA/pull/1993), branch `codex/snap-unassigned-profit-20261118-r2`.

PR #1992 was closed without merging because production advanced during the preceding release's verification. Its frozen source/intent pair remains preserved and must not be deployed. The previous complete record is [07322bbdb2c8c58d8f276b3176cdd942faba1df6](https://github.com/AMASI-SA/AMASI-SA/blob/07322bbdb2c8c58d8f276b3176cdd942faba1df6/docs/operations/snap-unassigned-profit-20261118/STATUS.md).

**Production changed by this task: no.** No live source changes, lease creation or publication by this task.

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
- Required source-A2 security/build checks passed before B2 was frozen. Exact-B2 CI is running in release readiness [44369862714](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44369862714) and the other repository workflows; both host adapter rehearsals have started.

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

The official filtered status call returned **active:false**, observed around 2026-11-18 18:28 UTC. This is a point-in-time observation; recheck it before any live staging because PR #1991 is also waiting to publish. Do not clear, reuse or alter another lease or interrupt another rehearsal. No `/app` files were changed by this task. Preserve unrelated workspace files and never use `git reset --hard`.

## Exact next safe actions

1. Read AGENTS.md and current PR #1993, fetch and verify exact A2 and current production base J2.
2. Verify exact B2 above and its local intent-only acceptance; never reconstruct it from the superseded pair.
3. Collect exact-B2 checks and both host adapter rehearsals. If production advanced again, preserve the frozen pair and produce a new source/intent pair on the new base.
4. Under existing publication authorization, merge only the reviewed exact B2 on the verified current base. A GitHub merge is not publication; keep shared-host staging separate.
5. Before any change or rehearsal in `/app`, require official guard `active:false`, confirm the current production merge/tree and preserve unrelated work. Never interrupt the preceding release owner.
6. Run the configured governed adapter, prepare an owned lease only after rehearsal, then prepublish and publish through the ordinary Emergent management controls. Do not use paid chat or Preview.
7. Require a newer deployment success, official three-round verification and matching lease closure; inspect the live unknown contribution and unchanged operating/final footer.
8. Update this document and current PR with full identities, CI, merge, guard and publication evidence.

This is a documentation-only continuation branch, not a deployment candidate. Issue #1006 is full; use this record and the current PR without overwriting that issue.
