# SNAP-SETTINGS-RECOVERY-20260916

Status: WIP — bounded settings UI and exact-ID reads implemented; automated verification pending.

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
Local git diff --check passed. Added pagination/parent/budget tests and exact visible-page/off-page-selection read regression. Existing account/stale response and zero-write tests retained. Frontend dependencies are unavailable locally; run dedicated Snapchat settings-management GitHub PR CI (four frontend suites + build, three backend suites). No passing historical counts are reused.

Changed files: SnapchatV2Page.jsx/test, SnapchatEntitySettingsTable.jsx/test, dedicated workflow (allow this exact mandatory task document only), and this document. Backend exact-ID and parent filtering already exist; backend left unchanged.

Next: create Draft PR, inspect CI, repair any failures, and write final verification checkpoint. Early recovery commit: 16029c2b791f4a7dc3081bf0e325cf9304dcd911. No merge or deployment authorized.

Production changed: no.

