# SNAP-SETTINGS-RECOVERY-20260916

Status: implementation verified; Draft PR #1045; no merge or deployment.
Repository: AMASI-SA/AMASI-SA
Branch: fix/snapchat-settings-recovery-20260916
Base: hotfix/prod-snap-meta-final at 86e4b54ca808345535eaddf38c96bf7ce0fabfc7.
Verified source commit: 861d7ccfe09409096adb03052f184afefaacd685.
This final documentation-only checkpoint does not change the verified source.

## Recovery evidence
Read AGENTS.md and latest Issue #1006 including continuity PR #1044; inspected relevant branches and PRs. No saved September settings implementation was found. Reimplemented only the missing settings work from current production branch. Memory fix #1041 and unrelated accounting/currencies remain intact. Historical screenshot test counts were not reused.

## Completed
- Campaign/Ad Squad settings table: five rows per page, reset on report change.
- Exact settings reads for the visible page: at most five IDs, limit 1 per request. Backend already filters exact ID and parent before applying its limit.
- Independent exact selected-entity read for management, including off-page selection; latest selection and account/view isolation prevent stale replacement.
- Explicit parent campaign identity for Ad Squads; missing parent data does not crash.
- Separate campaign budget, group budget and sum of child budgets. Unsupported or stale values remain unavailable; no zero or aggregate substituted for campaign budget.
- Strategy-correct Bid / Target Cost; TARGET_COST only. Existing AUTO_BID and maximum-bid semantics retained.
- Read-only load/selection; no proposal, execution or live provider write.

## Changed files
- frontend/src/pages/SnapchatV2Page.jsx
- frontend/src/pages/SnapchatV2Page.test.jsx
- frontend/src/components/marketing/SnapchatEntitySettingsTable.jsx
- frontend/src/components/marketing/SnapchatEntitySettingsTable.test.jsx
- .github/workflows/snapchat-settings-management-v2.yml: allow this exact mandatory handoff document in scope check.
- This document.

## Fresh verification
Dedicated CI: https://github.com/AMASI-SA/AMASI-SA/actions/runs/35106985107 (source 861d7ccfe09409096adb03052f184afefaacd685).
- Scope guard: PASS.
- Python compile: PASS.
- Backend settings, management and safety regression suites: 107 passed, 2 warnings; exit 0.
- CI=true yarn test --watchAll=false --runInBand [Snapchat service, settings table, management panel and page suites]: 4 suites / 82 tests PASS; exit 0.
- CI=false yarn build: PASS; exit 0.
- git diff --check 86e4b54ca808345535eaddf38c96bf7ce0fabfc7 HEAD: PASS; exit 0.
- Security Gate: PASS on verified source.
- Initial source checkpoint 8ce61206c1c365f3e3c078400eeb1742ae7b76b5 had 5 frontend failures for absent optional parent data; repaired and rerun successfully.
Local frontend dependencies were unavailable; tests/build above executed in GitHub CI, not locally.

## Remaining / next safe action
PR remains Draft, unmerged. No manual live-account/browser validation performed. No measured 99% memory reduction claimed.
Mezan Release Readiness run 35106985032 failed during release classification/base validation (reviewed-intent source differs; base release_identity.json missing; exact intent-only B validation fails). Dedicated source build succeeded. Release Intent and release workflow remain unchanged because this task excludes release preparation and deployment. CodeQL was still running at final source evidence collection.
Next safe action: review PR #1045 and source-only evidence; if further fixes are needed, continue on this branch and update Issue #1006. Do not deploy or merge without a subsequent explicit instruction.

Production changed: no. No Emergent, lease, release-intent edit, live ad write, or Production operation performed.

## Merge/deploy request — 2026-09-16
User subsequently authorized merge and publish; the no-Emergent constraint remains.
Verified PR #1045 still open/Draft at 268e05b3e09c93dfbbd2b643d3b0cf8844a8ee4a, production source branch unchanged at 86e4b54ca808345535eaddf38c96bf7ce0fabfc7. Latest head Snapchat scope/backend/frontend checks pass; release Frontend build fails classification before release build.
Concrete blocker: tracked intent source P=f20bdfd866a10e624c1f14bcd8bdbecf2bcac016; P..current base contains AGENTS.md plus release/release-intent-v5.json because continuity PR #1044 follows the reviewed #1043 A/B pair. Thus current base is not the exact intent-only deployment required by Release Guard v5. Do not remove AGENTS.md, forge an intent, or bypass this gate.
This execution workspace has no /app and no callable deployment terminal. Shared lease status cannot be verified here; no lease/rehearsal/publish was attempted. No approved non-Emergent deployment path is available in this session.
Next safe action: resolve the release baseline lineage with regression evidence while retaining continuity instructions, generate a fresh reviewed A/B intent using the governed toolchain, then access the authorized deployment environment and verify lease before merge/publish. Merge/publish remain authorized by the user; do not ask for that permission again. This record is not a new successful release.
Production source branch changed by this request: no. Live Production changed: no. PR remains unmerged.

## Release lineage repair candidate
User requested continuation after authorizing merge/publish. The candidate workflow now resolves the nearest exact prior source/intent pair across documentation-only production staging. Existing prior-intent validation and full-history A/B invariants remain mandatory. Non-documentation staging and intent edits/reverts fail closed. New regression tests use real temporary Git histories, including a no-ff documentation merge.
Local verification: PYTHONPATH=backend:. python -m unittest backend.tests.test_emergent_deployment_adapter_v5 — 37 tests PASS, exit 0. git diff --check PASS. GitHub deterministic release build/intent freeze pending. Scope guard allowlist adds only the adapter, its regression test and release workflow required for this repair. No deployment or lease performed.

The user-authorized publication phase also permits release/release-intent-v5.json in the dedicated Snapchat path allowlist, so reviewed intent-only B can accompany this source PR. Mezan Release Readiness still independently validates the entire intent and exact A/B relation; no test or gate is removed. This source checkpoint precedes intent freezing.
