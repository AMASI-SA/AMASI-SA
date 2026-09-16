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
