# Shared marketing AI readiness — 2026-10-16

## Contract

Connect Snapchat's unified reporting evidence to the existing Campaign AI path;
future advertising providers use the same registered readers, candidate conversion,
decision gates and execution proof. Keep provider-specific write adapters, owner
approval, budgets/limits, snapshot identity and live provider-state checks.
The optional first-party Salla script is explicitly not a prerequisite.

Task branch: `codex/unified-marketing-ai-readiness-20261016`.
Repository: `AMASI-SA/AMASI-SA`. Base: `a0f03a73ec364481e1995d8d35987fd99a85ac8e`.
Production changed: **no**. No provider advertising writes performed.

## Implemented / review in progress

- Closed-period hierarchy reports prove completeness from their producing sync
  runs, rather than the latest unrelated run. Regression reproduced on all three
  hierarchy levels before correction; absent/cross-account/incomplete evidence
  remains blocked.
- Explicit provider registry and shared candidate conversion reuse the canonical
  seven decision gates. Default analysis ends on the last account-local closed day.
- Unified-source execution quality uses the same reporting contract instead of
  incompatible legacy sync records. Existing approval and provider write adapters
  are retained. Source/parent commercial evidence is bound before model generation
  and rechecked on initial quality capture and approval execution. Routine sync
  timestamps do not change this binding; independent freshness checks remain.
- Separate UI follow-up validates provider/account/period, exposes hierarchy blockers,
  and links to the existing recommendation/approval page. Removed the misleading
  static Snapchat "not connected" label. Passing diagnostic gates does not itself
  enable execution.

The settings-only CI scope guard rejected combining the UI with backend changes.
UI changes are preserved in a separate dependent branch/PR; the guard is retained.
This PR contains the shared backend path and its reporting CI coverage only.

## Verification at initial checkpoint

- Combined focused backend run: **152 passed**, exit 0.
- Frontend readiness/page/order panel run: **29 passed**, exit 0.
- Initial wider backend run: **268 passed, 1 skipped, 1 failed**. Failure was an old test
  expecting a TOTAL collection name to override incomplete coverage; the new
  period proof intentionally removes that bypass. Test now requires explicit
  complete proof. Fresh combined rerun: **274 passed, 1 skipped**, exit 0.
- Review found routine sync timestamps invalidating unchanged approvals and a
  missing generation-time commercial evidence binding. Both corrected and covered
  by regressions. This source checkpoint has no reviewed release intent yet.

Local backend runtime: `C:/Users/amasi/build37-test-venv/Scripts/python.exe`.
Use `-m pytest -p snapchat_v2_test_bootstrap` from `backend`.
Frontend uses matching-lockfile dependencies via a local junction; CI installs
fresh frozen-lockfile dependencies. Junction is untracked and is not release input.

## Outstanding / next safe steps

1. Complete semantic evidence binding review and rerun the combined suites.
2. Review complete diff, run CI and preserve exact checkpoint SHA in Issue #1006.
3. Follow applicable production protocol v5 for any separately authorized release;
   no lease, merge, deployment or live AI execution has been performed by this task.
4. Validate live account readiness and recommendation generation after deployment;
   mock tests alone cannot prove production coverage or provider execution readiness.

Unrelated main-worktree Qoyod changes and operational selection PR #1376 are
preserved. Existing production lease status has not been inspected in this task.

## Production verification and follow-up response contract repair

Deployment 58053cad completed Oct16 16:57 Saudi (green Deployment finished,
Live in updated Emergent UI). Release Guard verify exited0 with verified=true
for rg5-763513961fc3a4c3f2fba1d42935246388d0777a7268478c10bdc0808278bbf0,
source290adba4793c8474f305a0edddf867953a1693b5, deployment4561ef771676142d853104b433fc9dd2897dab46.
Three internal probes and frontend artifact checks passed; status activefalse.
Production changed yes. No advertising writes performed.
Live unified account contract now shows ready for closed day2026-10-15.
Detailed UI check exposed a response contract regression: UI checks provider at
response root, but phase5 only returned it in lineage. New branch
codex/readiness-provider-response-20261016 adds root provider from evidence.
Regression test failed twice with KeyError before fix; 52 focused tests passed
afterward (phase5 plus unified execution evidence). Future provider is covered.
No readiness gates weakened. Follow-up needs PR, new source/intent build pair,
CI, deployment and live verification under existing authorization.

## Follow-up release prepared and publishing

PR1380 merged after all checks passed, merge8c9ef8cd96bcef0f3b50c7c7928dad99698ba279.
SourceA69df38d25a84f7c671e821f898306e275f8cc3b6 and intentB624bdb695d7d26946b9a2f6849ef76c78623d825.
Runtime rg5-c07aa97b4a26ae633bd697c8c4f1a8886ccc3ab1ccce6bb48d6e6d0fa808d0b2;
intent SHA2564c923178366f136d78b6aeaded14ab9586be4b9ad180394d8b23c140504f0306.
Cloud and CI builds match, candidate workflow38912791031 and PR release workflow38913100896 passed.
/app was inactive, clean tracked, fast-forwarded from4561 to8c9ef; untracked preserved.
Governed build and prepare/prepublish succeeded (gate exit0), actor
unified-marketing-ai-provider-response-20261016. Publish confirmation clicked;
prior visible version58053ca. New deployment not yet verified; lease active.
A fresh AI cycle generated two Meta recommendations at17:08 Saudi after first
deployment; no approval/execution performed. Snapchat-specific live readiness
and source evidence still require final validation after this second deploy.
Next: wait for completed/green live deployment; guard verify exact runtime,
confirm inactive lease, recheck all7 Snapchat gates and fresh recommendation scope.

## Verified deployed result and remaining live data blocker

Second deploy completed green in Emergent at17:25 Saudi, live version10c6ee4
(after58053ca). Production Guard verify exited0 and returned verified=true for
source69df38d25a84f7c671e821f898306e275f8cc3b6, deployment8c9ef8cd96bcef0f3b50c7c7928dad99698ba279,
runtime rg5-c07aa97b4a26ae633bd697c8c4f1a8886ccc3ab1ccce6bb48d6e6d0fa808d0b2.
Boot observed2026-10-16T14:24:24.834343+00:00. Three internal probes and full
frontend checks passed. Guard status activefalse. UI now says green Deployment
finished rather than literal Deployment Succeeded; record exact observed label.
Logs: /root/readiness-provider-verify.log. Production changed yes.

Live detailed readiness now renders correctly for closed day2026-10-15:
5/7 gates passed (contract, closed day, reconciliation, freshness, attribution).
Coverage false ONLY at campaign level; account, ad_group and ad complete.
Financial coverage false; unmatched orders0 and missing-cost orders0.
Code explains financial dependency: _derive_account_profitability in
backend/unified_marketing/readers/snapchat_v2_decision_evidence.py refuses account
profit aggregation when campaign sync/coverage is partial. This does not prove
which source-run field causes campaign incompleteness; raw production source-run
and fact diagnostics remain required. Do not weaken guards or claim100% ready.
The account-only readiness card says ready but is a narrower check.

Selected15Oct in UI, applied and confirmed report dates. Campaign rows and totals
remain partial. Clicked manual V2 report sync once for that closed day (report data
refresh only, no campaign/budget changes); busy state ended. Result toast not
captured, so do not claim sync succeeded. Subsequent detailed readiness unchanged.
A fresh17:08 recommendation snapshot was generated after first deployment and
contains2Meta recommendations; proves model cycle, not Snapchat eligibility.
No recommendation approved/executed and no advertising/financial transaction writes.

Outstanding: diagnose production campaign source facts/run proof for15Oct and
AI3day closed window, repair source or sync issue with evidence, then recheck7/7
and Snapchat model input/recommendation path. Future adapters share registry/core;
actual future providers are not yet certified. Tracking snippet remains deferred.
Current source fixes are published and verified; full business readiness remains
incomplete. Preserve all unrelated work. No active lease. Next safe action is
read-only production campaign-run/fact diagnostics, not another blind deploy.
