# SHIPPING_PRINT_LEGACY_PATH_READY

Scope: restore the pre-guard READ -> SELECT -> PRINT behavior. No deployment,
production testing, release preparation, financial writes, or provider mutations.
The latest owner instruction supersedes the earlier proposal to require a newly
identified current shipment. Any ready outbound shipment returned by the legacy
Salla reader may be selected, even if a newer returned shipment is pending.

## Exact Git comparison

- Production base: `a0db1be01d9aa1e3b9c8fcd6c87969cda302b9d3`.
- Last Production commit before the guard:
  `83363097d48e034dc7140a60c290efc684e1ffde` (PR #1238).
- Last source commit before the original guard:
  `6017bb0a7954f8ff188bf35333fde0f26d68321c`.
- Both contain the identical shipping service blob
  `8cd9fc1b87aca3e7eb01710cfc2a0b28c2375a09`.
- Original guard: `95d6d7d783fabd5257e7dd8efb206f5e443aa040`,
  2026-10-01 18:29:48 +03:00.
- Integration copy: `03f83b33e209de06b28740df22a6c7ce905a79ae`.
- First Production first-parent commit containing the guard:
  `78dcf31af73581ceba3677c464c657a0b9b2c4fc`, PR #1245,
  2026-10-02 21:19:45 +03:00. Its parent is the legacy commit above.
- Frontend identity guards: `dac573ddd1a17a0943ec81d925b3a4a5bfda321f`.
- Internal-courier carrier-clock checks and local-ID selection:
  `b3dba04db`, merged in PR #1251 at `513e87304097c34f9c0af99a0420cfbb711d5ab1`.

Original reader: `_resolve_order` -> `_shipment_rows` -> `_active_outbound` ->
first `_snapshot(row)["ready"]` -> return label. `_active_outbound` excludes
returns and cancelled shipments and retains its original sorting. Selection
does not compare shipment ID, AWB, carrier, label, provider timestamps, or local
superseded IDs. Existing Salla GET enrichment/fallback behavior is unchanged.

The old refresh also called resync and persisted/cleared local label fields.
Those side effects are deliberately omitted under the explicit READ ONLY
printing requirement. The old no-label message is retained except the false
claim that the saved number was deleted. Pending-label and Salla-error messages
are retained.

Store courier uses the unchanged legacy `_store_courier_print_data` formatter.
The old issuer's order-status transition, resync and shipment creation are not
called. Explicit store-courier order data can print without an external shipment;
old embedded external shipments are excluded from its address source. The
legacy shipment-based store-courier representation is also supported.

## Minimal production-code changes

1. `backend/order_engine/shipping_label_service.py`: only
   `refresh_shipping_label` changes. Restore legacy selection, bypass local
   baseline/guard/persistence/resync, reuse the old courier formatter.
2. `backend/fulfillment_v2_routes.py`: existing refresh endpoint calls the reader
   after existing permission and completion checks; avoids the workflow-writing
   synchronization wrapper. Issue and confirm-print actions are unchanged.
3. `frontend/src/pages/OrderDetailsV2.jsx`: print through existing refresh API;
   remove shipment/carrier/AWB snapshot comparisons and the issuance fallback
   from the print control. Keep order, permission, unmount and duplicate-click
   protection. Do not invoke the order refresh callback after printing.
4. `frontend/src/components/fulfillment/CompletedFulfillmentOrders.jsx`: allow
   read/print even when saved label metadata is not ready; prevent opening a
   late response after switching orders or losing permission. Existing separate
   issue/confirm/handoff operations are unchanged.

No new service, endpoint, feature flag, persisted snapshot, shipment validation,
carrier synchronization or provider write is introduced.

## Fresh local verification

Backend (Python 3.13, existing isolated test dependencies):

```powershell
$env:PYTHONPATH='backend;backend/tests'
python -m pytest backend/tests/test_shipping_print_legacy_path.py backend/tests/test_shipping_label_current_guard.py backend/tests/test_shipping_print_boundary.py backend/tests/test_fulfillment_carrier_label.py -q --asyncio-mode=auto --tb=short
```

Result: **81 passed, 22 skipped**. Mongo replica-set variants and their
transaction-only case are skipped without `BUILD37_TEST_MONGO_URI`; no live
database was contacted. The HTTP tests use the actual Order Engine route factory
without the unrelated package-wide product/AI router installer, plus the actual
fulfillment router. Salla is replaced at its request boundary and every method
is asserted to be GET. All synthetic collections are compared before/after.

Frontend:

```powershell
$env:CI='true'
node node_modules/react-scripts/bin/react-scripts.js test --watchAll=false --runInBand --no-cache --runTestsByPath src/pages/OrderDetailsV2.shippingIdentity.test.jsx src/components/fulfillment/CompletedFulfillmentOrders.test.jsx
```

Result: **35 passed, 2 suites**. Covers simultaneous shipment ID/AWB/carrier-clock
differences, late responses after order/permission changes, direct courier
printing, missing labels, provider failure, cached stale errors, and no issuance
or order-resync callback during printing.

Regression proof: substitute Production's exact `refresh_shipping_label`
function in memory, without editing source or invoking production. New focused
suite: **10 failed, 3 passed**; failures identify its forbidden resync/guard path.
Candidate suite passes. `git diff --check` passes.

## Unchanged behavior evidence

Source-segment comparison against Production confirms byte-identical bodies:
`issue_shipping_label`, `_persist_verified_snapshot`, `_internal_delivery_document`,
`_best_effort_resync`, `_ensure_order_completed`, `_stale_label`.

Source comparison against the legacy commit confirms byte-identical bodies:
`_resolve_order`, `_shipment_rows`, `_active_outbound`, `_store_courier_print_data`.
The only changed existing service function is `refresh_shipping_label`.

`salla_shipping.py`, `salla_integration/sync.py`, `fulfillment_carrier_label.py`,
preparation, Review Completion/#1289, accounting, webhooks and abandoned-cart
files have no diff. Existing creation/persistence guard tests still pass.

## Review / continuation

Draft PR #1292 was initially opened under the earlier instruction, then closed
when the owner changed the request. Reopen only after the legacy comparison and
tests above; keep it draft. No merge, deploy, prepare/prepublish, release-intent
update, or production test is authorized. Production writes = 0.

Exact final diff: `git diff a0db1be01d9aa1e3b9c8fcd6c87969cda302b9d3 HEAD`.
Historical diff: `git diff 83363097d48e034dc7140a60c290efc684e1ffde a0db1be01d9aa1e3b9c8fcd6c87969cda302b9d3 -- backend/order_engine/shipping_label_service.py frontend/src/pages/OrderDetailsV2.jsx`.
Full final HEAD/TREE and remote checkpoint are recorded in the PR and repository
continuation ledger after committing. Next safe action: review this draft only.
