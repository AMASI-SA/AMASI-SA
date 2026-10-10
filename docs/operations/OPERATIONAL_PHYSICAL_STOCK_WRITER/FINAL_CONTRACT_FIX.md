# MZ2_OPERATIONAL_STOCK_ELIGIBILITY_FINAL_CONTRACT_FIX

Scope: Draft #1317, based on a033fa6076eb3cfc2ce901400ccf36adc8c14637. Only functional file changed in this phase: backend/fulfillment_v2_routes.py. Three existing test files updated. No fifth functional file, writer, index/hint, projection, accounting, opening, orders, shipping or Salla sync change.

## Contracts repaired

1. Differing receipt/lot IDs require the exact original stock-preparation:<source_id>:<source_line_id>:<receipt_id> relationship against current posted source evidence. Wrong IDs, missing provenance and unsafe conditions stay blocked. Original Mezan/Salla aliases and SKU-less key fallback are covered.
2. A unique approved opening witness can prove retained historic lots without being shadowed by an old receipt. Current pending/rejected/unsafe evidence always vetoes. Explicit conflicting identity/configuration/quantity/location also vetoes; missing positive fields in a sparse old receipt can be supplied by the approved opening witness. Opening aggregate quantity bounds current adopted remainders; duplicate/ambiguous witnesses or lots and manufacturing-provenance adoption reject. No opening documents are created or modified by production code.
3. Configuration validation compares canonical specifications, preparation state and variant aliases. Original product purchase receipts omit specs/state but encode them in their configuration key; the reader recomputes that existing contract. Missing keys require explicit matching complete proof or the original product key. Conflicting/malformed or unprovable specifications yield unavailable stock with a reconciliation reason, preserving physical quantity. Names/SKUs alone never prove a configuration. Receipt quantity must bound current stock.

All consumers retain the shared helper and existing owner transaction, revalidation, identity snapshot and CAS fencing. Variant alias is now included in reservation identity snapshots. Source quantity remains warehouse_locations.occupancy.items; receipt evidence stores no competing balance.

## Test evidence

Before production-code changes: 11 targeted real-Mongo cases ran, 9 FAIL and 2 PASS (37 unrelated cases deselected in that narrow probe). Failures demonstrate valid preparation/adoption rejection and missing-configuration acceptance. The original diagnostic archives are unchanged.
New acceptance cases cover all three contracts, partial historical proof, explicit conflicting historical facts, negative state vetoes, quantity limits, receipt alias formulas, post-reservation invalidation and variant aliases.
Local integrated seven-suite run:168 PASS, zero fail/error/skip. Final narrow alias regression:4 PASS,94 deselected (targeted run only).
Earlier exploratory runs exposed incomplete synthetic fixtures: original component fixture lacked receipt quantity/configuration; reader-gap fixture used an invented product key. These now contain writer-contract proof; old assertions remain. Two new invalidation tests initially had fixture writes_paused=true and failed at the owner gate; corrected only that isolated fixture state. One exploratory local component-suite run skipped its optional standalone test because that fixture URI was absent. Final CI includes an isolated standalone fixture and forbids any skip.

Final complete GitHub results and exact HEAD/TREE are recorded in the PR/Issue #1006 handoff after this commit. The unchanged dedicated Workflow executes every case in all eight suites, including transactional component crash/replay and concurrency tests, with no deselection. No local success is substituted for CI.

## Index/query proposal — NOT IMPLEMENTED

Previous isolated Mongo explain dataset:100000 receipts,20000 target-owner receipts,20/location. Current index median actual-loader times at10/100/1000 locations:116.15/125.62/1452.65ms; examined docs20000/20000/200000. Candidate(user_id,location_id) index automatic selection:126.21/129.63/605.01ms. Test-only forced candidate:8.83/51.05/538.87ms and200/2000/20000 examined docs. Planner sometimes retained old owner/posted index. These are synthetic observations, not Production benchmarks.
Separate proposal: review compound index deployment, verify explain after installation, and review bounded receipt-ID targeting to avoid historical-volume growth; retain owner identity, adoption witnesses and current negative evidence. Do not add a permanent hint without approval. Current loader still batches100 locations and rejects >20000 receipt records per batch. No query/index change in this patch.

## Remaining boundaries

operational_inventory_projection.py still needs separately approved evidence-aware available/held reporting. Physical receipt writer and110-piece cycle remain unimplemented. Pending financial valuation versus opening/G47 guard behavior still requires independent design approval; this patch gives no accounting authority. Production integration remains a separate explicit review; no merge from #1271 or Production was attempted.
GitHub test environment explicitly disables MEZAN_SALLA_BRANCH_INVENTORY_SYNC_ENABLED. Tests invoke read paths only and verify absent or unchanged cost/ledger fixtures. No live Salla call, application server restart, release intent, lease, deploy or Production business write.
Production business writes = 0.

Additional boundary check: adopted component quantities use exact Decimal bounds; 0.1 + 0.2 remains eligible against a 0.3 opening witness. Three focused quantity-bound cases PASS. No cost calculation or valuation was changed.
