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

## Reviewed source checkpoint

Backend PR #1377 merged at `6d11803503e98fcd5727f4e11c2abc38ae3c95a7`
after all GitHub checks passed. Reviewed branch head:
`692a68183789cfa33417aa7f06e7a9b242beb410`.
UI PR #1378 is separate, based on production, with source A
`75d61325b62ee5e0bd5f0568f8fb8199ec9b362d`. Settings-only guard and its
backend/frontend tests passed. The UI guard adds only the new extracted UI path.

Full Campaign AI suite: **334 passed, 1 failed**. The failing
`test_production_runtime_uses_same_prompts_as_live_eval` also fails unchanged
production base `a0f03a73ec364481e1995d8d35987fd99a85ac8e` (1 failed/1 passed
in the isolated two-test file); it is not caused by this change.

Read-only live evidence: existing recommendation page generates Snapchat advice,
but showed zero executable approvals; readiness still lacked hierarchy coverage.
This is pre-deployment evidence, not validation of the new implementation.

`/app` guard reported active=false. Its HEAD remains the old production base;
untracked files were preserved. Isolated cloud worktree:
`/root/unified-marketing-ai-release-20261016`. Node22.23.2/Yarn1.22.22 governed
two-pass build verified for source A, artifact tree
`2a8598f897b981603c1e71e99a0da58f91efbbc7464efdae5282f789b0e92f80`, proof hash
`caa84127994d63c87d65cac9e579229bd9c0cbc66cf2ee75d3bacc7aa5308941`.
Initial build correctly rejected using the prior release intent; rerun used the
documented candidate bootstrap mode. Release Readiness run 38908252600 passed
and exported a candidate intent; clean-clone rehearsal awaits intent commit B.

Production runtime changed: **no**; production Git source changed: **yes**, PR1377.
This documentation-only checkpoint is not part of source A. Latest release state
and exact intent/merge SHA must be read from Issue #1006 before continuing.

## Outstanding / next safe steps

1. Compare downloaded CI candidate intent with isolated cloud build, commit only
   the intent as B on the UI branch, then run the clean-clone adapter rehearsal.
2. Complete UI review/merge without squashing A/B; preserve exact SHA in Issue #1006.
3. Follow applicable production protocol v5 for any separately authorized release;
   no lease, deployment or live AI execution has been performed by this task.
4. Validate live account readiness and recommendation generation after deployment;
   mock tests alone cannot prove production coverage or provider execution readiness.

Unrelated main-worktree Qoyod changes and operational selection PR #1376 are
preserved. Do not clear another conversation's lease.

## Final release candidate (supersedes earlier identities)

The first intent was rejected because its source preceded the PR base merge.
New source A `290adba4793c8474f305a0edddf867953a1693b5` includes that merge
without changing the reviewed source tree. Intent B is
`457f66e4e6975b974d75ac6787b3b7f6a32b4ba2` on the UI branch. A..B changes
only `release/release-intent-v5.json`; prior commits were preserved.

- Runtime release: `rg5-763513961fc3a4c3f2fba1d42935246388d0777a7268478c10bdc0808278bbf0`.
- Intent SHA256: `c8274ca3c6323029c69ceb058fc4531efcb277ff5e72c98de1141297c1889a79`.
- Source workflow 38908904404 passed. CI intent bytes match independent cloud
  arm64 freeze exactly.
- Release workflow 38909195999 passed, including clean-clone Host Node20 adapter,
  isolated Backend/Frontend packages and JSON build metadata runtime route.
- Independent cloud adapter rehearsal at B passed and emitted the same identity;
  log `/root/unified-marketing-ai-rehearsal.log`.
- Latest PR check pending at this checkpoint: CodeQL Python. No known failed
  checks for B. PR1378 not yet merged at this checkpoint.
- No publish, no lease, no production financial/advertising write performed.

Next: wait for CodeQL, merge PR1378 without squash/rebase, then follow the separate
publication authorization and mandatory release gates. Do not publish older
75d61325b/19dafa15b candidate. Runtime remains on the prior release.

Rollback if a deployed regression is verified: create a new dedicated revert PR
for this task's source changes (backend1377 and UI1378), retain unrelated later
changes, build a fresh source/intent pair and pass the same deployment gates.
Do not reset the production branch or bypass validation with a stale artifact.
