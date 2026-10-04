# Dashboard memory P0 — WIP checkpoint

Base: 3a2cc4baab7119e59b66a9d667486e118a527e7a.
Scope: dashboard read memory only; no production actions.

Confirmed: recent_abandoned_carts loaded 100,000 carts including all item arrays,
then filtered and sorted in Python. Separate summary path also retains large
order/catalog lists concurrently. The latter is NOT solved by cart pagination.

Owner clarification: active carts must be selected by provider cart_created_at,
not last update/receipt. Missing creation dates are not inferred. Recovery KPI
retains its existing recovery-day definition pending a separate request.

Completed locally: bounded metadata cursor (128 batch), page heap (limit+1),
page-only details, explicit cursor pagination and period counters. Web More
requests additional pages. Work remains on UI tests and full summary memory.

Fresh checks: original creation-date tests failed 3/6 as expected. After change,
6/6 pass. Isolated Mongo at localhost:27261: 4 page tests pass (3000 large carts,
no duplicates, full traversal, empty, tenant boundary, four concurrent readers).
Combined 10 PASS. No skipped. Existing deprecation warnings only.

Pending: 20k-cart fresh-process RSS benchmark single/four concurrent; UI tests;
full dashboard summary boundedness; full applicable CI; final independent review.
This is NOT rootfix-ready, release-ready, or deployed.

No changes to Accounting/Supplier/Shipping/Mobile, pricing or financial writers.
Production financial writes=0. No Production reads invoking operational handlers.
