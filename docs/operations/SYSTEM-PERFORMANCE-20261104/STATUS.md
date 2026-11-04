# System performance investigation — 2026-11-04

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
