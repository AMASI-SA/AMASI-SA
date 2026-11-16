# Payment-method discovery and commission setup

## Task and current checkpoint

- Task: `PAYMENT-METHOD-DISCOVERY-20261116`.
- Repository: `AMASI-SA/AMASI-SA`.
- Task branch: `codex/payment-method-discovery-20261116`.
- Verified production/source base: `b56e0794f7d9d4c2ef52e69fd0b36886ea3bfa95`.
- State: all exact-B checks passed; PR1962 merged normally; shared source is aligned to M and its configured adapter build is running.
- Production Git branch changed: **yes**, through reviewed PR1962.
- Live published production changed by this task: **no**, publication has not begun.
- Runtime task branch is frozen at reviewed intent-only B. This status update is a separate documentation-only handoff; never merge or deploy the handoff branch.
- PR: https://github.com/AMASI-SA/AMASI-SA/pull/1962 (merged normally).
- Production merge M: `71d1dd9010b3fd73de0463a6164f82fcd2b7cff5`.
- Previous documentation checkpoint: `d6dd72a581de1793f7160d26e1c324153ace2e54`.
- Source A: `03c91e903c992a2e397fccab2124e07fe002e0e6`.
- Reviewed intent-only B: `03605c83e67c26b8f92477624f92d662fb97616e`.
- B tree: `c04a6d71d253eba4ad96a79cb02eecf39b32f71b`.
- Documentation branch: `codex/payment-method-discovery-handoff-20261116`.
- Local Git push lacked credentials; the authorized GitHub connector preserved an identical source tree and normal A/B parentage. No approval rejection occurred.
- Issue #1006 is at its 2,500-comment limit; this task document and the task PR body are the continuation records.

## User-visible behavior

New payment-method names found in the owner's saved orders appear automatically in the existing Operational Balances payment-fees page. A compact dashboard notice links directly to each method requiring commission setup. Date and payment filters cannot hide unresolved methods.

Commission percentage, fixed fee, and fee VAT remain unspecified until the owner explicitly saves all three terms. Explicit zero terms are accepted; missing values never become zero. Configured custom methods remain listed. The dashboard notice clears on its existing refresh after authoritative settings confirm completion.

The editor writes the existing `settings.payment_methods` authority used by the dashboard/report calculator. It does not activate a fee source, alter dated provider policies, create settlement accounts, backfill orders, or make ledger/payment writes. The existing compact advertising card and its dimensions are unchanged.

## Implementation

- `backend/payment_method_setup.py`: owner-scoped, names-only discovery; calculator-compatible matching; explicit unknown terms; stable settings fingerprint; validated commission save with compare-and-set concurrency protection.
- `backend/accounting_onboarding_setup_routes.py`: additive method directory on the existing candidate read, and a narrow save endpoint requiring a freshly loaded owner role.
- `backend/dashboard_v2_routes.py`: additive executive-card notice data and fee-content-aware executive cache identity; no additional browser polling.
- `backend/server.py`: declares a compound owner/payment-method index alongside existing startup indexes. No live index operation has been executed by this task.
- `frontend/src/components/PaymentMethodSetupNotice.jsx`: compact warning and exact-method links outside the advertising card.
- `frontend/src/pages/operational/ObservedPaymentMethods.jsx`: automatic directory, blank pending fields, explicit save, conflict handling, and authoritative post-save confirmation.
- `frontend/src/pages/operational/ProviderFeeSetup.jsx` and `frontend/src/services/accountingOnboarding.js`: integration with the existing page and request flow.
- `.github/workflows/prod-dashboard-four-platform-spend-v1.yml`: relevant source/test paths and the new focused tests added to existing gates without weakening checks.
- Tests cover the new core, actual authenticated routes, actual dashboard integration, editor behavior, and existing matching/default regressions.

## Verification

All commands exited **0** against the complete reviewed working tree.

### Backend: 128 passed

Run with Python 3.12 and the existing compatible test environment, without bytecode/cache output or dotenv loading:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHON_DOTENV_DISABLED=1 PYTHONPATH=backend:backend/tests \
python -B -m pytest --noconftest -p no:cacheprovider -q --tb=short \
  backend/tests/test_payment_method_setup.py \
  backend/tests/test_payment_method_setup_routes.py \
  backend/tests/test_dashboard_card_snapshots.py \
  backend/tests/test_dashboard_ads_costs.py \
  backend/tests/test_payment_synonym_iteration30.py \
  backend/tests/test_salla_payment_fee_defaults.py
```

Two pre-existing Pydantic deprecation warnings were reported. The tests include actual cost-collector behavior, owner isolation, fresh-role authorization, concurrent saves, preservation of unrelated settings, defaults versus unavailable settings, and executive cache invalidation.

### Frontend: 88 passed across 5 suites

```bash
CI=true PYTHONDONTWRITEBYTECODE=1 python -B scripts/frontend_release_toolchain.py exec -- \
  yarn --cwd frontend test --watchAll=false --runInBand --runTestsByPath \
  src/components/PaymentMethodSetupNotice.test.jsx \
  src/pages/operational/ObservedPaymentMethods.test.jsx \
  src/pages/operational/ProviderFeeSetup.test.jsx \
  src/services/accountingOnboarding.test.js \
  src/pages/AdvancedDashboard.cardRefresh.test.jsx
```

Used the governed Node 22.23.2 / Yarn 1.22.22 toolchain. The actual dashboard integration verifies unchanged polling count, unchanged card placement, and notice removal following the existing refresh.

`git diff --check` and workflow YAML parsing also passed.

## Independent review

Backend core and frontend integration were reviewed separately. One material finding was fixed: unavailable, foreign, or ambiguous merchant settings could previously fall back to reporting defaults. The final implementation leaves all fees null and methods immutable in that case, including known card methods. Genuine absence retains established reporting-default behavior. The reviewer rechecked the fix and its focused tests and approved with no remaining material finding in the reviewed scope.

## Limits and preserved behavior

- Discovery is limited to 250 raw method groups plus an overflow detector, with a 2-second Mongo query limit. Incomplete reads are explicit and prevent editing; they cannot silently clear warnings.
- Method discovery is read-only and runs within existing page/card requests. No scheduler or ingestion hook was introduced.
- Inherited or ambiguous aliases cannot overwrite another configured method's fees through this editor.
- No real merchant commission has been supplied or saved by this task.
- Real Mongo query planning, production latency, and live UI verification remain release-time checks; local tests are not evidence of those properties.
- The preceding compact advertising-table release is already complete and must not be repeated.

## Next safe actions

1. Observe completion of the shared configured adapter; require exit0 and exact source/runtime/artifact identities.
2. Inspect exact-M post-merge checks and reconfirm production/ref/source state.
3. Only after the matching adapter build, prepare the task-owned v5 lease and run prepublish immediately before one authorized publication.
4. Observe a new explicit successful deployment, run all three official verification probes, and require an inactive guard.
5. Confirm the live dashboard notice, direct setup link, automatic pending row and blank input terms without saving any real commission.
6. Update the separate documentation handoff and PR; never merge/deploy the documentation branch.

## Reviewed release evidence

- Runtime ID: `rg5-2e67a08592737d05609cbedd7b422876345be2ac13df2d97ea6f0e44066a24cb`.
- Frontend artifact SHA-256: `a31d130116eaa72d7ba3a0ec0f36fda19c29db272d207642d520482df16e46ff`.
- Reproducibility proof SHA-256: `8d9cf4298e157b914558c2219b1993cb68288d7bed4a7e51ec987567bb70262e`.
- Intent-file SHA-256: `5cdb170471b2770d29584260b6b22d2f8793bf838edbf72e60b9caa419b556df`.
- Local source-A bootstrap, matching A/B frontend builds, verifier and official external candidate validator exited 0. Remote B was fetched and matched the candidate bytes exactly. A to B changes only the intent.
- Readiness B run `43937924442`: backend preflight, frontend build, both Host engine/dispatcher gates and both actual clean-clone adapter rehearsals succeeded. Host 20.20.2 rehearsal job `133548197956`; Host 24.19.0 job `133548197829`. The manual redeploy job is expected to be skipped for this PR.
- Four Platform Spend B run `43937924868` succeeded: backend 345 tests plus 5 subtests; frontend/build/read contract passed.
- Read-performance B run `43937924852` succeeded: query-budget backend 33 tests. Dashboard cadence run `43937924188` also passed.
- Final exact-B checks: **81 completed; 71 success, 10 intentionally skipped, no failure or pending check**. The final Review Completion Resumable run `43937924953` succeeded before merge.
- Root fetched M and verified its tree equals B, A is its ancestor, and the official `verify_release_intent_git` accepted M.

## Shared environment and continuation

The signed-in Emergent Salla Analytics project is available. Existing published version remains `1ce35c9` from the preceding release. Before changing shared source, the official `/app` guard returned `active: false` inside the guarded update command. Shared HEAD was the expected base and both staged and tracked diffs were empty. A normal fast-forward to M then succeeded; observed output confirmed HEAD `71d1dd9010b3fd73de0463a6164f82fcd2b7cff5`, tree equal to B, and tracked-clean state. Existing unrelated untracked files were preserved.

The actual configured `/app/frontend` `yarn build` is running with the reviewed public origin. A wrapper will record its exact exit status and assert the generated backend identity equals the reviewed intent. Local receipts: `/tmp/mezan-payment-method-release-1962/adapter.log` and `adapter-exit.json`. No lease, prepare/prepublish or publication has occurred yet.

Post-merge workflows on M are running: Readiness `43940808188`, Security `43940808320`, CodeQL `43940808126`. Their completion remains a pending observation, not an assumed pass.

An extra scratch-only Host install was cancelled while fetching dependencies before hiding its Git metadata or starting any adapter build. It is not counted as a successful rehearsal. The authoritative two-host clean-clone CI rehearsals passed; the actual shared configured adapter remains required before the lease.
