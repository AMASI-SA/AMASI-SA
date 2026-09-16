# SNAP-INLINE-SETTINGS-20260916

Status: IMPLEMENTED; final CI verification in progress. PR #1047.

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
- Final pagination regression added in 7c33aae6; CI run 35119942554 verifies it. Scope and backend already passed, frontend/build pending at this checkpoint. See Issue #1006 for final result.
- git diff --check: exit 0.

## Scope and continuation

Changed code: SnapchatV2Page(.test).jsx, UnifiedMarketingEntityTable(.test).jsx, new SnapchatInlineSettings(.test).jsx, narrow CI allowlist, this document. No backend/sync/provider/accounting/auth changes. No provider writes. Shared /app tracked code unchanged; implementation/test checkout isolated. Parallel login PR1046 and Snapchat sync work preserved.

Production changed for this correction: NO. Previous PR1045 deployment remains completed and must not be repeated. No new lease/merge/publish. Existing release intent belongs to prior deployed source; do not deploy this source checkpoint with it. Any later release requires fresh source A/intent B, reviewed clean-clone rehearsal and Release Guard v5, serialized with parallel releases. Next: verify final CI and review PR1047; retain branch as canonical recovery point.
