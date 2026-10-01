# Frontend regression classification — MZ2 final integration

Latest existing-writer wiring source `3bf348a236de0c04c6bea40926f19fe2111ec8e0`: fresh complete frontend run gives **9 failed /223 passed suites;21 failed /1248 passed tests;exit1;125.063s**. The same nine suites below fail. Directly implicated source files and dependency manifests remain unchanged from the preceding handoff (`git diff --quiet a53eb991 -- <paths>` exit0). This is not a newly executed baseline checkout. Focused H2 advertising tests pass7/7. Full log remains in the local evidence directory as `wiring-frontend-full.log`; its hash and retained summary are in the follow-up evidence manifest.

Date: 2026-10-01. Read-only investigation; no frontend source/test/dependency changes.

## Evidence and limits

Full-run evidence: `C:/Users/amasi/mz2-final-integration-evidence-20261001/frontend-full.log`.
Logged command: `react-scripts test --watchAll=false --runInBand` via Yarn 1.22.22.
Result: **9 failed / 223 passed suites; 21 failed / 1247 passed tests; exit 1; 177.467 seconds**. Two failed suites could not load and contribute no executed failed test cases. Failure details repeated in Jest's final summary are not additional failures.

Compared production base `5a7b44b71c6c9974aba358493b3267a47d6e6314` with the integration working tree at HEAD `8dd823006832a89517422fb216acb15a44cff5c6`, including uncommitted tracked changes, using `git diff --quiet <base> -- <path>` and `git cat-file -e <base>:<path>`. Every failed test and every directly implicated source listed below exists at base and is unchanged (all diff checks exit 0). `frontend/package.json` and `frontend/yarn.lock` are unchanged. `frontend/craco.config.js` is absent in both trees.

**Classification: all nine suites have baseline source/test or harness mismatches already present in the compared content; no integration-linked direct-path change was found.** A baseline checkout was not executed. Therefore this does not claim an observed baseline test failure, prove identical transitive runtime behavior, or waive the red full-suite gate. The current full run remains failed.

## Suite-by-suite findings

| Failed suite under frontend/src | Failed tests | Observed failure and implication | Log lines | Compared production source paths under frontend/src |
| --- | ---: | --- | --- | --- |
| `services/mezanComponentCatalog.test.js` | 11 | 8 calls fail because buildMezanComponentWorkspace is no longer exported; 3 preview-link cases call the real GET /components-v2/workspace and fail with Axios Network Error. The service declares production mode, while tests expect seven preview components and disabled writes. | 14, 74–266 | `services/mezanComponentCatalog.js`, `services/mezanProductCatalog.js`, `lib/api.js`, `lib/componentCreationRules.js` |
| `components/fulfillment/FulfillmentExperimentPanel.test.jsx` | 1 | Confirmation renders, but the exact expected Arabic phrase says previous invoice/accounting remain unchanged; rendered copy additionally mentions the real Salla shipment. This is a copy assertion failure. | 288–307 | `components/fulfillment/FulfillmentExperimentPanel.jsx` |
| `components/fulfillment/PreparationFilesRegistry.test.jsx` | Load error | Suite cannot load: @testing-library/react cannot be resolved. package.json does not declare this package; manifest and yarn.lock are unchanged. This is a dependency/test-harness blocker, not evidence about registry behavior. | 322–334 | `components/fulfillment/PreparationFilesRegistry.jsx`, `services/orderReviewEngine.js` |
| `mezanUnifiedHeader.test.js` | 4 | Source-text assertions expect an old header branch/whitespace, green CSS class combination, searchOpen state declaration, and overflow class. Current implementation differs; the log also reports MezanV2NavigationShell.test.jsx passing. These failures alone do not establish a responsive-layout defect. | 421–1034 | `components/Layout.jsx`, `components/MezanV2NavigationShell.jsx` |
| `marketingAdsSalesVisibilityFix.test.js` | 2 | Expected 3 and 2 repaired cells; observed 0. Tests rely on enhanceMarketingAdsTables annotating synthetic cells, but that compatibility export now returns 0 because native React tables own columns. The repair function only selects data-mezan-sales-with-spend=true cells. | 1064–1096 | `marketingAdsSalesVisibilityFix.js`, `marketingAdsTableUXEnhancer.js` |
| `defaultDashboardRoute.test.js` | 1 | Source-text assertion requires api.get(`/dashboard-v2?...` literally. AdvancedDashboard uses apiClient.get for the same /dashboard-v2 endpoint (line 708). The observed failure does not demonstrate a changed endpoint. | 1125–1967 | `App.js`, `pages/Dashboard.jsx`, `pages/AdvancedDashboard.jsx`, `pages/retiredDashboard.css` |
| `components/SnapchatSeparatedAccountsView.test.jsx` | 1 | Static rendered markup lacks the exact expected Arabic no-merging phrase. Received markup contains separate account cards and text explaining Snapchat API per-account figures versus Salla totals. This is a copy assertion, not proof that accounts are merged. | 1976–1995 | `components/SnapchatAccountsCards.jsx` |
| `pages/CampaignRecommendations.test.jsx` | 1 | Source-text assertion expects an old Arabic decision-explanation heading. Current page uses revised recommendation/analysis wording. The failure occurs at test line 17 before subsequent wording assertions. | 2006–2483 | `pages/CampaignRecommendations.jsx` |
| `publicLegalNoIndex.test.js` | Load error | Suite cannot load because it synchronously reads missing frontend/craco.config.js. That file is also absent from the production-base tree. This is a stale build-configuration test dependency; legal noindex behavior remains unexecuted in this suite. | 2486–2499 | `publicLegalNoIndex.js` |

## Scope and next action

The evidence supports separating these inherited-content mismatches from the MZ2 canonical wiring, shipping, supplier, and financial-report integration checks. It does **not** justify restoring retired preview functions, DOM enhancers, old UI text, or CRACO as part of this integration. No new feature or redesign is proposed or implemented here.

If a clean full-suite gate is required, authorize a bounded test/dependency maintenance pass: reconcile assertions with approved current behavior, isolate mocked API access, and replace obsolete configuration dependencies with the governed runtime contract. Re-run the nine suites and the full suite afterward. Any behavior change requires its own scope decision. Preserve this failed-run evidence in the final integration status instead of labeling the full regression suite green.

## Final source rerun

At source checkpoint `fb688621c99cdf413604f514beffa274e58d73ea`, after restoring the six delivered Track G service exports, the complete repository test command was rerun: **9 failed /223 passed suites;21 failed /1248 passed tests;exit1;178.478s**. The same nine suites fail. The additional passing test covers native setup transport reachability. Log: `frontend-full-final.log` in the same evidence directory. This supersedes counts for the final source but does not erase the earlier run or waive its classification limits.
