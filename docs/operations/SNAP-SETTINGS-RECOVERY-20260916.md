# SNAP-SETTINGS-RECOVERY-20260916

Status: WIP — recovery contract saved; implementation pending.

Base: hotfix/prod-snap-meta-final at 86e4b54ca808345535eaddf38c96bf7ce0fabfc7.
Task branch: fix/snapchat-settings-recovery-20260916.

## Scope
- Bound Campaign / Ad Squad settings table to five rows per page and bound settings reads to visible rows.
- Preserve explicit Ad Squad -> Campaign identity.
- Show campaign budget separately from sum of child budgets; show Ad Squad budget and strategy-correct Bid / Target Cost.
- Fetch precise selected-entity settings for management, preserving stale/account/selection isolation.

## Recovery evidence
Read current AGENTS.md and Issue #1006, including continuity merge #1044. Reviewed Snapchat branches and PRs. No recoverable checkpoint found for the September settings-table changes. Memory correction #1041 is already merged and is excluded. Earlier screenshot test counts are historical claims, not fresh verification.

## Constraints
No Emergent, Production deployment, merge, Release Intent edit, release lease, live provider/budget/bid/accounting writes. No dashboard, Qoyod, GCC currency or memory-worker reimplementation.

## Verification
Not run yet. Next: inspect current settings data flow, add regressions, implement bounded UI/read paths and validate. Save verified milestones on this branch and update Issue #1006.

Production changed: no.
