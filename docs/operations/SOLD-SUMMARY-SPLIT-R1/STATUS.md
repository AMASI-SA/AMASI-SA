# SOLD-SUMMARY-SPLIT-R1
Repository: AMASI-SA/AMASI-SA
Branch: codex/sold-summary-split-r1
Base: 42b56ebc351c11e1c5f6d2f5b72fffbd0eab6a72
Production changed: no. No merge, release lease or deployment.

Implemented:
- Always render net profit; unknown cost coverage withholds a numeric profit.
- Separate product/component/service amounts from priced report cells; preserve the
  original authoritative all-item total to avoid duplication. Legacy combined
  snapshots cannot establish a category split and are explicitly incomplete.
- Basket average = sales/orders; ad acquisition average = ads/orders.
- Show cost and profit percentages of sales on screen and in print. No ratio when
  sales are zero or unknown.
- Move platform/account sources behind a three-dot toggle; show distinct linked
  service/component counts from cost columns. Counts and sources are not printed.
- Carry resource identity in report columns to avoid duplicate counts for one resource.
- No worker, schedule, external fetch, database write or fulfillment change.

Verification:
- Frontend targeted Jest: 24 tests passed.
- Backend cost detail pytest: 12 tests passed.
- git diff --check: passed.
- Browser visual acceptance and release rehearsal not run.

Remaining:
- Verify visual layout/print with real report and review category coverage.
- Missing selection recovery for AMS11665 is a separate unresolved task; this change
  does not turn unresolved customer selections into complete costs.
- Prepare a fresh governed release only after review; rebase/integrate with current
  production before freezing A/B intent.

Continuation ledger: task PR (Issue #1006 reached comment limit).
