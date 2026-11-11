# DASHBOARD-CARD-CADENCE-20261111

## State
Evidence-only branch. Never deploy it. One authorized publication was submitted; Emergent shows Deployment Started Nov11,10:40PM Riyadh. OUR RELEASE LEASE IS ACTIVE. Do not publish again, change shared source, or clear the lease. Await a newer explicit success, then official verify3/inactive and live card acceptance.

## Owner request and acceptance
- Normal Products recent Salla sync: hourly automatic admission, with manual/explicit recovery still available.
- Advanced Dashboard Top Products: independent 30-minute automatic snapshot and circular icon-only Arabic-labelled refresh button.
- Period Summary: independent five-hour automatic snapshot and visible Arabic تحديث button.
- Manual refresh must work before the automatic deadline, refresh only the selected card, coalesce overlapping work, retain the last successful same-filter result on failure, and never show old-period numbers under new filters.
- Reduce repeated expensive work without changing financial formulas, frozen cost authority, unknown-versus-zero semantics, provider cadence or preparation-file creation.

## Source baseline
Repository AMASI-SA/AMASI-SA; production branch hotfix/prod-snap-meta-final.
Base J: d3c9b0c946afe2418aecf0b96629e46d7f14f196 (published PR1883).
Task branch: codex/dashboard-card-refresh-cadence-20261111.
Existing Snapchat spend-only release is complete; source/intent identities are preserved.

## Design contract
- Dedicated GET /dashboard-v2/cards/top-products, /period-summary and /executive avoid the legacy client response broker's cross-card interference.
- Existing /dashboard-v2 default contract remains compatible.
- Card TTLs 1800 and 18000 seconds; executive saved read TTL60 seconds. Explicit refresh=true bypasses saved value and joins an already-running calculation.
- Owner/filter/day-scoped bounded caches, authentication before cache lookup, and cache hits before heavy-resource admission. Executive invalidates on product-cost revision.
- Executive omits current-month and rich product-row work; light cost calculations retain the same authoritative financial totals.
- Products recent sync receives persisted owner-scoped hourly admission, atomic bounded lease and finite execution timeout. Browser automatic callers stop forcing the endpoint.
- No live provider, invoice, financial, ad, budget or preparation mutation is used as a test.

## Implemented behavior
- Dedicated card routes and independent client state implement the requested 30-minute and five-hour schedules. Explicit manual refresh affects only its card; the global period refresh still refreshes all three dashboard sections.
- The main executive calculation has a 60-second saved-result gate and omits current-month and rich product-row work. Default legacy dashboard callers retain the full uncached response. Financial formulas and frozen cost authority are preserved.
- Snapshot results are scoped by authenticated owner, section, date/filter scope, Riyadh date, product-cost revision, relevant settings and status policy. Each router/process retains at most 32 entries / 8 MiB total / 2 MiB per entry and admits at most eight distinct in-flight calculations, each bounded to 45 seconds. Concurrent reads share work; failed calculations cancel and drain siblings before releasing admission.
- Client reads have 60-second deadlines, validate actual card facts and cadence metadata, retain the last valid same-filter value on failure, and fence late responses and filter changes. Automatic failure retries respect each card interval. Manual refresh remains available immediately.
- Hourly products admission is persisted per authenticated owner across tabs/workers, with a 120-second lease, at most 90 seconds of work and 60-second retry delay after failure. Explicit forced recovery cannot overlap an active operation. Full manual catalogue sync remains available.
- Catalogue scheduling follows returned deadlines, including an initial list sync completing after mount. Cost-changing writes first record durable pending revision invalidation; retries recover that invalidation even when products are unchanged. Lease and source-revision comparisons protect newer concurrent work.
- Added focused GitHub Actions backend/frontend jobs with disposable MongoDB 7. Existing cwd-sensitive source-contract test now locates the same source file relative to its own path.

## Root integrated verification
- Backend: 126 passed, five AST subtests passed, zero skips, exit 0 (9.21 seconds pytest time). The run uses a disposable localhost MongoDB 7 instance plus in-memory cases, fake providers and a read-only synthetic source database for dashboard routes.
- Frontend: 155 passed across 15 suites, exit 0 (12.047 seconds Jest time).
- Workflow YAML parses; git diff --check exits 0.
- PR1884 source-candidate CI passed the new cadence workflow and release-readiness build. Four existing workflows exposed the same obsolete DashboardAnalyticsPlacement source assertion expecting the literal legacy request. That contract now checks the executive helper, latest-request sequence and synchronous filter-key visibility; its other assertions remain. Focused follow-up: 54 tests across three suites passed, exit 0. The placement contract is included in the new focused CI job. No runtime change was needed for this CI follow-up.
- Independent final reviews confirmed catalogue deadline/revision fixes, malformed-success rejection, sibling cancellation/draining, workflow imports and unchanged financial authority. No material blocker remains within this scope.
- Backend command from repository root: PYTHONDONTWRITEBYTECODE=1 PYTHON_DOTENV_DISABLED=1 PYTHONPATH=backend:backend/tests PRODUCT_RECENT_SYNC_TEST_MONGO_URI=<disposable-localhost> python -B -m pytest -q --tb=short backend/tests/test_dashboard_snapshot_cache.py backend/tests/test_dashboard_card_snapshots.py backend/tests/test_product_v2_recent_sync_cadence.py backend/tests/test_product_v2_recent_sync_contract.py backend/tests/test_dashboard_runtime_source_contract.py backend/tests/test_dashboard_v2_live_refresh_contract.py backend/tests/test_dashboard_v2_product_cost.py backend/tests/test_dashboard_bundle_cost_authority.py backend/tests/test_dashboard_v2_salla_ads_executive.py backend/tests/test_dashboard_memory_admission.py.
- Frontend command from frontend: CI=true yarn test --watchAll=false --runInBand --runTestsByPath src/hooks/useDashboardCardSnapshot.test.jsx src/services/dashboardCardSnapshot.test.js src/pages/AdvancedDashboard.cardRefresh.test.jsx src/pages/AdvancedDashboard.latestOrders.test.jsx src/pages/AdvancedDashboard.cartFreeze.test.jsx src/pages/AdvancedDashboard.mobile.test.jsx src/pages/AdvancedDashboard.campaignJobs.test.jsx src/components/DashboardPanel.test.jsx src/services/mezanProductsV2.recentSync.test.js src/pages/MezanProductsAutoSync.cadence.test.jsx src/campaignProfitabilityProductNavigation.test.js src/lib/dashboardPlatformSpendMerge.test.js src/lib/dashboardLatestResponseBroker.test.js src/components/DashboardAdsSpendCard.manualRefresh.test.jsx src/services/snapchatManualSpendRefresh.test.js. Root used the existing locked react-scripts installation without changing dependencies; CI performs a fresh frozen install.

## Release state and next safe action
Current AGENTS.md and Issue1006 were read. Fresh production fetch still resolves exactly to d3c9b0c946afe2418aecf0b96629e46d7f14f196. The local origin tracking ref is limited by its fetch refspec; use explicit fetched SHAs and do not treat the stale tracking alias as a rollback.
PR1884 is open. After the development environment resumed, official status freshly reported inactive and shared tracked source was clean at J in the task-owned Terminal2. No paid agent chat is used. An isolated local frozen install stalled during package fetching and was interrupted; an isolated copy of the existing locked dependency tree is used for the local bootstrap, with package.json/yarn.lock verified unchanged. CI and the actual shared adapter perform fresh frozen installs, and intent bytes must agree with the local build.
Next: validate the updated source candidate in exact-head CI, run the local governed two-clean-build proof with the reviewed public origin, compare CI candidate intent, freeze intent-only B, require exact-head CI and normal merge. Then repeat guard/clean-head checks, fast-forward shared /app to the exact merge while preserving unrelated untracked files, run the actual adapter, and only then own prepare/prepublish and one already-authorized publication.
Final acceptance requires newer explicit deployment success, official verify with three probes, final inactive guard, and live visual/manual card readback. Timing tests use controlled clocks; no claim of five-hour live observation or overall production speedup is made.
Preparation creation, automatic two-hour Snapchat sync, prior completed spend-only release, other worktrees and platform files remain outside this task. No live provider, invoice, financial or advertising mutation was used as a test.


## Frozen release identities and evidence branch
- Evidence branch: codex/dashboard-card-cadence-evidence-20261111 (never deploy).
- PR: https://github.com/AMASI-SA/AMASI-SA/pull/1884
- Final source A: 60858eeb6074669dbd93258d7e2f69ebadb59e8e.
- Intent-only B: ff648cf5ebcc29acdf25fa855a8c80481b529f6c.
- B tree: d5240e4fe0e72890d4e06d6da43e07f5ae680962.
- Deterministic runtime: rg5-cfe81337974d09f350b54dcb41587c0fcade8596714493ffd753d4332d94819d.
- Intent SHA256: 5405191a0544b5dc2c8f01aaa75d47b3e6d8cbbb3838d8a962679c5f79b7c2b7; 1267519 bytes.
- Governed local two-clean builds completed in 28.71 seconds; verify and freeze exit 0. Both build-meta hashes and full artifact-tree hashes agree. The task-generated untracked backend pytest cache was removed before freeze; tracked source was unchanged.
- Downloaded final-A release-readiness artifact 13886551580 from run43085668667; exact source and base verified, archive digest checked, and intent bytes identical to local.
- Exact-B release-readiness run43086290023 and all other required checks are pending; no merge/prepare/publish yet.
- Next safe action: require exact-B terminal CI success including Node20/24 clean-clone adapter rehearsals, normal merge with B-equivalent tree, then fresh shared guard/clean-head checks and actual adapter build. Source A/B must not change.
- Source-A focused CI frontend job130749269065:159 tests across16 suites passed, exit0, including the updated placement contract.

## Exact-B adapter rehearsal passed
Release-readiness run43086290023 completed successfully. Both fresh clean-clone adapter rehearsals passed: host Node20.20.2 job130752550360 and host Node24.19.0 job130752550356, each dispatching the pinned governed build. Backend preflight and Frontend reproducibility checks also passed. Five general workflows are still running; merge remains pending. No shared source mutation, lease or publication.

## Normal production merge verified
- Merge M:245a0beccbdbc2585a3ec2214e808eeef3734b57. Parents are base Jd3c9b0c946afe2418aecf0b96629e46d7f14f196 and frozen Bff648cf5ebcc29acdf25fa855a8c80481b529f6c.
- Merge tree equals B exactly:d5240e4fe0e72890d4e06d6da43e07f5ae680962. Source A is an ancestor and the content diff after A changes only the intent.
- Root independently confirmed27 exact-B workflows terminal:26success,1expected branch-gated TikTok skip,0failures/cancellations/pending.
- This supersedes pending-CI/merge statements above. No publication submitted. Next fresh shared guard active:false, expected clean HEAD at J, fast-forward to exact M preserving unrelated untracked files, run actual package adapter, then own prepare/prepublish and one authorized publication.

## Shared adapter passed and own lease prepared
- Actual shared package adapter exit0 in92.18seconds, with exact source A, merge M and runtime identity. Shared tracked source is clean; all120 unrelated untracked files were preserved.
- Official prepare succeeded at2026-11-11T19:37:57.306854+00:00, protocol5, actor codex-dashboard-card-cadence-20261111. OUR matching lease is active.
- Production Git source changed; public runtime remains prior version9d4db97 until publication.
- No task publication yet. Next official prepublish immediately before one authorized publication, newer explicit success, official verify3 and final inactive, then live card acceptance. Preserve frozen A/B and shared M throughout.

## Publication submitted once
Official prepublish returned ready_to_publish:true with exact A/M/runtime identities. The Publish changes button was clicked once. Emergent explicitly shows Deployment Started Nov11,10:40PM Riyadh and Packaging your latest changes. Prior publication baseline is9d4db97. No new success or production identity claim yet. Own lease actor remains codex-dashboard-card-cadence-20261111. Do not repeat publication or modify/app.
