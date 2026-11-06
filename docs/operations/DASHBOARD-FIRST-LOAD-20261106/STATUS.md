# Dashboard first usable data — WIP

User reports first figures, executive cards, charts and bestselling products remain slow after PR1782. The first isolated UI regression reproduces a complete backend snapshot waiting behind an unrelated spend read; all four new regressions fail before the change and pass afterward.

This slice publishes the actual selected-period backend snapshot immediately while keeping incomplete profit null. The existing advertising/canonical follow-up continues afterward and cannot replace a newer period. Initial data survives an optional follow-up failure; unmount invalidates pending work. Existing calculation tests now check both the initial and final frames.

All 12 affected frontend suites (115 tests) passed, and the ordinary local Vite build completed in 7.20 seconds. Controlled timing, exact-source governed CI and live authenticated API timing remain pending. Backend dashboard latency is not yet measured; this is not a complete live performance claim. No /app change, lease or follow-up deployment. The user previously authorized commit/push/review and release after checks; that authority persists. Native My Products loading stays unchanged.

The frozen previous release remains recoverable as PR1782. Issue1006 reached its comment cap; the follow-up checkpoint is recorded on PR1782 until a dedicated review PR exists. See STATUS.json for exact scope, evidence and next actions.
