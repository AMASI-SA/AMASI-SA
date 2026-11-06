# Dashboard first usable data

User reports first figures, executive cards, charts and bestselling products remain slow after PR1782. The first isolated UI regression reproduces a complete backend snapshot waiting behind an unrelated spend read; all four new regressions fail before the change and pass afterward.

This slice publishes the actual selected-period backend snapshot immediately while keeping incomplete profit null. The existing advertising/canonical follow-up continues afterward and cannot replace a newer period. Initial data survives an optional follow-up failure; unmount invalidates pending work. Existing calculation tests now check both the initial and final frames.

All 12 affected frontend suites (115 tests) passed, and the ordinary local Vite build completed in 7.20 seconds. A controlled React DOM probe with primary data at 400 ms and optional spend at 3500 ms observed first usable sales and product row at 3500 ms on the Production base and 400 ms with the fix. Fake timers prove the removed dependency wait; they do not measure live browser or server performance. Exact-source CI reruns all affected financial, freshness and lifecycle suites.

Manual source review found no additional issues in the changed path. Exact-source governed CI and live authenticated API timing remain pending. This is not a complete live performance claim. No /app change, lease or follow-up deployment. The user previously authorized commit/push/review and release after checks; that authority persists. Native My Products loading stays unchanged.

The frozen previous release remains recoverable as PR1782. Issue1006 reached its comment cap; follow-up checkpoints are recorded on PR1784. See STATUS.json for exact scope, evidence and next actions. Source and intent must follow the two-commit v5 protocol before deployment.
