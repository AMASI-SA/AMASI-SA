# SNAP-INLINE-SETTINGS-20260916 — release integration

Status: source integrated; CI and fresh v5 intent pending.

Original reviewed UI checkpoint: 373852854d6af036e9aea0d273844744ed47edda, PR1047, branch fix/snapchat-inline-settings-20260916. Preserved unchanged for recovery. Tests: 95 frontend and107 backend passed; build passed; all final PR1047 checks green.

Delivery branch: release/snapchat-inline-settings-20260916. Source parent: current production6190081644c65e584d1319ee88de42acf1da1443. Includes already-merged auth PR1046 and Snapchat30-day-sync PR1048. No overlapping paths between these production changes and original eight UI task files. All seven executable/test/workflow blobs are identical to PR1047; this document records integration. Direct-parent source avoids old release-intent merge history; no guard bypass or existing task history rewrite.

Behavior: existing campaign/Ad Squad rows include native daily budgets; Ad Squad has strategy-specific Target Cost/Bid and parent; separate settings table removed. Five visible rows bound exact-ID reads; no fabricated missing values or child totals substituted for campaign budget. Management/account/epoch checks unchanged.

Next: verify exact source CI and candidate intent, validate A/J manifests, create intent-only B, require clean-clone rehearsal and required gates, merge preserving A/B. Inspect shared Release Guard before deployment; do not interfere with another lease or terminal session. No provider writes, accounting/Qoyod/currency/Salla edits. Production runtime changed by this task: no. Canonical continuation Issue1006 records exact SHAs.
