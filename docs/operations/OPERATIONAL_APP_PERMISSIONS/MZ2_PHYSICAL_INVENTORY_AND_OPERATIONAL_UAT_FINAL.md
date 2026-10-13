# MZ2_PHYSICAL_INVENTORY_AND_OPERATIONAL_UAT_FINAL

Verdict: BLOCKED, not FINAL PASS. Inspection and diagnostic tests only; no physical writer implemented and no financial integration changed.

## Exact reviewed state
Backend/Web PR https://github.com/AMASI-SA/AMASI-SA/pull/1271, branch codex/operational-app-20261006. Reviewed baseline HEAD cea6fa9d8486aac2a10ff77df082483cf4f530be, TREE de71ddf6ede6bf5130cec6c90de2f580513db5d8; production-reference merge-base a7977f4cfc1ef0a721fc25d783661332f32fe3b6 (local cached reference, not a claim about current deployed Production).
Native PR https://github.com/AMASI-SA/amasi-mobile/pull/257 unchanged: HEAD8a3fa082d45df4233825cf331c2b38f6a70028d0, TREE d0a2e547fc9c50897fef2f3740ecaec6172546ff. APK operational UAT 1.0.11/code14 x86_64, SHA2562407410da1e2b8043521361e9bf5848d9fb16bbf73f92c120d0e0f62a8b5ba0a. Not Build44; not published or installed in this verification pass.

## Authority and current receiving paths
- Physical quantity: warehouse_locations.occupancy.items, with receipt evidence in mezan_inventory_receipts_v2. Reservations and component units are related commitments, not a second on-hand balance. Purchase report quantities are never added to these.
- product_inventory_receipt_routes.py:1039 receive_purchase explicitly rejects with 409 purchase_full_approval_required. This is a deliberate gate, not missing UI wiring.
- purchase_receiving_service.py:298 approve_and_receive is the live receiving coordinator. _validate_posting:236 checks accounting permission, safe cutover/verified opening, account/supplier/tax evidence and the opening inventory cost guard.
- purchase_receiving_service.py:395-440 places receipt, updates authoritative cost, posts journal and creates liability in the existing owner transaction. Calling it violates the present no-accounting-write requirement. Removing pieces would change the approved financial contract.
- inventory_receipt_service.py:13 place_inventory_receipt is a lower-level placement primitive with per-location receipt dedupe/capacity. It does not enforce valuation, cutover, source adoption, global source uniqueness, or the whole lifecycle on its own. Directly exposing it is unsafe.
- purchase_receiving_service.py:200 rejects positive uncosted occupancy; a pending flag is ignored. With an existing matching cost identity the guard can pass without validating the new pending lot: that is also not proof of financial safety.
- fulfillment_v2_routes.py:158 _inventory_rows does not provide a safe unvalued-stock eligibility contract. Existing reader behavior can expose pending items as remaining stock.
- opening_inventory_service.py:132 ready binds cutover/opening and existing receipt schemas; adding a new schema cannot be used to evade it.

## Operations and result
| Operation | Result |
|---|---|
| Purchase invoice, canonical options, names, component without SKU, location reference | Implemented and previously verified; not physical receipt |
| Read existing warehouse physical/reserved/available quantities | Existing projection; 15 fresh tests PASS; no new stock writing |
| Raw10 / ready-silver-Abeer100 physical receiving without accounting | BLOCKED by full approval contract |
| Physical supplier return2/1 | BLOCKED for this lifecycle; existing operational adjustment changes invoice/payable, not warehouse |
| Raw-to-ready atomic conversion | BLOCKED for this scope. stock_preparation_order_routes.py:1549 consumes through stock_component_consumption_service then places stock at1586; this is a preparation-order/BOM provenance path, not an unrestricted purchase-config conversion. User prohibits changing preparation. No manufactured order used as a bypass |
| Component reservation/consumption concurrency | Existing operational_owner-backed service at stock_component_consumption_service.py:402/525 operates on order plans/units and approved source identity; not a general purchase reservation endpoint. Not executed or claimed accepted for new receipts |
| Customer return quarantine / inspect / release to exact ready identity | No safe matching lifecycle contract proved in the inspected operational returns path. BLOCKED; condition labels/read-only filtering alone are not a writer |
| Customization images | Product images and selectable canonical image options supported; customer-upload attachment contract unproven, no enabled upload action added |

## Fresh isolated execution
Mongo localhost27316, disposable operational_balance_test_<uuid> DBs, deleted by test teardown. No production server imported or used.
- Existing boundary probes4 + purchase metadata12 + physical read projection15: 31 PASS, 13.46s.
- New real HTTP/Mongo receiving blocker test: 1 PASS, 1.81s. Real router, owner actor, no mocked receiving/cost guard.
- Initial physical3 -> request raw10:409, stays3 -> same request retry:409, stays3 -> ready100:409, stays3 -> retry:409, stays3. Every database document unchanged after the rejected requests. No receipt or authoritative cost created.
- Physical supplier exit was NOT attempted against quantities never received. Required physical totals gold8, silver/Abeer99, combined110 are NOT achieved. They remain acceptance requirements.
- Earlier operational-only sample (not physical acceptance): purchase2150.00, return50.00, outstanding2100.00; operational quantities8/99; physical3 unchanged.
- Current validated full regression393 Backend /134 Web; Native focused81 and typecheck PASS from preceding exact code checkpoint. These are retained results, not freshly rerun totals in this pass.
- Weighted-average financial approval/cutover E2E: NOT RUN; would require the prohibited accounting flow and an approved transactional test environment. Existing gate diagnostics do not establish financial acceptance. Standalone27316 cannot prove multi-document transaction rollback/concurrency for a proposed new writer.
- Actual new-slice Web/Android UAT: BLOCKED pending approved fixture restart; not PASS.

## Minimum dependency before implementing physical receipt
A coordinated, separately approved integration is necessary, not just a new button or one condition removed:
1. Define pending physical receipt eligibility/source adoption in the existing receipt authority and location/capacity contract; do not create a parallel balance ledger.
2. Integrate with purchase_receiving_service._validate_posting/assert_opening_inventory_initialized/approve_and_receive so later approval adopts the SAME receipt once, values it under the approved policy, and never doubles quantity/cost.
3. Preserve opening_inventory_service.ready/cutover invariants so pending post-cutover receipt cannot be mistaken for opening stock or escape activity checks.
4. Proven consumers must exclude unapproved/quarantined stock from reservation/consumption. This touches fulfillment eligibility and requires separate scope approval under the present prohibition.
5. Prove atomicity and lost-response retry with a real isolated Mongo replica set, fixed source/line identities, revision/unique constraints and failure injection. No change to sealed post_journal_v2 is proposed or implemented here.
No financial modification has been made. Physical integration stops at this dependency as instructed.

## Safe UAT recovery / manual action only
The rejected fixture update/restart command was NOT retried. Read-only inspection still sees listener127.0.0.1:8135 owned by PID10788, command run_fixture.py. No process was stopped. No alternate remote Preview was established as approved.
User may restart ONLY their known isolated fixture terminal after checking that no other task uses it. Do not kill an unknown/shared process. The following foreground start command refuses to proceed while the port is occupied; it starts the existing synthetic fixture only, no Production server and no deploy:

```powershell
if (Get-NetTCPConnection -LocalPort 8135 -State Listen -ErrorAction SilentlyContinue) { throw '8135 is occupied. Do not stop an unknown/shared server.' }
Set-Location 'D:/codex-evidence/operational-inventory-20261007'
& 'C:/Users/amasi/.codex/tmp/mz2-track-f-python/Scripts/python.exe' './run_fixture.py'
```

This command was supplied for manual approved execution, not executed by the agent. Source backend is loaded from the task worktree. The fixture's historical hard-coded startup HEAD is not build identity proof; verify actual loaded source separately. Once a permitted fixture is available, canonical test catalog/location preparation and Web/emulator UAT remain necessary. Restarting the server does not solve the physical accounting dependency.

## Changed in this verification pass
Only backend/tests/test_physical_inventory_operational_boundary.py, this report, and STATUS.json. All application, accounting, opening, preparation, orders, shipping and Salla code unchanged. No Merge/Deploy/Prepare/Prepublish. Production business writes = 0.
