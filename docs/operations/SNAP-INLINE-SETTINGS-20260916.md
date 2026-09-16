# SNAP-INLINE-SETTINGS-20260916

Status: IN_PROGRESS. User requested daily campaign/group budgets and strategy-specific Target Cost/Bid in the existing performance rows, with the separate settings table hidden.

Base: hotfix/prod-snap-meta-final @ 1dec19e1ab9df15739a48b3dff90b531519c4e6f (PR1045 live verified3/3).
Task branch: fix/snapchat-inline-settings-20260916.

Acceptance: one table; campaign daily budget is distinct from child totals; Ad Squad daily budget, Target Cost/Bid and parent shown beside performance; missing/stale/wrong-account values never shown as zero or authoritative; settings reads bounded by visible page; selected management read preserved; no provider writes.

Scope: SnapchatV2Page; optional inline settings columns/pagination seam in UnifiedMarketingEntityTable; tests; narrow CI allowlist and this record. Other report consumers retain existing defaults. No sync/backend/accounting/auth changes. No modifications to shared /app during implementation. Prior task completed; this is a new UI correction.

Next: implement and test in isolated task checkout; persist verified checkpoint and PR. Follow AGENTS v5 before any later release.
