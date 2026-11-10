# SALLA-12H-20261110 — scheduled recent-order reconciliation

Scope: Amasi only. Refresh orders created in the preceding rolling 24 hours at
00:00 and 12:00 Asia/Riyadh. Recover absent orders and refresh statuses/items.
This is a candidate change, not deployed. Production changed: no.

Implementation: dedicated background-worker process; shared manual-order import
Mongo lease; durable slot/window/retry state; all sequential order pages with a
12-minute bound; authoritative items endpoint; canonical component snapshot
transactions. No provider writes, financial sends or explicit preparation actions.
Initial startup performs one catch-up window. Failed windows retry after 15m.

Verification (local): scheduled/short-page/boundary/items/restart/concurrency tests
and affected regression tests. Real Mongo acceptance is configured in the existing
background-worker CI workflow. No production database or provider is used by tests.
The old test_salla_orders_pagination_cap.py source-string assertion fails on the
unchanged base as well (expects pagination.get("last_page"), now parsed via helper).
It is superseded behaviorally by test_salla_orders_sync_pagination_evidence.py;
the unrelated old test was not modified.

## Current checkpoint

Owner explicitly authorized continuing and publishing. Production changed: no.
Orders refresh at 00:00/12:00 Riyadh with a rolling 24-hour creation window.
Products V2 catalogue refresh runs in the same cycle with an independent durable
state and per-product resume stamps. Provider details/options/variants refresh;
local cost profiles, components and services remain intact. No catalogue archives
or deletes. Concurrent catalogue edits use updated_at compare-and-set.

Latest focused tests: 43 passed. Previous broader affected suite: 39 passed,
13 skipped because this host cannot run Mongo. Real Mongo CI is mandatory before
merge/deploy. Official local Mongo 8.0.12 failed CPU topology initialization;
7.0.16 failed with open: Operation not permitted. No production test database used.

Remote task branch: codex/salla-12h-reconciliation-20261110.
Source base: 3bd72fe50a40a3a653d060e5589cd8e098dc5cff.
Next: upload reviewed source checkpoint, create PR, execute isolated-Mongo CI,
then freeze v5 source/intent and follow the production release protocol.
Git CLI has no write credentials; use the authorized GitHub connector.

Known limit: persistently failing pending windows remain retryable and require
operator attention; older orders outside the creation window rely on webhooks.
A schedule is not a diagnosis of the historical missing webhook events.

API references: https://docs.salla.dev/orders/list and
https://docs.salla.dev/5565737e0.
