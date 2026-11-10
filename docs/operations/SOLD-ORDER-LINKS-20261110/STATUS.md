# Sold-product order navigation and campaign search

Branch: codex/sold-order-links-20261110
Base: ec79949e36e7f636ff4ba8246e4413e7d54bcd6a
Production changed: no. No release intent or deployment created.

Implemented:
- Screen-only Open orders link beside each sold-product cost status, opening a new tab with SKU (name fallback), Riyadh start and exclusive next-day end.
- Orders initializes SKU/product from URL, renders timestamp inputs without losing explicit timezone offsets, and uses the same existing campaign-name resolver as dashboard badges.
- Dashboard top-products report title opens a new tab.
- Campaign-ID search matches exact Salla campaign candidates, including UTM and nested campaign identity, before optional catalog-name enrichment. Candidate extraction runs only for campaign-ID searches. No provider requests, workers or writes added.

Verification:
- pytest backend/tests/test_order_engine_operational_attribution.py: 9 passed.
- CRA Jest SoldProducts.test.jsx: 17 passed.
- CRA Jest AdvancedDashboard.latestOrders.test.jsx and OrdersV2.navigation.test.jsx: 41 passed.
- git diff --check: passed.

Limits / next steps:
- Live campaign in the user's screenshot has not been verified against production data.
- Existing combined-search 5000-row scan cap and 50-result response/no pagination remain unchanged. Thus this patch fixes missing identity matching, not exhaustive historical search; older campaigns outside that window require a separate scoped database search design, not raising the scan limit.
- Review PR/CI, verify the specific campaign live, and use repository Release Guard v5 if deploying. No modifications to costs, accounting, preparation or Snap synchronization.
- GitHub Issue #1006 read attempted but connector transport closed; CLI has no GitHub auth. Retry ledger update through connector after pushing checkpoint.
