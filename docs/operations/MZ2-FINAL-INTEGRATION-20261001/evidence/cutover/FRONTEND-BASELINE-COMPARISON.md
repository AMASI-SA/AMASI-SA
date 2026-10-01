# Frontend baseline failure comparison — 2026-10-01

Gate remains BLOCKED. This comparison identifies inherited failures; it does not waive them or establish release readiness.

## Runtime evidence

- Baseline: `0a9f1ac30d2b1a3e8aee21aab1da0ae364a74697`, detached worktree `C:/Users/amasi/mz2-frontend-baseline-0a9f1ac`. Tracked worktree clean after run.
- Current: working tree based on `62adc43910b88201114de93074a5bb25fdbfe495` in `C:/Users/amasi/mz2-final-integration-owner-20261001`; concurrent integration work was permitted, so this is a working-tree run, not a claim of immutable clean HEAD testing.
- Both used `CI=true`, Node22.23.2, Yarn1.22.22, and the identical installed node_modules tree (baseline Windows junction points to current frontend/node_modules). Package.json/yarn.lock have no changes between baseline and current.
- Invocation: `npx --yes --package=node@22.23.2 --package=yarn@1.22.22 yarn --cwd frontend test --watchAll=false --runInBand --runTestsByPath <the nine paths below> --json --outputFile=<external evidence JSON>`.
- Baseline: 9 failed suites, 21 failed tests, 8 passed tests, 29 total, 10.124s, exit1.
- Current focused rerun: same counts, 2.777s, exit1.
- Current original full run: 9 failed/226 passed suites, 21 failed/1263 passed tests, 139.214s, exit1. Baseline was only rerun for these nine failing suites, not its complete frontend suite.
- Machine comparison: all nine suite failures match. All collected test names/statuses and complete failure messages match after removing checkout absolute paths, ANSI color escapes, and stack-frame lines. Suite-load failures match under the same normalization. No failure assertions were dropped.

## Per-suite classification

All paths below are under frontend/. Counts distinguish failed test cases from suites that did not load.

| Suite | Failed cases | Observed failure / classification |
|---|---:|---|
| src/components/fulfillment/FulfillmentExperimentPanel.test.jsx | 1 | Confirmation copy assertion expects older Arabic text; rendered confirmation now explicitly also preserves the real Salla shipment. Likely stale wording contract; no mutation behavior conclusion from this assertion. |
| src/components/SnapchatSeparatedAccountsView.test.jsx | 1 | Rendered HTML lacks expected “لا يوجد دمج بين الحسابات” wording. Copy-contract mismatch; test does not prove account amounts are merged or isolated. |
| src/services/mezanComponentCatalog.test.js | 11 | Tests call removed buildMezanComponentWorkspace and synchronous preview helpers; delivered module exports async production API workspace and writes_enabled=true. Stale preview/API contract strongly indicated. API error paths are not independent proof of a production defect. Requires deliberate test-contract reconciliation, not restoring old fake workspace. |
| src/marketingAdsSalesVisibilityFix.test.js | 2 | Expected repairs3/2, received0 after table enhancement; helper requires data-mezan-sales-with-spend cells, which setup does not produce. Integration/fixture behavior unresolved: could be retired folding contract or actual regression. Baseline equivalence does not settle intended UI behavior. |
| src/mezanUnifiedHeader.test.js | 4 | Source-string assertions for old conditional, green class, collapsed-search state variable and scrolling classes do not match current implementation. Stale structural contracts likely; responsive/accessibility behavior requires separate validation before changing expectations. |
| src/pages/CampaignRecommendations.test.jsx | 1 | Source assertion lacks expected Arabic decision explanation heading. Copy/source-contract mismatch; actual decision-explanation UX still needs checking. |
| src/defaultDashboardRoute.test.js | 1 | Source assertion expects direct api.get dashboard request string; current component delegates loading through loadDashboardPeriodSnapshot. Stale structural assertion likely, not evidence of backend API failure. |
| src/components/fulfillment/PreparationFilesRegistry.test.jsx | Not loaded | Cannot resolve @testing-library/react. Shared installed-dependency/test-environment limitation reproduced identically. No component behavior was tested. |
| src/publicLegalNoIndex.test.js | Not loaded | ENOENT frontend/craco.config.js at module initialization. Test still reads CRACO config while package uses Vite. Stale build-config dependency; browser legal noindex behavior was not exercised. |

Detailed exact test names are in frontend-baseline-comparison.json; raw expected/received diagnostics are preserved in both Jest JSON outputs and logs.

## Artifacts and limits

Evidence directory: `C:/Users/amasi/mz2-final-integration-evidence-20261001`.

- cutover-frontend-full.log: parent full run.
- frontend-baseline-nine.log / frontend-baseline-nine.json: baseline runtime.
- frontend-current-nine.log / frontend-current-nine.json: current matching runtime.
- frontend-baseline-comparison.json: nine normalized comparisons, all true.

An initial attempt with shell-default Node24.19 failed Yarn's engine check before running tests; it was replaced by the exact root Node22.23.2 command above. No engine bypass was used for the recorded comparison. Shared dependencies deliberately keep environment constant; this does not establish that a clean dependency install is complete or that these failures occur on every CI host.

No tracked repo files, UI source, tests or package manifests were edited. The isolated worktree and dependency junction are retained for reproducibility. No production action, commit, push or release was performed. All nine failures remain open gate failures until repaired and verified within authorized scope.
