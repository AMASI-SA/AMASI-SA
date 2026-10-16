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

## 2026-10-16 stable closed-period evidence follow-up (WIP)

Latest verified deployment remains PR1380, merge8c9ef8cd96bcef0f3b50c7c7928dad99698ba279,
source69df38d25a84f7c671e821f898306e275f8cc3b6, runtime
rg5-c07aa97b4a26ae633bd697c8c4f1a8886ccc3ab1ccce6bb48d6e6d0fa808d0b2.
The prior follow-up checkpoint is22c98a460d4c1f83fa44fea8293cfba35e5c410e.
No Production changes in this follow-up yet.

Production Mongo viewer (PROD) now available after user opened it. Read-only
inspection established that closed-day campaign facts are overwritten by the
currently running refresh before identity/child processing completes. The
previous run was complete; the new run had financial/campaign complete but
identity/ad/ad_squad pending. Thus total_period_complete correctly refuses the
new run, but the last good evidence was destroyed by mutable upserts. Older
retained campaign rows examined have complete source runs; they are not the
observed blocker. No credentials or customer data retained.

Added regression tests/test_snapchat_reporting_v2_refresh_visibility.py on
codex/snapchat-stable-period-evidence-20261016. Command from backend:
python -m pytest -q -p snapchat_v2_test_bootstrap tests/test_snapchat_reporting_v2_refresh_visibility.py
Exit1: completed proof passes before refresh write and fails afterward,
exactly reproducing the Production mechanism. This is an intentionally failing
WIP regression, not a deployable change. Next: publish bounded per-day committed
TOTAL fact snapshots only after a successful run; all intelligence readers use
that same complete snapshot while subsequent refreshes run. Retain all source
proof, ownership, period, freshness, financial and approval checks. Add failure,
first-run and successful-refresh tests; then follow protocolv5 for approved release.

## Committed account-day snapshot implementation

A successful sync now publishes each account-day hierarchy atomically into
mezan_snapchat_committed_totals_v1 after complete_sync_run. All three hierarchy
levels switch together; running/partial/failed runs cannot publish. The bounded
snapshot contains only rows produced by that exact successful run, so older
rows absent from a replacement provider report are not mixed into that snapshot.
Documents are limited to12MiB, reads/writes to100000 rows; publication validates
source scope, coverage and request window. Legacy facts remain untouched for
compatibility and rollback. No destructive migration or credential change.

Report loading, compact readiness and daily-series loading now share the same
committed reader and source-run proof. Before the first successful publication,
legacy proof remains conservative. A day missing one hierarchy level is not
published. Snapshot reads project only the requested hierarchy level.

Verification: actual mutable-upsert regression failed before implementation.
After implementation, affected Snapchat reporting/unified marketing/Campaign AI
and phase5 tests:214 passed,1 skipped; exit0. Tests cover running/partial/failed
refreshes, complete replacement, shared readiness/daily-series values, owner
isolation, first-run refusal and bounded publication preserving prior data.
No readiness or approval gates relaxed. No Production changes or ad writes.
Next: fresh source checkpoint, independent cloud andCI build/intent agreement,
reviewed PR with intent-only commit, production protocolv5, then verify committed
snapshot creation and seven gates during a concurrent refresh in Production.
