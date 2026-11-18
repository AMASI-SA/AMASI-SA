# SNAP-UNASSIGNED-PROFIT-20261118

## State

Implementation and release preparation are complete. PR [#1992](https://github.com/AMASI-SA/AMASI-SA/pull/1992) is open and ready for review. All exact-head checks are complete: **30 successful, 6 intentionally skipped, no failures**.

**Production changed by this task: no.** No production merge, live source changes, lease creation, or publication has been performed for this task.

The shared Emergent workspace is still completing verification for the preceding PR #1968 release. Do not alter or clear that release's lease. The official status command must report `active: false` before work in `/app` or a release rehearsal.

## User scope and resulting behavior

The user explicitly retained the operating-expense and final-net footer and asked to fix the unassigned Snapchat row that still showed “بانتظار توزيع الصرف”.

The row now displays **مساهمة الطلبات** from its own verified sales minus product, shipping and customer-payment costs. It supports positive, zero and negative amounts in the daily table, row details and half-day overview. Missing or inconsistent evidence displays “بيانات الربح غير مكتملة”.

Advertising spend and advertising bank fees remain on the actual Snapchat accounts and are deducted once. The unassigned cohort remains unassigned; its full net-profit, spend, bank-fee, CPA and ROAS availability are not invented. The contribution is already included in the canonical aggregate and is not added again. Operating expenses and the authoritative final-net footer are unchanged.

No new backend/provider reads, syncs, financial writes, or operating-expense logic were introduced.

## Repository and immutable release contract

- Repository: `AMASI-SA/AMASI-SA`
- Production branch: `hotfix/prod-snap-meta-final`
- Task branch: `codex/snap-unassigned-profit-20261118`
- Production base J: `19527acf5ad97976e1d5f773a95bf9ff6dc4cca8`
- Source A: `ca0e17ec90b9c22cf435b40b77d40bad96f69f2d`
- Source A tree: `3053de536853f8de3bfea4a95897da829acc4d4d`
- Intent-only B: `9b68eac44d21f00aaaefd038d44fe6bb646a35fa`
- Deployment B tree: `1d6909e4a0c2cb4cea7f116290433e2b435ef736`
- Runtime release ID: `rg5-f199658227e21a372caefa5ed6fa21fabc09718c5d07bece9de975df62590feb`
- Frontend artifact tree: `2dfa5fcd78384ca4e05e3fd4a618909c9b97469f70646a4f7c5c8b7fecce63ef`
- Build-meta SHA-256: `da84ccdf14ee9e9887364a595659d1fafd4816e795e5b4c3a42f2a99b2bf3385`
- Intent SHA-256: `d7659525bb304414ded88a8b274da2efc635b0035807386437de50359cb0c14b`

Source A includes the concurrent reviewed PR #1968 as its base without changing that work. A to B changes exactly `release/release-intent-v5.json`. Do not squash, rebase, or add source changes after A. This handoff lives on a separate documentation branch and is not a deployment candidate.

## Changed source and test files

- `frontend/src/components/DashboardAdsAccountRow.jsx`
- `frontend/src/components/DashboardAdsAccountRow.test.jsx`
- `frontend/src/components/DashboardAdsFinalProfit.test.jsx`
- `frontend/src/components/DashboardAdsPeriodsOverview.jsx`
- `frontend/src/components/DashboardAdsPeriodsOverview.test.jsx`
- `frontend/src/components/DashboardAdsProfitValue.jsx`
- `frontend/src/components/DashboardAdsSpendCard.jsx`
- `frontend/src/components/DashboardAdsSpendCard.test.jsx`
- `frontend/src/lib/dashboardAdsAccountResults.js`
- `frontend/src/lib/dashboardAdsAccountResults.test.js`

## Verification evidence

- Regression reproduced the original missing contribution before implementation.
- Focused integrated frontend execution: **5 suites, 328 tests passed, exit 0**.
- Independent complete-diff review found no blockers.
- `git diff --check`: exit 0.
- Coverage includes signed/zero contribution, cohort identity and count, currency/time/period boundaries, complete cost evidence, malformed values, unchanged canonical totals and bank fees, empty elapsed periods, no input mutation, and independent cohort fee rounding.
- Source readiness [run 44364846635](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44364846635) passed its governed two-build artifact validation.
- Candidate intent artifact: `14456765383`; archive SHA-256 `f625a9375ea36bc0cf878ea8a19c0f96b132eb9b7c9db8f94a3daf2d3a1bb8be`. Archive digest, source/base, schema and complete manifests were verified using `load_candidate_release_intent` at A.
- Reviewed-intent `load_release_intent` validation at B passed, exit 0. The local worktree was clean; the A-to-B diff was intent-only.
- Exact-B release readiness [run 44366012474](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44366012474) passed, including both Node 20.20.2 and 24.19.0 host clean-clone adapter rehearsals.
- Exact-B focused dashboard [run 44366012284](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44366012284) passed.
- Required checks, CodeQL, operational backend/frontend/browser, and remaining repository checks passed. At 2026-11-18 18:15:41 UTC: 36 checks, 30 success and 6 skipped.

Focused command, from `frontend`:

```bash
CI=true node node_modules/react-scripts/bin/react-scripts.js test --watchAll=false --runInBand --runTestsByPath src/lib/dashboardAdsAccountResults.test.js src/components/DashboardAdsSpendCard.test.jsx src/components/DashboardAdsAccountRow.test.jsx src/components/DashboardAdsPeriodsOverview.test.jsx src/components/DashboardAdsFinalProfit.test.jsx
```

## Shared release coordination

The Emergent manager visibly shows the previous publication as the 100th publish, platform version `8a0d442`, finished Nov 18 at 21:06 Asia/Riyadh. This is the PR #1968 release, not this task.

The official `python /app/scripts/production_release_guard.py status` waited behind an existing verification process and then returned the prior lease for source `2707f66fd0fa701cc904496c176304b1a373c8c3`, runtime `rg5-4ab5ccc21f961f4ab6a50d9d9f60f966706f8565dd2d2fd529c418b851bf68dc`. A subsequent filtered official status call is waiting behind verification again. No lease was changed.

The live workspace contains unrelated pending/untracked work. Preserve it. Do not use `git reset --hard`, remove another task's files, clear another owner’s lease, or use paid Emergent chat/Preview.

## Exact next safe actions

1. Read `AGENTS.md`, this status and PR #1992. Fetch the task branch and verify exact B above; recheck the current production ref and exact-B checks.
2. Wait for the preceding release owner to finish. Run the official guard status and proceed only on `active: false`. Do not infer availability from the manager's publication badge.
3. Before merging, verify production is still J. If production advanced, preserve this checkpoint and create a newly reviewed source/intent pair on the new base rather than editing the frozen pair.
4. Under the user's publication authorization, merge PR #1992 using a merge commit with expected head B. Verify the production merge tree equals B and that A-to-merge changes only the intent.
5. Recheck the guard and preserve unrelated `/app` work. Fetch and fast-forward only to the verified production merge; run the configured governed package adapter rehearsal, then `prepare` and immediate `prepublish`.
6. Publish through the Emergent management UI. Require a newer explicit deployment success and official three-round `verify --url https://mezansalla.com`, followed by `active: false`.
7. Check the live dashboard contribution and retained operating/final footer, then update this handoff and the PR body with the exact production evidence.

Issue #1006 has reached its comment limit; this status document and the PR body are the durable continuation record. Do not overwrite that issue or retry posting comments there.
