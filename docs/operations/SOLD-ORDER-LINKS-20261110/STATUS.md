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

## Dashboard sorting follow-up
- All four numeric headers toggle descending/ascending across the full product cohort before slicing visible rows. Unknown values stay last, negatives sort numerically, and ties retain stable order. No network requests added.
- AdvancedDashboard.latestOrders.test.jsx: 41 tests passed, including clicking all four sort controls and checking products previously hidden under More.
- Issue #1006 cannot receive comments (GitHub 2500-comment cap). PR #1865 is the continuation record. Production unchanged.

## Campaign history and result pagination follow-up
- Campaign-ID searches now stream only stored attribution fields (no customer/payment/items) across tenant history and hydrate only exact campaign matches in batches of 100. Explicit user search only; no remote provider calls/background jobs. Cursor batch 250, Mongo deadline 5s, overall coroutine deadline 8s.
- Combined results now paginate beyond 50 using a filter-bound cursor. Orders uses an explicit Load more results button, preserving filters and ignoring stale responses.
- New regression: matching campaign after 5001 unrelated orders is found without hydrating unrelated orders; 55-result API pagination preserves cohort and rejects cross-filter cursor reuse.
- Verification: 19 focused backend tests pass; 65 frontend tests pass across useOrders, OrdersV2, SoldProducts and dashboard. Broad legacy routes suite cannot initialize because local `openai` dependency is absent; focused tests use the real original Order Engine route factory instead of unrelated application extension routers.
- Remaining: non-campaign advanced searches retain existing 5000 scan cap. Campaign metadata search performance against production Mongo has not been measured; deadline bounds slow requests but does not prove index efficiency. Live screenshot campaign verification still needed after deployment. TikTok hourly data issue is diagnosed only and not changed.
- Production unchanged. No release artifacts, deployment or writes to store data.

## Search refresh regression follow-up (2026-11-10)
- PR #1871 merged as 3bd72fe50a40a3a653d060e5589cd8e098dc5cff and Emergent published version 4900bb6. Full guard verification is still running; do not reuse/clear the owner lease codex-sold-order-1871.
- Live smoke proved sortable headers, new-tab report/product-order links, and resolved campaign hover names. Historical campaign summary now finds 408 results, but polling reduced the list to one; product filter summary 4 also reduced to one.
- Root cause: useOrders refresh treated every search as an exact-order lookup and replaced all rows with the refreshed first order on the 10-second timer/focus/online/visibility events.
- Narrow fix: combined searches keep loaded cohorts and cursor until explicit search/load-more; exact-order refresh remains enabled, stale responses are ignored after a new search, and leaving combined search resets its filter reference. No new background queries/provider calls/workers.
- Regression demonstrated before fix: 3 failed, 6 passed. After fix: 68 passed across useOrders.shippingRefresh, OrdersV2.navigation, SoldProducts, AdvancedDashboard.latestOrders; git diff --check passed.
- Follow-up is not deployed yet. Next: preserve source in dedicated PR, finish #1871 verification, then governed A/B release rehearsal and intent-only commit; check fresh production base before merge. Live campaign and SKU lists must remain intact after 10 seconds and loading more.
- Issue #1006 is at its 2500-comment cap; PR #1871 and follow-up PR are continuation ledgers. Unrelated cost/accounting/fulfillment/Snap synchronization remains unchanged.
