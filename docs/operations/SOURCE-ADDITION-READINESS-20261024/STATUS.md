# Source addition readiness — completed 2026-10-24

Repository: AMASI-SA/AMASI-SA
PR: https://github.com/AMASI-SA/AMASI-SA/pull/1476
Task branch: codex/source-addition-readiness-20261024
Publication record branch: codex/source-addition-readiness-status-20261024

## Published identity
- Source A: b54701339314a5af2187ed241b0bc55f54fa3d35
- Intent-only B: 119d8c216507f3b469ee2c20c93f02255fdb519b
- Merge/deployment D: b505921915185272d40102d1efef2b8200f2c572
- Runtime: rg5-6174cec9a1f3b0ca7000be79409e8d561b4364a17d3c0e6e983efa6e35f91e27
- Emergent: Deployment finished Oct 24 23:34 Riyadh; published version d7ac594. Publish clicked once.
- Production changed: yes. production_release_guard.py verify --url https://mezansalla.com exited 0, verified=true, checks=3, expected source/deployment/runtime matched. Guard status active=false.
- Evidence: /root/source-addition-readiness-candidate-20261024/{package-verification,prepare,prepublish,production-verify}.json.

## Root cause and behavior
A source-only refresh could persist new line/payment facts with component_pending=true but not reconcile stock. Explicit added-line review previously required that reconciliation already complete, disabling the button. Equal provider timestamps could also reject a newer canonical revision after payment settlement.

Explicit review now validates actor/session/source/revision/instructions, reconciles the pending canonical component snapshot, rechecks strict current acceptance and approves only the selected line in one owner transaction. Failures roll back all changes. Existing pieces/units remain intact; no employee routing or provider write is introduced. Equal-time reconciliation requires strict mode, the current canonical revision and a freshly read matching fingerprint. GET and assignment guards remain read-only/strict. Arabic UI explains pending preflight and stock blockers.

Changed files: backend/fulfillment_v2_routes.py, backend/source_added_item_review.py, backend/tests/test_source_added_item_review.py, frontend/src/components/orders/SourceAdditionReview.jsx and its tests, frontend/src/services/sourceAdditionReview.js and its tests, this status document, and release/release-intent-v5.json.

## Verification
- Two baseline failures reproduced disabled capability and equal-time stale rejection.
- 70 local isolated real-Mongo tests pass across source addition/catalog/stock/lifecycle suites and focused regressions. Covers old pieces/units, read-only GET, source conflicts, authoritative-refresh guard, stock shortage, full rollback, equal-time canonical validation, and settlement after awaiting_eligibility.
- 24 React component/service tests pass. git diff --check exit 0.
- All 11 applicable workflows pass on A and B; unrelated TikTok candidate skipped on B. Review CI: 577 tests + 324 subtests, plus acceptance guard, no failures/skips.
- Clean-clone Host-20 adapter rehearsal, isolated packages and runtime verification pass. Generated output artifact 12574321723, run 40128813374, ZIP SHA256 59eb65f8cd059430605f2f0077681bbd226c7365ba04fc7e0ea63ba820da71bc; inner tar and exact tracked intent checked.
- /app package verification initially rejected one generated __pycache__ directory. Verified untracked .pyc-only content, removed it, reran with PYTHONDONTWRITEBYTECODE=1: exit 0. Tracked source remained clean.
- One transient 520 during deployment routing cleared on reload. Live authenticated capability now eligible=true, reviewed=false, requires_component_reconciliation=true, blocked_reason=null. Web UI reload shows the exact review button enabled and the Arabic component-preflight notice. No actual customer product was approved or assigned. Native Android UI was not separately exercised in this release.

## Rollback and preserved work
Prior production merge 4e9695365cab73f2ed11fe05fec7427eb51d27b7 preserved by branch codex/source-addition-readiness-rollback-20261024 and matching generated-output backup /root/source-addition-readiness-rollback-20261024. Rollback must follow the governed release protocol; do not reset shared source or reuse a closed lease. PR #1475, previous payment fix, untracked shared-workspace files and other worktrees preserved.

## Continuity / remaining work
Issue #1006 rejects new comments (GitHub 403: over 2500 comments); this remote status and PR #1476 provide the continuation record. No further publication or approval is required for this fix. User may refresh the order/app and explicitly review the added item. Do not auto-approve it.

Separate prior payment task: actual Salla Partner order.payment.updated subscription verification still awaits the user's authentication handoff. It was not completed by this button fix; do not claim live automatic event delivery verified.
