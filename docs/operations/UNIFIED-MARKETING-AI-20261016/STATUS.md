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
- Shared readiness UI validates provider/account/period, exposes hierarchy blockers,
  and links to the existing recommendation/approval page. Removed the misleading
  static Snapchat "not connected" label. Passing diagnostic gates does not itself
  enable execution.

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
