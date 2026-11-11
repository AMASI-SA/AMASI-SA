# GOOGLE-DASHBOARD-SPEND-20261111

## Scope and current state

- Repository: `AMASI-SA/AMASI-SA`
- Task branch: `codex/google-dashboard-spend-20261111`
- Reviewed production baseline: `245a0beccbdbc2585a3ec2214e808eeef3734b57`
- State: regression reproduced; implementation and independent review in progress.
- Production changed by this task: **no**.
- No campaign, budget, bid, provider configuration, scheduler, sync, backfill, or business-data writes.
- No change to the completed bundle study or the separately released dashboard-card cadence task.

## Evidence and root cause

The authenticated dashboard for the same Riyadh business day shows saved Google
spend in the platform card but zero in the executive summary. Their advertising
totals differ by exactly the saved Google amount. An independent, uncached,
read-only Google Ads query confirms the saved amount, currency, account, and
timezone. No customer records or credentials are retained in this checkpoint.

`build_mezan_v2_ads` reads only `daily_costs.google_ads`; Google native sync saves
to `mezan_google_ads_performance_daily_v2`, which the platform card already reads.
The frontend correctly avoids rewriting backend financials while spend is
provisional, so it does not mask this backend source split.

The live health identity observed during diagnosis was source
`60858eeb6074669dbd93258d7e2f69ebadb59e8e`, runtime
`rg5-cfe81337974d09f350b54dcb41587c0fcade8596714493ffd753d4332d94819d`.
This is a read-only observation of the preceding release, not this fix.

## Intended fix

Use one bounded, read-only native Google projection for both dashboard surfaces,
with explicit legacy-day compatibility and no duplicate addition. Preserve owner,
account, date, currency, and zero-versus-unknown evidence. Keep the single
`google_transitional` numeric compatibility key and leave frontend provisional
financial guards and card refresh cadences unchanged.

Google account-level fee allocation remains a separately identified limitation
of the existing fee engine; this patch does not invent FX or bank-fee settings.

## Regression evidence before implementation

Added `backend/tests/test_dashboard_google_financial_source.py`, using synthetic
owners and accounts with the real Google source entry point, actual ASGI card
and summary routes, real financial reducers, and a read-only database facade.

Command:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHON_DOTENV_DISABLED=1 PYTHONPATH=backend \
  /workspace/scratch/81f2839b514c/courier-test-env/bin/python -m pytest \
  --noconftest -q backend/tests/test_dashboard_google_financial_source.py
```

Result before implementation: **2 failed, 1 passed**, exit 1. The positive
native-spend case returns zero, and configured Google with missing native facts
also returns zero. Both failures are the expected assertions, not setup errors.

## Continuation and release boundary

Root owns this status file, public-route financial acceptance tests, Git
checkpoints, and PR. The backend implementation agent owns the shared Google
reader, its two consumers, and its focused tests; reviewers are read-only.

Next: complete the bounded source fix, rerun the failing regressions, verify
existing dashboard/Google/Snapchat contracts, obtain independent patch review,
and push a reviewable task PR. Do not merge or publish this task without the
separate applicable release authorization and protocol-v5 gates. Do not clear
another task's release lease or modify the shared `/app` workspace.

Issue #1006 is the shared continuation ledger and has reached GitHub's comment
limit. The task PR will pin the exact checkpoint SHA; any ledger-body update
must preserve concurrent tasks and be based on a fresh read.
