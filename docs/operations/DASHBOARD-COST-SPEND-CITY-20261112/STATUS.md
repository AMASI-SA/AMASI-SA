# Dashboard cost, spend and city correction

## Scope and current state

- Initial task branch: `codex/dashboard-cost-spend-city-20261112`.
- Merged PR: https://github.com/AMASI-SA/AMASI-SA/pull/1893
- Follow-up branch: `codex/dashboard-city-clarity-20261112`, based on the first release merge.
- Initial reviewed production-branch base: `570867c02ff2486750e4a99d574c25a8fef6c428`.
- Observed live source before this task: `361978f854cecf572a8f3d3f0074f41fd91fcfd0`. The separate financial release subsequently deployed `d94930510a92e2c426617725d21b9659bb8a9b1c`, already included in our reviewed base. Prior Google fixes were verified deployed by ancestry and live health.
- First release source A: `491577016ec525f89df516d53386d605c1126627`; intent-only B: `b7c34d9cee0f5b888eddef7eca6147e28b97f62f`; tree-equivalent merge M: `06d0002fab257e1976dd2344865da4de77c158c3`.
- First release ID: `rg5-a2a278b1079e2761f7bb619ce677f275c795cfa1a37d7d5fab3b9b59137aef07`.
- First release published successfully at 2026-11-12 15:52 UTC; Emergent version `7a1e7c9`. The reviewed frontend entry `index-Ck3L9ue4.js` is observed in the live browser.
- Production changes: reviewed code published; the live owner reporting policy was saved successfully with exactly one active Google account. No provider campaign, budget, credential, order or address mutation was performed.
- The first release's three-round production verifier is running under this task's owned lease. Do not change the shared `/app` checkout, start another release rehearsal, or create another lease until verification closes it and status is inactive. Current follow-up edits are confined to the local source checkout.

## Confirmed mechanisms

1. A negative selected parent answer resolved correctly, but the cost resolver discarded the saved Salla visibility condition of a legacy dependent name field. An absent hidden field was therefore incorrectly unresolved.
2. Two option bindings may represent one service only when an exact parent/value visibility condition, service identity, quantity and legacy field relationship prove that equivalence. Unrelated charges must remain separate.
3. The platform spend card exposed a saved partial subtotal while the executive card gated its total on full financial availability. Independent card refresh state could also diverge even when both readers used the same endpoint.
4. Recent orders displayed the raw English city value without a display translation.
5. The owner clarified that only one Google account is active; inactive discovered accounts must not define required spend coverage. The live saved-spend response included three Google accounts in required coverage. The owner selector then confirmed that no reporting selection had been configured, invoking the old all-discovered fallback. OAuth projection also replaced account rows, losing saved selection. Three readers now share a durable owner-selected policy. Legacy records with no selection metadata retain compatibility; an explicitly empty, invalid or missing selection never expands to all accounts. The live owner selected the one active account and received successful transactional readback. Missing spend for a selected account must remain unknown.

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

Result: **139 passed**, exit 0. Independent review separately reran the 37 new route/rediscovery cases successfully. Cases cover session ownership and fresh active-owner checks, exact expected scope, policy/account transaction CAS, no unsupported-transaction fallback, empty discovery, A disappears then returns, owner replaces A with B before A returns, concurrent metadata refresh, partial known spend and explicit missing-account repair. Existing reports remain intact. Transaction tests use a driver-boundary staging double; the later successful production owner save/readback established the real transaction path too.

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

The earlier `d7b0ac2` checkpoint passed all **27 exact-head GitHub workflows**. The later `794467b` source reproduced twice and completed 53 workflows with 51 successful and two reviewed gate failures: the runtime query contract expected the old Google projection, and the Snapchat-only path guard was triggered by registering a Google route in shared router composition.

The replacement source carries exactly the two saved-policy fields in the existing runtime read contract, with negative tests rejecting either missing field. It registers Google settings through the existing Google connection registrar, restoring the shared router file to the production base. The Snapchat allowlist and its checks are unchanged. Actual registrar tests prove one GET and one PUT registration and retain the owner contract. The combined actual-registrar and strict runtime-query-contract run passed **67 tests**, exit 0, with bytecode/cache writing disabled. The original projection mismatch was reproduced first. Exact-B CI completed: 37 successful runs, one expected skipped TikTok release-candidate run, and one superseded Snap run with a successful replacement; zero failed or pending runs. This includes full Mongo runtime adoption. Clean-clone and operator-host adapters passed before prepare/prepublish/publication.

`git diff --check`: pass. Independent reviews closed the conditional deleted-value case, complete/provisional snapshot divergence, same-total provider/account financial-basis change, and executive-failure independence.

## Production UI acceptance and bounded follow-up

- The target product's report group is exactly two units: base 80, bag 11, unit 45.50, total 91, status complete. Both embroidery bindings are not required and add no charge. The order detail confirms the negative selection and its aggregate product cost is complete. No live customer payload is committed.
- Both dashboard cards display the same current available spend with a provisional label. The existing manual executive refresh restored CPA and ROAS on the same provider/account basis. No provider synchronization was invoked.
- The Google owner selector saved one of three discovered accounts and returned success. Both inactive accounts and their historical data remain present but excluded from reporting scope.
- Known city aliases render in Arabic. A new live recent-order spelling, `Hail`, remained English; the bounded follow-up adds `hail -> حائل`. Unknown names remain unchanged; this is not a worldwide city catalog.
- The resolved embroidery cell still used the old ambiguous phrase `غير محدد`. The follow-up labels only `not_required` as `غير مطلوب`. Genuine unresolved, missing cost, unavailable historical detail and included-in-base states remain distinct. There is no arithmetic or completeness change.
- Follow-up verification: the existing LatestOrders city-rendering case passed with Hail included (1 passed, 55 intentionally excluded). The full existing SoldProducts suite passed (17 tests), covering distinct cost states, partial totals and printed details. Independent read-only review cleared all four source/test files.

The user confirmed continuation after the merge/publish plan. These acceptance fixes remain within that authorized goal. No recurring checker, synchronization schedule or extra dashboard read was added.

## Next safe action

Finish the already-running first-release verifier and confirm the owned lease is closed. Before another rehearsal or `/app` change, re-read production branch and guard status and preserve unrelated files. Commit the bounded follow-up source, perform its governed A/B build, freeze a new intent-only commit, and run the exact-head gates/clean-clone adapter before normal merge and publication. Recheck the live cost label, Arabic city and retained single-account spend behavior. PR bodies carry post-freeze release identities and verification state so the frozen source is not edited. The repository-wide issue ledger is at its comment limit.

The earlier operator-database diagnosis was read only and proved to be development data without the target accounts/order; no selection was applied there. The successful selection described above was performed solely through the production owner UI.
