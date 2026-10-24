# Source addition readiness — 2026-10-24

Source candidate; not merged or deployed. Base: 4e9695365cab73f2ed11fe05fec7427eb51d27b7 (preserves PR #1475).
Branch: codex/source-addition-readiness-20261024. Initial recoverable checkpoint: d3faf6052ebe7e0117028446c364463e3ae50ded.

Live read-only evidence: source-addition capability rejected at components_not_current; canonical watermark component_pending=true. Payment/stage/identity/review proof checks passed. No customer data stored here.

Root path: source-only refresh persists new line/payment facts without inventory reconciliation. Single-item review previously required reconciliation already complete, leaving no explicit recovery action. Provider timestamps can also remain equal while an authoritative canonical revision advances.

Implementation:
- backend/source_added_item_review.py offers explicit review attempts for versioned pending canonical snapshots. Under one owner transaction, validate actor/session/source/revision/instructions, reconcile the stock lifecycle, recheck strict current-component acceptance, then approve only the selected line. Failure rolls all changes back. Reads and assignment guards remain unchanged.
- backend/fulfillment_v2_routes.py permits equal-provider-time reconciliation only for strict operations with the current source revision and a fingerprint matching a freshly read canonical order. Event replay and noncanonical DTOs remain blocked.
- SourceAdditionReview component and sourceAdditionReview service explain pending preflight and blocked/inventory errors in Arabic.
- Regression tests cover explicit-only recovery, source conflict, authoritative-refresh guard, shortage, full rollback, equal timestamp, rejected noncanonical DTO, and paid settlement following awaiting_eligibility using the real source-only refresh boundary.

Validation (all local isolated, no provider writes):
- Original-source regression fails eligible=false/components_not_current (exit 1), reproducing the defect.
- Initial equal-time regression fails component_source_event_stale (exit 1), reproducing the second defect.
- 67 real-Mongo tests pass across source-added review/catalog/stock and G47 lifecycle suites; 3 subsequently added tests pass separately (70 total). Original pieces/units preserved; full DB rollback verified. Fixture databases dropped after tests.
- React component/service suites: 24 tests pass, exit 0.
- git diff --check: exit 0.
- Initial local fixture environment lacked optional socksio for its inherited proxy; test commands unset proxy variables, without app changes.

CI/A-B intent/review/merge/Release Guard/deployment: pending. Next: Draft PR and governed source A build; inspect all applicable CI, freeze intent-only B, reverify, merge and perform v5 guarded release. No actual customer product has been approved or assigned. Production changed: no.

Issue #1006 append rejected by GitHub (403: commenting disabled above 2500 comments). Continue via this remote task status and the task PR until the ledger is available. Separate Salla Partner payment-event subscription verification remains a manual-auth blocker from the previous task. Other worktrees and payment deployment preserved.

## Publication checkpoint

Source A: b54701339314a5af2187ed241b0bc55f54fa3d35. All 11 applicable source workflows passed; review suite 577 tests + 324 subtests, plus acceptance guard.
Intent B: 119d8c216507f3b469ee2c20c93f02255fdb519b. Runtime: rg5-6174cec9a1f3b0ca7000be79409e8d561b4364a17d3c0e6e983efa6e35f91e27. PR #1476: https://github.com/AMASI-SA/AMASI-SA/pull/1476 . B CI and generated-output rehearsal pending.
Rollback branch codex/source-addition-readiness-rollback-20261024 at base 4e9695365cab73f2ed11fe05fec7427eb51d27b7; generated outputs saved at /root/source-addition-readiness-rollback-20261024. Shared /app clean tracked state at that base, guard inactive. Current live frontend source eda00f9f71015c407972e866bbb1adeea8f341e8. Production unchanged by this task. No customer item approved. Next: inspect all B checks, import verified artifacts only after merge and publish through v5.
