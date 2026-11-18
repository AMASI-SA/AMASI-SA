# SNAP-UNASSIGNED-PROFIT-20261118

## Current state

**Published; official verification running; live UI confirmation passed.**

- Repository: `AMASI-SA/AMASI-SA`.
- Production branch: `hotfix/prod-snap-meta-final`.
- Source branch: `codex/snap-unassigned-profit-20261118-r2`.
- [PR #1993](https://github.com/AMASI-SA/AMASI-SA/pull/1993) is merged as M `8761f7b2f80faec29638e1438b8fedefcec64a28`.
- Publication: **`ed5f35c`**, green **Deployment finished Nov18 21:56 Asia/Riyadh**, newer than `8a0d442`. One ordinary free publication was submitted under the user's existing explicit approval.
- Owned v5 lease: `snap-unassigned-1993-20261118`, prepared 2026-11-18T18:46:36.083348+00:00.
- Official verify attempt1 started **2026-11-18T18:59:10.955811+00:00** and is still running. No final verification result or lease closure has been asserted.
- **Production changed: yes**, GitHub source M, staged host M and new published version above. Integrity verification remains pending.
- This documentation-only handoff branch is not a deployment candidate. Its exact commits are in Git history; the prior full chronological checkpoint is [4ad1b107c88d075235a41c395f7c8eb50381e36a](https://github.com/AMASI-SA/AMASI-SA/blob/4ad1b107c88d075235a41c395f7c8eb50381e36a/docs/operations/snap-unassigned-profit-20261118/STATUS.md). Issue #1006 is full, so this record and PR #1993 provide continuity.

## User requirement and behavior

The latest user instruction retains the operating-expense/final-net footer and asks why **سناب غير محدد** still displays **بانتظار توزيع الصرف**.

The row now shows **مساهمة الطلبات** from that cohort's own verified sales minus product, shipping and customer-payment costs. It supports positive, zero and negative values in daily and half-day views. Missing or inconsistent evidence displays **بيانات الربح غير مكتملة**.

Advertising spend and its bank fees stay on actual Snapchat accounts and are deducted once. The unknown cohort remains unassigned: normal full netProfit is null and complete is false; advertising spend, associated bank fee, CPA and ROAS remain unavailable. The contribution is not calculated as total-minus-visible-rows and is never added again to the canonical total. Operating expenses and final-net arithmetic are unchanged.

The helper requires valid cohort/source identity, period/currency/count consistency, catalog coverage, the existing executive-order-cost contract, complete component evidence, finite numbers and rounded component/known-total consistency. No backend edit, new API/provider call, cost-policy change or financial-data write is introduced.

### Changed files

Source:
- `frontend/src/lib/dashboardAdsAccountResults.js`
- `frontend/src/components/DashboardAdsProfitValue.jsx`
- `frontend/src/components/DashboardAdsSpendCard.jsx`
- `frontend/src/components/DashboardAdsAccountRow.jsx`
- `frontend/src/components/DashboardAdsPeriodsOverview.jsx`

Tests:
- `frontend/src/lib/dashboardAdsAccountResults.test.js`
- `frontend/src/components/DashboardAdsSpendCard.test.jsx`
- `frontend/src/components/DashboardAdsAccountRow.test.jsx`
- `frontend/src/components/DashboardAdsPeriodsOverview.test.jsx`
- `frontend/src/components/DashboardAdsFinalProfit.test.jsx`

B2 changes only `release/release-intent-v5.json`. All 10 dashboard blobs remain byte-identical to the first independently reviewed implementation.

## Immutable release identities

| Item | Exact identity |
| --- | --- |
| Production base J2 (PR1991) | `ce94ce01b0e631a6373ee3524119f26959bf89a4` |
| Source A2 | `84ae882f78ae954449cc4f3380b17a8ff808cb3a` |
| Source tree | `da532bd626f20662fa7840963d4f183f7f3c2918` |
| Intent-only B2 | `7268ab80e0781c288943f290ea020b1b27f1e57d` |
| Deployment merge M | `8761f7b2f80faec29638e1438b8fedefcec64a28` |
| B2 / M tree | `b8bfb1aece57f3f89282216e4171398bd8854c9d` |
| Runtime | `rg5-c8f8108755b4a5d612b50e91902fbe83b014b80ce076b3372408f1bf41fc286b` |
| Frontend artifact tree | `1f7da282b51120ea6954d2a799092e07464dd6576e0e9abb994107ec1b3e19be` |
| build-meta SHA-256 | `2c9dd4e546c6ead76090b0afe1d74b8d190aeff51cba1437597616797980f306` |
| Intent SHA-256 | `6f1297fd5a11d96c2a2fee094f542b4f51f121a4b681d70ec3f3d50d07ec1165` |

M's tree equals B2. A2→B2 and A2→M content diffs contain exactly the intent file. The source/intent pair must not be rebased, squashed, edited or reused for a different tree.

PR1991's 17 changed/added paths are preserved byte-for-byte; there is no overlapping dashboard path. Backend and AdvancedDashboard source are unchanged versus J2. Superseded [PR1992](https://github.com/AMASI-SA/AMASI-SA/pull/1992) was closed without merge when production advanced; its frozen pair is preserved and must not be deployed.

## Code verification

- The regression reproduced the original missing-value defect before implementation.
- Five focused suites: **328 tests passed, exit0**, before and after integration on J2. Exact-A2 rerun: 6.54s.
- Independent financial-contract and current-base integration review found no blocker.
- All 10 reviewed dashboard blobs and all 17 newer-production paths preserved; `git diff --check` passed.
- A2 release readiness [44368961758](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44368961758) and dashboard [44368961767](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44368961767) passed.
- Candidate artifact14457802088; ZIP SHA-256 `5bdc2a62ab3629f519c67ab3e398c4b16590b5ed17254be5e93b34364927a65a`. Candidate validator verified exact A2/J2, archive digest and full manifests, exit0.
- Exact-B2 readiness [44369862714](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44369862714), dashboard acceptance, security/build checks and host20.20.2/24.19.0 clean-clone adapter rehearsals passed before merge.
- Latest B2 check read: 44 checks,33success,10skipped,1neutral,none pending/failing. The neutral CodeQL aggregate reported a missing comparison configuration; its jobs passed. Postmerge M: **11/11 success**, including both CodeQL languages/security/build/host rehearsals, none pending/failing.
- Exact-B2 local reviewed-intent acceptance passed, clean worktree, intent-only relation.

Focused command from `frontend`:

```bash
CI=true node node_modules/react-scripts/bin/react-scripts.js test --watchAll=false --runInBand --runTestsByPath src/lib/dashboardAdsAccountResults.test.js src/components/DashboardAdsSpendCard.test.jsx src/components/DashboardAdsAccountRow.test.jsx src/components/DashboardAdsPeriodsOverview.test.jsx src/components/DashboardAdsFinalProfit.test.jsx
```

## Host staging and governed rehearsal

The preceding PR1968 owned lease completed and official status was inactive before this task staged anything. No other owner's lease was cleared or reused.

- Fresh inactive-guard/idle-host checks preceded an ordinary fast-forward to exact M at **18:38:46 UTC**.
- Tracked worktree clean; all120 unrelated untracked paths preserved. Inventory SHA-256 `2a0200fc4ebb665ebc1fec275ef8db46870809282ef7fcc40a931e631f74eb04`. No reset-hard used.
- Attempt1 of the configured adapter (18:42:52–18:42:54UTC) exited1 before build because the source validator rejected `backend/__pycache__`. Inspection identified2 ignored generated bytecode files,120563bytes. Plain-Python guard imports can generate these.
- The2 verified ignored bytecode files were preserved outside the package at `/tmp/snap-unassigned-1993-release/generated-bytecode-quarantine`. Reviewed source and validation rules were not changed. Subsequent guard calls use Python `-B`; adapter retry used `PYTHONDONTWRITEBYTECODE=1`.
- Attempt2 **passed**,18:44:18–18:45:23UTC,exit0:
  `REACT_APP_BACKEND_URL=https://mezansalla.com yarn --cwd frontend build`.
- Host evidence: Node24.19.0,Yarn1.22.22,Python3.11.18,aarch64; governed toolchain Node22.23.2/Yarn1.22.22. Both deterministic passes matched the exact reviewed artifact/build-meta above.
- Backend package1242files; isolated identity and health verified without Git/sibling frontend. Package tree `9d196f24fffcb3aadffacbb6f4edcaa4db900a8097866f97cecfd5eb439849d2`; identity446702bytes,SHA `1217b0bcdc17a13d37dfa40fef586f62573638ad8a15445b517c23b1c7dbf624`.
- Frontend package1540files; isolated runtime artifact verified. Package tree `d14f725c5f4954c0a59bc726369aaf278af2de30002f935375731713716e90ee`; build-meta494297bytes,exact SHA above,application/json contract.
- Successful sanitized adapter log SHA `3b615204d31d168901f78697b5288611ec77ac64cfc43bac6fe58280e32efabe`.
- Attempt2 ended tracked-clean with unrelated inventory preserved.

## Prepare, publication and production verification

- `python -B scripts/production_release_guard.py prepare --actor snap-unassigned-1993-20261118` passed,exit0,**18:46:36.083348UTC**, after fresh inactive status.
- Prepare receipt SHA `6e92a4b8784b070f965e1d446d61b6e8953c8109ea6c958cdf8eb03dcfe1cb68`.
- `prepublish` passed at **18:47:07.944577UTC**,exit0,`ready_to_publish:true`, exact A2/M/runtime/protocol5.
- One ordinary **Free republishes → Publish changes** action. No paid chat/purchase/Preview click/provider sync/financial write.
- Manage later showed new version **ed5f35c** and green **Deployment finished Nov18,09:56PM Riyadh**. View Logs exposes runtime logs, not a full Cloud Build transcript. Observed health requests returned200; UI showed0errors/0warnings. Historic outer build argv/snapshot order remains unobserved and is not inferred.
- Official unchanged verify attempt1 started **18:59:10.955811UTC**:

```bash
python -B scripts/production_release_guard.py verify --url https://mezansalla.com
```

**Running; no final result yet.** It holds the operation mutex. Do not start another verify or a blocking status call while it runs, and do not reprepare/rebuild/republish.

Receipt directory: `/tmp/snap-unassigned-1993-release`.
- Start: `verify-attempt1-start.json`.
- Raw outputs: `verify-attempt1.stdout.json`, `verify-attempt1.stderr`.
- Completion metadata: `verify-attempt1-run.json` (created on process completion).
- On success: `verify-attempt1-summary.json`, `post-verify-status.json`.

The official success output must have `verified:true`,`checks:3`,exact identities/artifact, followed by official `status active:false`. The guard enforces verified health identity,critical hashes,frontend proof,and exact canonical/cache-busted JSON/asset bytes in every round. It serializes an aggregate3check success and first-round request/header arrays; do not claim separately emitted per-round arrays.

## Live UI confirmation

The new production dashboard was reloaded after publication:
- **سناب غير محدد** now shows numeric **مساهمة الطلبات**.
- Its own cost breakdown independently explains the contribution.
- Empty morning cohort shows0.00; evening/day contribution agree.
- Advertising spend and bank-allocation fields remain unavailable for the unassigned cohort.
- Operating-expense/final-net footer remains visible; final net matches the executive summary in the same rendered data snapshot.
- Only read-only navigation and row expansion; no provider-refresh action.
- Customer-free table screenshot retained privately in the conversation workspace; no customer details or live financial amounts are committed here.

## Exact next safe action

Observe the running verify attempt's task-owned result files from the idle terminal. If it fails, preserve the failed receipt and inspect the actual cause before any bounded diagnostic or unchanged retry. Never weaken the verifier or create a new lease to sidestep a failure.

If it succeeds, record full identity/artifact/checks3/start/end/output-hash evidence and the separate inactive status, update this document and PR1993 to completed, and deliver the verified UI result. Do not repeat already completed merge/staging/rehearsal/publication.
