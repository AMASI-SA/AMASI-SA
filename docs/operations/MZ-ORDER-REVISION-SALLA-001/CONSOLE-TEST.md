# Browser Console test bridge — draft, not deployed

The owner requires live tests from the authenticated production browser Console.
Local commands below the existing CLI plan are retained for synthetic contracts
and historical recovery; do not use them for this owner's live test.

`backend/order_revision_console_routes.py` exposes authenticated owner-only routes
under `/api/order-revision-tests`. Preparation and execution are disabled unless
`SALLA_ORDER_REVISION_CONSOLE_ENABLED=true` in the deployed backend. Deployment
still requires a separately authorized protocol-v5 release; do not reuse the
inherited release intent. No production order has been mutated by this work.

The server resolves its existing encrypted Salla integration. The browser never
supplies a Salla token. Shared contracts retain the exact order, products/options,
reviewed totals, zero-payment, synthetic receipt and pending-courier bindings.
The Console itself is not a security boundary: the authenticated server contracts
and durable Mongo records enforce scope regardless of the HTTP client.

One immutable fixture is registered per owner. Each prepared plan expires after
five minutes, is atomically claimed once, and acquires an order lock before fresh
eligibility reads. The write intent persists before dispatch. A lost response,
crash or failed postcondition retains the lock for manual reconciliation; there
is no TTL unlock or mutation retry. Original lines cannot be changed. Only a
line created by an observed successful case can be updated/deleted.

## Console procedure after deployment and review

1. Sign in as owner at `https://mezansalla.com`. Paste
   `scripts/research/salla_order_revision_console.js` into that tab's Console.
   Loading the helper performs no network request or order write.
2. Run `await mezanOrderTest.status()`. Proceed only with `enabled: true`.
   A non-JSON/404 response means the endpoint is not available; do not infer
   route availability from `/openapi.json` or SPA HTML.
3. Populate the existing manifest/case templates privately with verified live
   IDs, options and independent before/after totals. The synthetic receipt is
   bound to its exact reference digest. Complete downstream/non-dispatch review.
   Do not publish customer data or filled manifests in GitHub.
4. `const plan = await mezanOrderTest.prepare(manifest, testCase)` reads current
   Salla data and records a plan; it does not mutate order items. Inspect the
   returned method, body and total assertions, and retain `plan.plan_id`.
5. `await mezanOrderTest.execute(plan.plan_id)` sends at most one item mutation
   after fresh checks. If the browser disconnects, use
   `await mezanOrderTest.read(plan.plan_id)`; do not prepare another mutation.
6. Review the returned state and provider snapshots' assertions. `observed`
   with `observed_verdict: PASS` proves only immediate Salla read-back. Overall
   verdict remains `INCONCLUSIVE`, webhook `NOT_VERIFIED`, until the resulting
   webhook and Mezan data are independently correlated. Never report end-to-end
   success from this response alone.

Prepare a new case for each add, quantity update, option update and delete.
Use the recorded added item ID for subsequent operations. Do not guess IDs or
expected totals. Stop on `unknown`/`quarantined`; inspect the durable record and
actual provider state before any lock intervention. No recovery/unlock endpoint
or general order-page editor is introduced here.

## Verification limits

120 isolated tests use synthetic provider I/O and mongomock. They cover shared
CLI contracts, HTTP authorization, add/quantity/options/delete, replay/concurrent
attempts, intent-before-write, expiry, payment/receipt drift and quarantine.
They do not prove live Salla permissions, real Mongo failover durability, complete
live pagination shape, webhook arrival, inventory or downstream behavior. These
remain required live evidence. The general editor and release are incomplete.


## Owner policy update — 2026-09-17 (WIP, not deployed)

Console eligibility no longer requests Salla shipments or examines shipment
numbers, shipment status or courier state. The legacy research CLI retains its
historical fixture contracts; it is not the authorized live test executor.
Completed, delivering and delivered order states prohibit all item mutations.
The state is checked on preparation and again immediately before execution.

Mezan preparation pieces and workflow items are read for PUT/DELETE. Ready
items expose an Arabic confirmation warning and remain blocked from dispatch.
The shared confirmation contract binds customer request, item identity,
preparation revision, reason and authenticated actor. It is intentionally not
wired to execution yet: durable audit, service UI and preparation invalidation
must land together. Do not enable ready-item execution or claim this WIP is a
finished order editor. POST does not require confirmation for existing ready
items. Existing test manifest/payment/original-item protections remain.

## Reviewed staged release boundary

This candidate publishes the inline tracking UI and customer-service product
stop confirmations. The Console test bridge remains disabled by default and
not a general editor. Until provider read-back plus authoritative preparation
reconciliation is proven, PUT/DELETE rejects any existing Mezan preparation
records, including non-ready records. Ready items retain their explicit warning.
Only unallocated disposable test lines can proceed through the test bridge.
No shipment scope, shipment status or tracking number is required.
Actual test writes must use the production browser Console, never a terminal.
