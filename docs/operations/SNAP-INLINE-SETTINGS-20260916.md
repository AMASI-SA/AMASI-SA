# SNAP-INLINE-SETTINGS-20260916

Status: IMPLEMENTED AND VERIFIED; ready for source review in PR #1047. Not merged or deployed.

Repository: AMASI-SA/AMASI-SA
Branch: fix/snapchat-inline-settings-20260916
Base: hotfix/prod-snap-meta-final @ 1dec19e1ab9df15739a48b3dff90b531519c4e6f (PR1045 live verified 3/3).
Implementation checkpoint: 7c33aae6b60a8a4640cbb2be01318a6249296bd9. This document-only successor records completion; Issue #1006 records its exact SHA and final CI.

## Behavior

- Removed separate SnapchatEntitySettingsTable mounting from SnapchatV2Page.
- Existing performance rows show campaign daily budget, or Ad Squad daily budget, strategy-specific Target Cost/Max Bid/Bid and parent campaign.
- Automatic bidding has no invented numeric target. Unsupported campaign budgets are not replaced by child totals. Missing, stale, failed, mismatched-entity or unknown-currency settings never produce fabricated monetary values.
- One five-row pagination control drives bounded exact-ID settings reads. Filter/report changes reset to page one, including returning from active-only filtering.
- Selected management reads retain independent account/epoch checks. Other UnifiedMarketingEntityTable consumers keep existing default columns and 25-row pagination.

## Verification

- Isolated frontend component/service suites: 90 passed. Actual-page suite: 5/5 passed after fixtures were completed to render the real performance table instead of a mock-only table.
- CI on 90df4d3adea90a9ceebe7c3a6ab3449e9cbc33f9: Snapchat scope, backend, frontend tests/build, Security Gate and Release Readiness succeeded.
- Final exact tested source checkpoint: 96874b2501fa490eb3a079b3e13363b20e5ccd2c. CI run 35120153768: scope PASS, frontend 95/95 across six suites PASS, production-mode frontend build PASS (5.11s), backend 107/107 PASS. This final successor changes this document only.
- git diff --check: exit 0.

## Scope and continuation

Changed code: SnapchatV2Page(.test).jsx, UnifiedMarketingEntityTable(.test).jsx, new SnapchatInlineSettings(.test).jsx, narrow CI allowlist, this document. No backend/sync/provider/accounting/auth changes. No provider writes. Shared /app tracked code unchanged; implementation/test checkout isolated. Parallel login PR1046 and Snapchat sync work preserved.

Production changed for this correction: NO. Previous PR1045 deployment remains completed and must not be repeated. No new lease/merge/publish. Existing release intent belongs to prior deployed source; do not deploy this source checkpoint with it. Any later release requires fresh source A/intent B, reviewed clean-clone rehearsal and Release Guard v5, serialized with parallel releases. Parallel auth PR1046 merged at 67de138c57fc3204ba4af529b14460e99b4dbf9f after this task started. Next: review PR1047; before a release, integrate the latest production branch without dropping the auth fix, rerun required gates, then freeze a fresh A/B intent. Serialize deployment with the login task. Retain this branch as canonical recovery point.
