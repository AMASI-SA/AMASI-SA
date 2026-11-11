# GOOGLE-DASHBOARD-SPEND-20261111

## Scope and current state

- Repository: `AMASI-SA/AMASI-SA`
- Task branch: `codex/google-dashboard-spend-20261111`
- Reviewed production baseline: `245a0beccbdbc2585a3ec2214e808eeef3734b57`
- State: implementation complete; local acceptance passed; remote CI pending.
- Draft PR: https://github.com/AMASI-SA/AMASI-SA/pull/1886
- Remote regression checkpoint: `924e480258c8052741c5d9999e531739238287e0`.
- Reviewed source checkpoint before the final rounding compatibility fix:
  `cc21d961e111fdf83b2427399e571f9d1e12f715`.
- Production changed by this task: **no**.
- No campaign, budget, bid, provider configuration, scheduler, sync, backfill, or business-data writes.
- No change to the completed bundle study or the separately released dashboard-card cadence task.

## Evidence and root cause

The authenticated dashboard for the same Riyadh business day shows saved Google
spend in the platform card but zero in the executive summary. Their advertising
totals differ by exactly the saved Google amount. An independent, uncached,
read-only Google Ads query confirms the saved amount, currency, account, and
timezone. No customer records or credentials are retained in this checkpoint.

Before this fix, `build_mezan_v2_ads` read only `daily_costs.google_ads`; Google native sync saves
to `mezan_google_ads_performance_daily_v2`, which the platform card already reads.
The frontend correctly avoids rewriting backend financials while spend is
provisional, so it does not mask this backend source split.

The live health identity observed during diagnosis was source
`60858eeb6074669dbd93258d7e2f69ebadb59e8e`, runtime
`rg5-cfe81337974d09f350b54dcb41587c0fcade8596714493ffd753d4332d94819d`.
This is a read-only observation of the preceding release, not this fix.

## Implemented fix

One bounded, read-only native Google projection now feeds both dashboard surfaces,
with positive legacy-day compatibility and no duplicate addition. It preserves owner,
selected-or-all connected account scope, date, stored currency conversion, and
zero-versus-unknown evidence. It keeps the single
`google_transitional` numeric compatibility key and leaves frontend provisional
financial guards and card refresh cadences unchanged.

Native facts block fallback for their day even when invalid or incomplete. Missing
configured Google facts, malformed manual values, duplicate rows, partial account
coverage, and bounded-read overflow remain unknown. Default legacy zero does not
prove Google zero. A merchant without Google keeps other platform totals available
and the platform card still reports Google as unconnected rather than an observed zero.

Native positive sub-cent spend that rounds to SAR zero remains valid; native zero
requires explicit provider proof. A frozen intraday observation stays provisional
after midnight until an observation after the business day is available. Positive
legacy fallback also remains provisional. Google hourly account scope matches the
selected daily projection. These reads trigger no provider or business mutation.

The final compatibility fix preserves the writer's independently rounded native
and SAR amounts. For actual writer arithmetic, SAR hourly micros `[500, 4500]`
persist native `0.005` and SAR `0.00`; USD micros `[4179, 15821]` at stored FX
`3.75` persist native `0.02` and SAR `0.08`. Re-rounding the already rounded native
amount selected the opposite cent and rejected both valid facts. The reader now
requires SAR cent precision and an absolute consistency bound of
`0.005 + fx * 0.0000005 + 1e-9`, derived from the two original quantizations.
It still uses stored SAR unchanged and rejects amounts outside the bound.

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

## Final local verification

Result on the completed patch: **149 passed, 5 source-parity subtests passed,
zero skipped**, exit 0, 9.57 seconds. Two existing Pydantic deprecation warnings
remain. Six changed Python files also passed AST parsing, and `git diff --check`
returned exit 0.

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHON_DOTENV_DISABLED=1 \
  PYTHONPATH=/workspace/scratch/68020b1592fb/google-spend-test-deps:backend:backend/tests \
  /workspace/scratch/81f2839b514c/courier-test-env/bin/python -m pytest \
  --noconftest -q --tb=short \
  backend/tests/test_google_dashboard_spend.py \
  backend/tests/test_dashboard_google_financial_source.py \
  backend/tests/test_dashboard_card_snapshots.py \
  backend/tests/test_dashboard_snapshot_cache.py \
  backend/tests/test_dashboard_v2_salla_ads_executive.py \
  backend/tests/test_dashboard_v2_ad_account_costs.py \
  backend/tests/test_dashboard_ads_executive_inline_fallback.py \
  backend/tests/test_google_ads_reporting_v1.py \
  backend/tests/test_dashboard_snapchat_spend_issue_727.py \
  backend/tests/test_dashboard_ads_hourly_spend_v1.py \
  backend/tests/test_dashboard_four_platform_spend_v1.py \
  backend/tests/test_dashboard_runtime_source_contract.py
```

The isolated test-only dependency directory contains `socksio==1.0.0`, needed by
the existing HTTPX fake-provider tests under this environment's configured proxy.
No proxy configuration or production dependency was changed. The initial broad
attempt had 14 missing-socksio setup failures and 90 passes; after installing the
test dependency, all provider tests passed. No network request was used for the
regression fixtures.

The financial acceptance checks actual executive, complete dashboard and cost-detail
routes. It reconciles synthetic sales minus product costs, Google and Snapchat
spend, and one bank fee; it tests incomplete Google versus proven zero and preserves
unknown Snapchat conversions. The helper tests cover native/manual precedence,
tenant/date/account separation, partial coverage, malformed proof and FX, zero,
sub-cent conversion, day closure, and read limits. New tests are wired into the
existing dashboard-card CI workflow.

Independent read-only integration review found no blocking issue in the two
consumers. Helper review found and drove fixes for day closure, sub-cent zero,
duplicate legacy days, and invalid legacy amounts. The final independent helper
rerun passed **36 tests**, exit 0, in 2.17 seconds. The final rounding delta was
then independently reviewed and its six new boundary cases passed, exit 0,
in 1.84 seconds, with no remaining blocking finding. The final reviewed helper
blob is `c1fc7119a53aa6375bc4feaf73a5cb55a1fa1dcb`, and its test blob is
`59dd751b145ca35a4336946c298184abc640ad6c`.
No frontend implementation changed.

Limits retained: Google-specific bank fee allocation is not added. Per-provider
Google provisional labeling is currently carried by the overall label; existing
generic hourly reads still have their previous 20,000-row cap before in-memory
filtering. No production latency claim or live post-deployment acceptance is made.

## Continuation and release boundary

Root owns this status file, public-route financial acceptance tests, Git
checkpoints, and PR. The backend implementation agent owns the shared Google
reader, its two consumers, and its focused tests; reviewers are read-only.

Next: push the completed source checkpoint and review exact-head CI results,
then request the applicable publication authorization for the concrete PR.
Do not merge or publish this task without the
separate applicable release authorization and protocol-v5 gates. Do not clear
another task's release lease or modify the shared `/app` workspace.

Issue #1006 is the shared continuation ledger and has reached GitHub's comment
limit. The task PR will pin the exact checkpoint SHA; any ledger-body update
must preserve concurrent tasks and be based on a fresh read.
