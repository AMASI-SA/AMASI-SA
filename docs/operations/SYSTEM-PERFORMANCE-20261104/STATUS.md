# System performance investigation — 2026-11-04

## Authorized publication preparation
User explicitly requested publication. Fresh /app status is inactive and tracked files are clean at 6e2ce52a7e6a2fe3c2e8e2f7a9fa20c76e979ff6. CI failures on 8da44fbb9384bae4a1a3d56a823ddea6fc5b8c43 were two obsolete eager-import assertions; 24 other employee tests and 461 fulfillment tests passed. Assertions now require the lazy route declarations while preserving route/navigation checks. Local navigation, fulfillment, protected route and deferred page suites pass: 5 suites, 48 tests. Next: full CI, governed candidate intent, intent-only B, reviewed clean-clone rehearsal, merge and guarded publication. No performance release has been published yet. This release scope is browser initial code loading; server contention and native Ready remain separate unresolved investigation items.

Status: WIP; production unchanged by this task. User authorized performance fixes and verified deployment for web/backend and staff app.

## Verified source and baseline
- Task branch: codex/system-performance-20261104.
- Initial fresh production branch head: 6e2ce52a7 (full SHA in git history).
- Live /api/health reported backend and frontend source 61b9594d11682bb55e9f22d4e1142fd4f51b6d91, protocol v5, critical hashes and frontend build verified.
- Production includes #1732 performance changes and bundle costs; do not assume newer Git commits deployed.
- Mobile main is old. Release #330 identifies staff Android 1.0.50 (65), source 862b98c01c8b78e541ad27155d2d4dfeb57672a0. Installed handset version unknown. Review release source, not main.
- Browser displayed auth recovery. Anonymous GET /api/auth/me timed out at 25.010 s with no bytes. Later sequential controls: health 200 / 2.358 s; auth/me 401 / 1.897 s. These are sparse observations, not p95 or a cause attribution.
- Existing issue #1724 documents deployed cooperative scheduling/report projection changes, and pending independently managed process isolation. Do not move jobs or alter financial/provider writes speculatively.

## Candidate, not deployed
App route components load on demand. Layout holds its navigation while page code loads and shows an Arabic status. Route paths, roles, permission guards, calculations, operations, schedulers and API contracts unchanged.

Same local dependency install and Vite build on baseline and candidate (Node 24.19.0, Yarn 1.22.22, Vite 8.2.4; not the governed release toolchain). Initial HTML JavaScript entry + modulepreload bytes: 7,452,073 -> 2,470,198. Sum of independently gzip-compressed initial files: 1,811,989 -> 652,992. Does not include subsequent route chunks; not a measured device latency or CPU improvement.

Verification: yarn test --watchAll=false --runInBand --runTestsByPath src/components/PageContentBoundary.test.jsx src/components/ProtectedRoute.test.jsx src/components/AppCrashBoundary.test.jsx src/context/authBootstrap.test.js: exit 0, 4 suites / 14 tests. Both Vite baseline/candidate builds succeeded. Existing CSS import ordering and large chunk warnings remain.

## Access and next work
User opened salla-analytics in Emergent. View Logs showed no logs. VS Code was opened via the UI but initially reported editor failed to load. No shared workspace command or release lease has been executed. Retry supported editor access; inspect guard status before /app changes. Need live resource/route diagnostics, native ready reconciliation and installed-build acceptance, full route navigation/regression checks, governed v5 rehearsal, CI/review, and deployment proofs. Preserve unrelated local Qoyod edits in original checkout.

Production changed: NO. No live operational writes performed. This checkpoint is not release-ready and does not establish resolution of intermittent outages.

## Platform evidence and release blocker
On 2026-11-04 the owner requested review of platform email. Support's 08:01 UTC reply attributes possible contention to a single process per replica at about 0.9-1.05 CPU core and reports replica replacement every 20-30 minutes, including without deployments. They report memory rebuilding from about 3 to 6-7 GiB and request grouped releases. Their capacity figures differ from the later 10:19 UTC automated plan alert, which reports 30 intervals above 85% and 15 at 75-85%, with Scale listed as 1 vCPU/4 GB. The aggregate alert cannot identify a particular route, CPU versus RAM, or establish an upgrade as the fix. No infrastructure purchase or support reply was made.

The code-server DOM became accessible. Executed the shared guard status read-only from /app. It reports active=true, actor provider-paid-sar-1735, source b6c63ea67504117023b30087d0174adeb2824658, deployment 6e2ce52a7e6a2fe3c2e8e2f7a9fa20c76e979ff6, prepared_at 2026-11-04T04:30:58.092981+00:00, release rg5-c525f4be2ff66389182109a8a04a1a8e03815a142b048fe4fb98a0c316819b61. This task does not own that lease and must not clear it, change /app, or start a competing release. No deployment attempted.

Mobile release source successfully checked out in separate local clone after transient Git network failures. Native Ready transport intentionally has one 15-second attempt and reports unknown outcome on lost response; coordinator switches to GET-only reconciliation. Re-sending the write or increasing timeouts without diagnosis is not an acceptable fix. No mobile changes made; live-device root cause remains unverified.

Next safe steps: owner of existing lease completes or aborts it; then refresh source, correlate live CPU/loop/memory with heavy routes and jobs, and complete full workflow/device performance validation. Candidate route splitting is independently reviewable but must not be represented as incident resolution.

## Release lock closed at explicit owner request
The user explicitly requested unlocking publication. Fresh inspection found the prepared SAR source b6c63ea67504117023b30087d0174adeb2824658 now live. Emergent first showed Publishing and then Deployment finished Nov 4, 04:47 PM, published identifier e5ef5f8. We did not click Republish or abort/delete the lease. Executed `python scripts/production_release_guard.py verify --url https://mezansalla.com` from /app. Result: verified=true, checks=3, source b6c63ea67504117023b30087d0174adeb2824658 and release rg5-c525f4be2ff66389182109a8a04a1a8e03815a142b048fe4fb98a0c316819b61. Subsequent official status: active=false. The verifier closed the completed matching lease. Runtime deployment was performed by the preceding operation, not this task; performance candidate #1746 remains unmerged and undeployed. The previous lease blocker is resolved; remaining performance acceptance work still applies.
