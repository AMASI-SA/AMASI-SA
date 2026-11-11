# DASHBOARD-CARD-CADENCE-20261111

## Final state

**Published, verified, and complete for the requested cadence/card scope.** Production changed: yes.

Emergent explicitly finished the one authorized publication at **November 11, 2026, 22:50 Riyadh**, version **7577839**, newer than baseline9d4db97. Official verification finished at2026-11-11T20:05:07.040468+00:00 with **exit0, verified:true, checks:3**, matching the intended source/deployment/runtime identity. Each probe checked576 public frontend requests, including canonical/cache-busted bytes and build-meta MIME. The verifier closed our exact matching release lease; a subsequent official status reported **active:false**.

Both requested card refresh buttons passed authenticated live read acceptance and desktop visual review. No requested cadence/card work remains. Further optimization and comparative performance measurement are separate follow-ups; no overall speedup percentage is claimed.

This document is maintained on **codex/dashboard-card-cadence-evidence-20261111**, an evidence-only branch that must **never be deployed**. Frozen source A, intent B and production merge M remain unchanged.

## Owner request and delivered behavior

| Area | Delivered behavior |
| --- | --- |
| Normal Products recent sync | Successful automatic recent sync admitted hourly per authenticated owner across tabs/workers; manual full catalogue sync remains available. |
| Top Products | Independent30-minute snapshot; circular icon-only button with Arabic accessible name. |
| Period Summary | Independent5-hour snapshot; visible Arabic تحديث button. |
| Executive summary |60-second saved-result gate; omits repeated rich product-row and current-month work. |
| Manual card refresh | Bypasses the saved value for its own card and joins matching in-flight work; reads stored data rather than invoking provider sync. |
| Error/filter behavior | Retains the last successful same-filter value with an error message; hides previous-period data immediately when filters change; fences late replies and rejects incomplete payloads. |

Automatic browser refresh is active while the page is open, visible and online, and checks its deadline on return. It is not a scheduler that runs after the page is closed. Product recent sync retains its existing first-page/30-product scope; it does not replace full manual catalogue sync.

## Resource and financial boundaries

- Dedicated GET routes: `/dashboard-v2/cards/top-products`, `/period-summary`, and `/executive`. The full legacy `/dashboard-v2` contract remains compatible and uncached.
- Owner/filter/day/revision/settings-scoped card cache:32entries,8MiB total,2MiB per entry,8distinct in-flight calculations,45-second calculation deadline. It is local to each application router/process; restarts or another worker can calculate sooner.
- Authentication precedes cache lookup, and cache hits precede heavy-work admission. Same-scope concurrent calculations share work. Failed sibling reads are cancelled and drained before admission is released; failures are not cached.
- Client deadlines are60seconds. Snapshot metadata and financial facts are validated; unknowns, legitimate zeros and negative profits retain their existing meanings. Financial formulas, frozen cost authority, FX/completeness behavior and report filters are preserved.
- Products recent sync uses persisted hourly admission, a120-second lease, at most90seconds of work, and60-second retry delay after failure. Explicit force cannot overlap an active lease. Durable pending cost-revision invalidation and compare-and-set fencing protect interrupted/concurrent writes.
- Preparation/PDF creation was not changed. Automatic Snapchat full sync remains7200seconds; the previously published spend-only refresh is preserved. Legacy Snapchat consumers were not globally disabled.
- Only stored-data card reads were manually tested in production. No provider sync, financial/invoice/ad/budget/bid/preparation mutation was used as a test.

## Main source files

- `backend/dashboard_snapshot_cache.py`
- `backend/dashboard_v2_routes.py`
- `backend/product_v2_recent_sync_routes.py`
- `frontend/src/hooks/useDashboardCardSnapshot.js`
- `frontend/src/services/dashboardCardSnapshot.js`
- `frontend/src/pages/AdvancedDashboard.jsx`
- `frontend/src/pages/MezanProductsAutoSync.jsx`
- `frontend/src/services/mezanProductsV2.js`
- Focused backend/frontend tests and `.github/workflows/dashboard-card-cadence.yml`; the existing placement/source contract was updated for the new executive helper.

The complete source change is reviewed in PR1884: https://github.com/AMASI-SA/AMASI-SA/pull/1884 . A→B changes only `release/release-intent-v5.json`.

## Release identities

| Identity | Exact value |
| --- | --- |
| Repository | AMASI-SA/AMASI-SA |
| Production branch | hotfix/prod-snap-meta-final |
| Source task branch | codex/dashboard-card-refresh-cadence-20261111 |
| Base J | d3c9b0c946afe2418aecf0b96629e46d7f14f196 |
| Source A | 60858eeb6074669dbd93258d7e2f69ebadb59e8e |
| Intent-only B | ff648cf5ebcc29acdf25fa855a8c80481b529f6c |
| Production merge M | 245a0beccbdbc2585a3ec2214e808eeef3734b57 |
| B/M tree | d5240e4fe0e72890d4e06d6da43e07f5ae680962 |
| Runtime ID | rg5-cfe81337974d09f350b54dcb41587c0fcade8596714493ffd753d4332d94819d |
| Intent SHA256 | 5405191a0544b5dc2c8f01aaa75d47b3e6d8cbbb3838d8a962679c5f79b7c2b7 |
| Frontend artifact tree | ab6e7d8994e8a5e32bb2843225bd156c34996798bb1ee780a5643ee35c535325 |
| build-meta SHA256 | 52c470e4651c58e9897f1419cb6c83bea07889da0be9e1132a4e9102e4612bda |

## Verification and publication evidence

- Root backend run:126tests plus5AST subtests passed, zero skips, exit0. It used disposable localhost MongoDB7, in-memory cases, fake providers and read-only synthetic dashboard data.
- Root frontend run:155tests/15suites passed. Placement follow-up:54tests/3suites passed (overlapping selection). Final-source focused CI:159tests/16suites passed, job130749269065.
- Independent final reviews passed. The later read-only count-contract check found no partial-route field loss or change to stored platform-count semantics; it does not establish the real production conversion count.
- Source-A CI artifact13886551580 from run43085668667 matched the locally governed intent bytes exactly. Local two-clean-build proof, verify and freeze exited0.
- Exact-B checks:26successful workflows and1expected conditional TikTok skip; no pending/failure/cancellation. Release-readiness run43086290023 passed both clean-clone adapter rehearsals on host Node20.20.2 and Node24.19.0.
- Normal merge M preserves B's exact tree and parents J/B. Shared `/app` was fast-forwarded only after fresh inactive/clean-source checks; all120 unrelated untracked platform files were preserved.
- Actual shared `cd /app/frontend && yarn build` adapter exited0 in92.18seconds with exact intended source/deployment/runtime identity. The local dependency bootstrap used isolated existing locked packages after a stalled install; CI and the actual shared adapter performed fresh frozen installs.
- Official prepare at2026-11-11T19:37:57.306854+00:00; actor`codex-dashboard-card-cadence-20261111`. Prepublish returned ready_to_publish:true. Exactly one publication was submitted at22:40Riyadh and explicitly finished at22:50Riyadh.
- Official command: `PYTHONDONTWRITEBYTECODE=1 python -B scripts/production_release_guard.py verify --url https://mezansalla.com`. It exited0 after792.57seconds, with3consecutive matching probes and576frontend requests per probe. Condensed result is `official-verify-summary.json`; full stdout SHA256 is9525c57d3024c9a97b04dd963f0809bb29a09bcfb95f7820e3d7c01392b7ba62.
- Subsequent `PYTHONDONTWRITEBYTECODE=1 python -B scripts/production_release_guard.py status` returned active:false; see`final-guard-status.json`.

## Live acceptance

At https://mezansalla.com/dashboard-advanced , both buttons entered busy and returned enabled without a displayed error. Period Summary refreshed month orders1857→1858 and sales466992.90→467313.66. Top Products refreshed products87→88 and units467→468. These are observed read-refresh results, not a latency benchmark or an assertion that no other automatic panel changed concurrently.

See `live-acceptance.json` and screenshots:

- `evidence/dashboard-cadence-1884-cards.png`
- `evidence/dashboard-cadence-1884-publication.png`

Desktop visual acceptance passed. Mobile behavior has automated-suite evidence; a separate live narrow-device inspection is not claimed. The live dashboard is retained as the user-facing result.

## Preparation-duration question

Read-only source review confirms that reviewed preparation creation awaits line/image work, PDF generation, assignment and reconciliation before returning success. The frontend then refreshes its list and downloads the PDF; that download route renders the PDF again. The direct load belongs to those actual requests, not automatically to the employee's whole half-hour workflow. It should subside when creation/download/follow-up requests finish, but backlog or unrelated jobs can delay recovery. No immediate production-speed guarantee is made, and preparation creation remains unchanged.

Relevant source: `backend/reviewed_preparation_batches.py` (`create_batch`, `download_batch_pdf`) and `frontend/src/pages/ReviewedOrders.jsx` (creation followed by list refresh and PDF download).

## Evidence limits and next safe action

- Automatic cadence was verified with controlled clocks, not a five-hour live wait.
- No measured overall production speedup is available. A single observed uncached executive request took5866.498ms; that sample is not a before/after comparison.
- Emergent View Logs exposed recent runtime logs, not the full Cloud Build command transcript or exact package snapshot timing. Those hidden details are not claimed; actual shared adapter, exact CI proof, publication state and deployed byte/identity checks are recorded separately.
- Raw runtime logs were not persisted. No secrets, tokens or customer records are included in this evidence.
- Source A/B, shared production M, unrelated worktrees/terminals and platform files were preserved. No lease remains active for this task.

**Next safe action:** normal operation. Do not repeat the completed release or deploy this evidence branch. Any later performance change starts from a fresh scope/current production state; support-requested comparative measurement and other remaining recommendations are separate follow-ups.
