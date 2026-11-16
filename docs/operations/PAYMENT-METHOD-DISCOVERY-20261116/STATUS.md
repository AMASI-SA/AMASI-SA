# Payment-method discovery and commission setup

## Task and current checkpoint

- Task: `PAYMENT-METHOD-DISCOVERY-20261116`.
- Repository: `AMASI-SA/AMASI-SA`.
- Task branch: `codex/payment-method-discovery-20261116`.
- Verified production/source base: `b56e0794f7d9d4c2ef52e69fd0b36886ea3bfa95`.
- State: source implemented, independently reviewed, and focused gates passed; preparing the remote source checkpoint.
- Production changed by this task: **no**.
- Current release intent still belongs to the preceding release. It must be regenerated under protocol v5 before merging or publishing this feature.
- The full new source SHA and subsequent PR/release state are recorded in the task PR handoff. A status file cannot embed the SHA of the commit that contains itself.
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

1. Commit and push only this task's reviewed paths to its dedicated branch, verify the full remote SHA, and create/update the task PR handoff.
2. Before any shared `/app` mutation or release rehearsal, confirm the official Release Guard reports `active: false`.
3. Use a fresh protocol-v5 source A / intent-only B handoff and governed clean rehearsal. Never reuse the preceding release identity or squash/rebase frozen A/B.
4. Inspect the current PR checks and production base before merge. Preserve unrelated concurrent work.
5. Publication completion requires a newer explicit deployment success and all three official identity probes, followed by an inactive guard. Until then, production completion is not claimed.
