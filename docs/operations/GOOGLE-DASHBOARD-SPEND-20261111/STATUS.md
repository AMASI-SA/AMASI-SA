# GOOGLE-DASHBOARD-SPEND-20261111

## Scope and current state

- Repository: `AMASI-SA/AMASI-SA`
- Task branch: `codex/google-dashboard-spend-20261111`
- Reviewed production baseline: `245a0beccbdbc2585a3ec2214e808eeef3734b57`
- State: publication explicitly authorized; source A and intent-only B frozen and independently validated; remote CI queued; no Production publication.
- PR ready for review: https://github.com/AMASI-SA/AMASI-SA/pull/1886
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

Publication is now explicitly authorized by the user ("انشر"). Next: resolve the queued exact-B CI gates, then perform the normal protocol-v5 merge, adapter, lease, publication and verify3 steps. No further publication confirmation is needed for this exact fix.
Do not merge or publish this task without the
separate applicable release authorization and protocol-v5 gates. Do not clear
another task's release lease or modify the shared `/app` workspace.

Issue #1006 is the shared continuation ledger and has reached GitHub's comment
limit. The task PR will pin the exact checkpoint SHA; any ledger-body update
must preserve concurrent tasks and be based on a fresh read.


## Authorized publication preparation — 2026-11-12 00:03 Asia/Riyadh

Source A is `9365a39e0b3dee0a88191eb0b58ca660a3d91a32`. Frozen intent-only
B is **`adb79110b2c25143730a784de2f4c9dc552cac57`**, tree
`61fda783293ff312a2b5b3c3ae114b8b4977b026`, on the deployment task branch
`codex/google-dashboard-spend-20261111`. It changes only
`release/release-intent-v5.json` after A. Fresh Production J remains
`245a0beccbdbc2585a3ec2214e808eeef3734b57`. Do not squash, rebase or amend A/B.

The pinned toolchain ensure returned warm_verified, Node22.23.2/Yarn1.22.22.
A local frozen install remained stalled at package fetching and was interrupted
with exit130. The local bootstrap used an isolated copy of the preceding approved
release's dependency tree, after verifying identical package.json and yarn.lock
SHA256 values. No dependency, proxy, network or source setting was changed.
This does not substitute for the fresh frozen installs in exact-B clean-clone CI
or the actual shared adapter.

Governed two-clean bootstrap build passed, exit0,21.86seconds, with the reviewed
public origin. Frontend artifact validation, external candidate freeze and
load_candidate_release_intent all passed, exit0. Independent read-only validation
verified exact A/J transitions, both full Git-derived manifests, actual frontend
artifact bytes and reproducibility proof against the candidate. Root then
validated the canonical remote B using load_release_intent and
verify_release_intent_git; exit0, clean local and remote tree equality.
The expected pre-adapter import diagnostic about absent ignored
backend/release_identity.json is not a runtime validation claim.

- Runtime: `rg5-4cf2a091b25ebe5cf33e80523dffc0917424630e9067ae3a7b4030acdcbdbebd`.
- Intent:1268111bytes; SHA256 `9db3a411a19da5d5a3ac3a2ad60224968c8ef7c8a28cb40d6702219a2ca36c23`.
- Backend manifest:1174files,added1/modified2/deleted0; SHA256 `360974cbaee739720388c3f55b0dbd70019a601b94782a4af756268e6da1c71d`.
- Release-control manifest:273files,modified1/deleted0; SHA256 `6c76070e032227083acca8abf580586ee99dac8008a92fb6bcb5049190ef7215`.
- Frontend artifact:`ab6e7d8994e8a5e32bb2843225bd156c34996798bb1ee780a5643ee35c535325`.
- build-meta:`c7b9b3b5c162cf2602ebcaff12871333da31d5c8c4b35e756c6af8fb0b42c4cf`.
- Reproducibility proof:`a4fcef88a75e96d14c13f930bec0597a0f24714578bb6b7e0313df0ffb88ce11`.

### External CI blocker

At00:02Riyadh the final B has20 workflow runs, allqueued with no conclusions
(17synchronize runs plus3ready-for-review runs). PR1886 is ready for review,
mergeable:true, mergeable_state:blocked. The exact-A16 runs also remain queued.
B release gate: https://github.com/AMASI-SA/AMASI-SA/actions/runs/43099171683
B Google/card tests: https://github.com/AMASI-SA/AMASI-SA/actions/runs/43099171528

Independent diagnosis found old PR1886 Release43095000753 (source924e480)
still in_progress despite completed steps and a failed candidate artifact upload.
Its concurrency group mezan-release-readiness-1886 has cancel-in-progress:false.
Superseded cc21 Release43096228904 is also queued. Cancelling these old runs may
release that group, but cannot be claimed to fix all queues: several other old
runs have all steps/jobs finished yet still in_progress, and some queued
workflows define no concurrency group. No confirmed general GitHub outage is
claimed. The official public status page reported Actions operational.

No workflow cancellation was exposed by the connected GitHub tools. The public
GitHub run page showed Sign in. Automatic approval review rejected reading
browserAuth capability documentation, citing the recorded restriction on hidden
browser authentication/credential access. No credential/auth access occurred,
no workaround attempted, and no workflow cancelled or rerun. The ordinary
authorized GitHub connector remains available for source/checkpoint operations.

### Production and exact next step

Production publication has **not** occurred. Shared /app tracked source was not
changed; no release lease acquired, prepare/prepublish, merge or republish.
Official shared guard returned active:false. Emergent Manage still shows the
previous100th publication, version7577839, finished Nov11 at22:50Riyadh.
Concurrent Android78 work and all unrelated platform files are preserved.

This evidence branch **must never be merged or deployed**; deployment input is
the immutable A/B task branch above. Next: obtain terminal passing exact-B CI,
including actual Node20/24 clean-clone adapter jobs. If queue management requires
browser authentication, user must explicitly authorize that distinct access or
perform the old-run cancellation in their own GitHub account. Publication
authorization is already recorded and must not be requested again.

After CI passes, fresh-read Issue1006, Production ref and shared guard before
any shared source/rehearsal. If Production advanced, integrate/rebuild a new
reviewed A/B pair without losing the concurrent task. Otherwise normal merge
with exact B tree, actual adapter, own prepare/prepublish, one publication,
newer explicit DeploymentSucceeded, official verify3, active:false, then
authenticated same-period Google card/executive reconciliation. Since Riyadh
midnight passed, explicitly select2026-11-11 for comparison with the earlier
96.48SAR observation; today's data can differ.
