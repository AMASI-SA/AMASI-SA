# Dashboard final net profit — 2026-11-18

## Scope and current state

- Task: `dashboard-final-net-profit-20261118`.
- Repository: `AMASI-SA/AMASI-SA`.
- Task branch: `codex/dashboard-final-net-profit-20261118`.
- Reviewed production base: `37a8211c383079711839557ecad6d5f3f0cdd51f`.
- Follow-up to merged and verified PR #1969. The user reports that Snapchat profit is still unavailable and the account table does not show final net profit after operating expenses.
- **WIP implementation checkpoint. Frontend validation and integrated review are pending.** The task PR is the canonical record of the exact remote checkpoint SHA and subsequent verification.
- **Production changed by this task: no.** No release intent, production merge, lease, deployment, provider refresh, advertising write or financial-data write was performed.

## Verified diagnosis

The current production source retains PR #1969. A fresh read of the current dashboard showed the unassigned Snapchat row and existing bank-fee explanation. The supplied screenshot does not include that unconditional explanation; no row-visibility regression or rollback was established.

The remaining profit gap is reproducible in source. A globally incomplete Snapchat attribution scope suppresses complete-account profit even when a mapped cohort has complete order costs. The table also displays profit before operating expenses, while the executive summary already has canonical final net in `totals.net_profit`.

## Implementation

1. Expose additive known-cohort cost completeness in the existing cost collector. Existing scope/attribution completeness fields are unchanged. No new I/O or provider access.
2. Calculate a separate mapped-order contribution only for exact Snapchat account identity, complete known-cohort costs, currency and time evidence, a complete catalog, matching spend basis and verified account bank fee. It deducts the full account spend and bank charge once. Full account `profit.complete` remains false, and full `netProfit` remains unavailable while attribution is incomplete.
3. Label mapped contributions **جزئي: الطلبات المسندة**. The unknown cohort still has no allocated advertising spend or final profit. Its unavailable value now explains that spend allocation is pending.
4. Add operating expenses and final net to the existing table footer without adding columns. The final value comes directly from the same executive snapshot as the executive summary; it does not subtract fees or operating costs again.
5. Share final-net eligibility between both displays, preserving signed values, cost/currency completeness, selected-period correspondence and current spending reconciliation.
6. Label account and half-day detail profit as before operating expenses. No operating-expense allocation to accounts or half-days is introduced. Existing bank-fee rounding and authoritative all-order totals remain unchanged.

Mapped contributions are not a lower bound: unassigned orders may add either profit or loss. Independently rounded cohort gateway fees need not add exactly to the authoritative total; no residual is redistributed.

## Verification checkpoint

- Six new backend regression cases first failed on the missing completeness evidence and then passed.
- Full affected backend suite: **85 passed**, exit 0, with two existing Pydantic warnings.
- `git diff --check`: passed.
- Frontend regression cases have been written, including paired real table/executive rendering, partial mapped contributions, negative/zero values, single bank-fee deduction and stale/incomplete source rejection. Execution is pending while the frozen-lockfile dependency installation completes.
- Independent integrated source review is running. No complete frontend/build/CI acceptance is claimed at this checkpoint.

Backend command:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHON_DOTENV_DISABLED=1 PYTHONPATH=backend:backend/tests \
python -B -m pytest --noconftest -p no:cacheprovider -q \
  backend/tests/test_dashboard_ads_account_results.py \
  backend/tests/test_dashboard_ads_costs.py \
  backend/tests/test_dashboard_ads_periods.py --tb=short
```

Frontend uses the governed Node 22.23.2 / Yarn 1.22.22 toolchain and the existing lockfile. Neither dependencies nor audit policy were changed.

## Next safe action

Finish dependency installation, run the new and affected frontend suites, resolve concrete failures, inspect the integrated diff and update this checkpoint. Then run the repository-required CI/build gates against the exact reviewed head. Any release must follow the current production-base and v5 A/B procedure; do not reuse PR #1969's release intent or claim that this source-only checkpoint has been published.

Issue #1006 is at its established comment limit. Preserve its body and use this task's PR for the canonical continuation record.
