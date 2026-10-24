# Source addition readiness — 2026-10-24

WIP, not merged or deployed. Base: 4e9695365cab73f2ed11fe05fec7427eb51d27b7 (preserves PR #1475).
Branch: codex/source-addition-readiness-20261024.

Live read-only evidence: source-addition capability rejected at components_not_current; canonical source watermark component_pending=true. Payment/stage/identity/review proof checks passed. No customer data stored here.

Root path: source-only refresh persists a new snapshot without inventory reconciliation; the single-item review previously required reconciliation to have already completed, leaving no explicit review recovery path.

WIP change: allow explicit review attempts for versioned canonical pending snapshots. Inside the same owner transaction, validate reviewer/session/source/revision/instructions, reconcile existing stock lifecycle, recheck strict current-component capability, then approve only the selected added line. Reads and assignment guards remain unchanged. No auto routing, employee assignment, or provider write. Arabic UI describes the preflight and individual blocked reasons.

Verification: regression against base fails at eligible=false/components_not_current (exit 1). Two focused real-Mongo regression tests pass (exit 0), proving old pieces/units preserved and reconciliation plus approval roll back together. Full source-addition suite running; broader verification pending. Initial local fixture setup needed proxy environment removed because optional socksio was absent; no app workaround introduced.

Next: complete pending/source conflict/legacy/payment tests, UI tests, review diff; open Draft PR, governed A/B CI and v5 release only after green verification. Production changed: no. No actual customer item approved. Existing payment deployment and unrelated worktrees preserved. Salla Partner event subscription verification remains a separate manual-auth blocker from the prior task.
