# Dashboard cost, spend and city correction

## Scope and current state

- Task branch: `codex/dashboard-cost-spend-city-20261112`.
- Draft PR: https://github.com/AMASI-SA/AMASI-SA/pull/1893
- Previous remote checkpoint: `d7b0ac255b77e897f4247e474c2893a805e9543e`.
- Initial reviewed production-branch base: `570867c02ff2486750e4a99d574c25a8fef6c428`.
- Observed live source before this task: `361978f854cecf572a8f3d3f0074f41fd91fcfd0`. At 15:10 UTC, live health reported `d94930510a92e2c426617725d21b9659bb8a9b1c`: the financial-demand-only source already included in our production base. Its health identity checks passed. The production branch remains `570867c02ff2486750e4a99d574c25a8fef6c428`; no rebase or source replacement is needed.
- State: locally verified source candidate; focused backend/frontend regressions and independent reviews passed. Governed build and live acceptance remain pending.
- Production changed by this task: **no**. No merge, release intent, lease, publish, account selection or provider write has been performed.

## Confirmed mechanisms

1. A negative selected parent answer resolved correctly, but the cost resolver discarded the saved Salla visibility condition of a legacy dependent name field. An absent hidden field was therefore incorrectly unresolved.
2. Two option bindings may represent one service only when an exact parent/value visibility condition, service identity, quantity and legacy field relationship prove that equivalence. Unrelated charges must remain separate.
3. The platform spend card exposed a saved partial subtotal while the executive card gated its total on full financial availability. Independent card refresh state could also diverge even when both readers used the same endpoint.
4. Recent orders displayed the raw English city value without a display translation.
5. The owner clarified that only one Google account is active; inactive discovered accounts must not define required spend coverage. The live saved-spend response included three Google accounts in required coverage. The old readers could fall back from no selected accounts to all discovered accounts, and OAuth projection replaced account rows, losing saved selection. Three readers now share a durable owner-selected policy. Legacy records with no selection metadata retain compatibility; an explicitly empty, invalid or missing selection never expands to all accounts. The owner selection operation is implemented but has not been applied. Missing spend for a selected account must remain unknown.

## Implemented at this checkpoint

- Resolve conditional visibility from saved exact option/value IDs; preserve missing, invalid, conflicting, cyclic and deleted-choice uncertainty.
- Collapse only a uniquely proven selected legacy service alias. Retain frozen historical cost precedence.
- Show the available subtotal with an incomplete annotation; preserve unknown final financial totals, CPA, ROAS and profit.
- Share the accepted display snapshot between the two dashboard cards using the existing reads; reject old-period and out-of-order responses. Add no polling or provider synchronization.
- Propagate complete/provisional display amounts too; keep derived financial values hidden until the canonical provider and Snapchat account fee basis agrees. The current executive endpoint retains backend financial authority. Publishing the existing saved-spend result is independent of executive API success.
- Translate the observed Saudi/Gulf city aliases synchronously for display. Preserve unknown labels and delivery-address precedence.
- Add a permanent Google reporting-account selector to both existing integrations-card layouts. It loads saved accounts once when opened and saves an explicit nonempty subset through owner-only GET/PUT routes. Stale scope and uncertain save outcomes require a fresh readback. No provider sync or timer is added.
- Share one bounded selection core between the owner route and `scripts/set_google_reporting_accounts.py` (dry run by default). An existing-integration policy and account selection flags are committed atomically with compare-and-set, audit and fresh readback. Mongo transactions are required; there is no partial fallback.
- Preserve the durable policy and account history during OAuth rediscovery using metadata upserts. A disappeared selected account remains missing; a later owner choice cannot be expanded by an old selected account returning. All three reporting readers use that same scope. Credentials, advertising campaigns and reports are not written.
- Add the selection and city tests to the existing PR-only dashboard workflow; no recurring job was added.

## Verification

From `backend`, using the focused workflow dependency versions:

```sh
python -m pytest --noconftest -p no:cacheprovider -q \
  tests/test_report_option_identity.py tests/test_order_cost_authority.py \
  tests/test_order_cost_identity_projection.py tests/test_sold_product_cost_details.py \
  tests/test_product_option_shared_cost_engine.py tests/test_authority_review_regressions.py \
  tests/test_order_option_cost_snapshots.py tests/test_product_field_cost_support.py
```

Result: **124 passed**, exit 0. Independent review reproduced and closed the deleted-parent-choice edge. The tests use synthetic business identities; no live customer payloads or credentials are stored here.

A separate private replay of the observed order's cost-relevant option IDs, nested selected values and bindings reproduced the original unresolved legacy field, then produced complete cost with the same amount after the fix. No live payload was committed.

Google verification from `backend`:

```sh
python -m pytest -q tests/test_google_reporting_account_selection.py \
  tests/test_google_account_selection_routes.py tests/test_google_selection_rediscovery.py \
  tests/test_google_dashboard_spend.py tests/test_google_ads_reporting_v1.py \
  tests/test_google_ads_overview.py
```

Result: **139 passed**, exit 0. Independent review separately reran the 37 new route/rediscovery cases successfully. Cases cover session ownership and fresh active-owner checks, exact expected scope, policy/account transaction CAS, no unsupported-transaction fallback, empty discovery, A disappears then returns, owner replaces A with B before A returns, concurrent metadata refresh, partial known spend and explicit missing-account repair. Existing reports remain intact. Transaction tests use a driver-boundary staging double; the live owner save/readback remains required to prove the actual server outcome.

Pinned Node 22.23.2 / Yarn 1.22.22 frontend verification:

```sh
CI=true python -B scripts/frontend_release_toolchain.py exec -- \
  yarn --cwd frontend test --watchAll=false --runInBand --runTestsByPath \
  src/services/dashboardAdsSpend.test.js \
  src/components/DashboardAdsSpendCard.test.jsx \
  src/components/ProfitSummaryCard.adsExecutive.test.jsx \
  src/pages/AdvancedDashboard.latestOrders.test.jsx \
  src/pages/AdvancedDashboard.cardRefresh.test.jsx \
  src/pages/AdvancedDashboard.cartFreeze.test.jsx \
  src/lib/orderCityLabel.test.js
```

Result: **7 suites / 255 tests passed**, exit 0. Six regression cases failed against the original base for the expected behavior, then passed on the candidate. Independent callback/race validation additionally passed all four selected cases. Private subtotal replay preserved the original known amount and unknown final total with zero provider calls. City assertions cover the observed destinations plus Jeddah/Makkah/Madinah aliases; no complete worldwide city catalog is claimed.

Google selector frontend verification: **6 suites / 44 tests passed**, including the three new selector/service/actual-card suites and existing Meta, TikTok and compact-card compatibility. The two actual-card mount regressions fail against the original cards for the absent selector. Independent source review cleared the final frontend contract and missing-policy repair case.

The previous `d7b0ac2` checkpoint passed all **27 exact-head GitHub workflows**, and its governed source build reproduced twice. Those results do not establish the final newer selector source build or its CI; both must be rerun for the final source.

`git diff --check`: pass. Independent reviews closed the conditional deleted-value case, complete/provisional snapshot divergence, same-total provider/account financial-basis change, and executive-failure independence. Build and browser acceptance after deployment: pending.

## Next safe action

The authorized Emergent operator database was read-only checked and proved to be development data: it has no target Google accounts or target order. No selection was applied there. Use the live owner selector after deployment to apply the requested single-account scope, preserving inactive accounts and history. The live saved-spend and executive provider bases were compared and matched for Snapchat, Meta, TikTok and both Snapchat bank-fee account bases; the Google coverage scope explains the remaining incompleteness. An unrelated financial release completed while this task was prepared; wait for its shared guard to become inactive before another release rehearsal. Freeze source A, perform the governed A/B build, then create intent-only B. Do not publish before the separate release authorization required by `AGENTS.md`. Before release, re-read the latest production branch and shared Release Guard and preserve other work. The shared operator checkout was observed at the original base with no tracked changes and Release Guard inactive; untracked unrelated files are preserved.
