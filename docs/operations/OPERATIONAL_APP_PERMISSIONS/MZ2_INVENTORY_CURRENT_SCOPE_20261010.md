# Operational inventory current scope — WIP checkpoint, 2026-10-10

Accounting separation is stopped. This checkpoint only extends existing operational purchase invoices and their existing supplier adjustment identity; it creates no warehouse, reservation, valuation or journal writer.

Implemented: canonical MZ2 product choice/text metadata; raw or ready purchase declaration; canonical MZ2 location reference; multiple independently identified configurations per invoice; component without SKU; readable unit costs, labels and location; report search/date filters. New configured lines require a valid location. Old payload fingerprints retain compatibility. Supplier returns target the exact purchase_line_key.

A (operational purchases) and B (existing physical warehouse stock) remain separate. Invoice and lines return physical_stock_status=unproven. Purchase declaration ready is not proof of physical readiness. No new SKU/catalog product is created for a free name.

Fresh verification: Web nine suites 134 PASS; new isolated Mongo cases 12 PASS; focused backend compatibility 75 PASS before exact 10 x 15 sample adjustment (12 rerun PASS afterward). Native purchase 52, adjustments 16, stock reader 13 PASS; full typecheck/verifier chain PASS. Complete backend regression and actual UI/device UAT remain pending; no final acceptance claimed.

Real Mongo sample: before physical stock 3, operational purchase lines absent. Purchase raw gold 10 x 15 + ready silver/Abeer 100 x 20 = 2,150.00; four concurrent identical submits produce one invoice. Return raw 2 (30.00) and ready 1 (20.00) leaves operational 8/99 and payable 2,100.00; physical stock stays 3. Synthetic intermediate failure before CAS leaves state unchanged; retry saves once. No financial cash movement is created by purchase. Existing receipt, reservation and cost collections remain byte-for-byte unchanged in snapshots.

Not implemented / BLOCKED: creating physical warehouse stock, physical raw-ready conversion or reservation writes; product-specific image upload without a verified contract. Existing product/variant pictures remain supported. Unknown conditional option relationships are not inferred. No production connections, Merge, Deploy or publication.

Next: exact-checkpoint complete regression, isolated APK build, actual Web/emulator save/read-back/restart and A/B checks, then final report. Production business writes = 0.

## Final automated verification checkpoint (actual UAT incomplete)

- Tested Backend/Web code HEAD `1837fb54ba88ae9d14536cac56bcc1ee0c5147c1`, TREE `e63eaed9749a945c57efad993ee8b4cd9856a4d7`.
- Built Native HEAD `8a3fa082d45df4233825cf331c2b38f6a70028d0`, TREE `d0a2e547fc9c50897fef2f3740ecaec6172546ff`.
- Full backend `test_operational_balance*.py`: **393 PASS / 0 FAIL / 0 SKIP**, 116.76s, five existing Pydantic deprecation warnings. Disposable isolated Mongo test databases only.
- Web **134 PASS** across nine suites. Native focused **52 + 16 + 13 PASS**, full typecheck/verifier chain PASS.
- APK **BUILD PASS**, version 1.0.11/code14, x86_64, isolated package com.amasi.sa.mezanoperationalpreview; API http://127.0.0.1:8135/api. APK SHA256 `2407410da1e2b8043521361e9bf5848d9fb16bbf73f92c120d0e0f62a8b5ba0a`.
- Actual new-slice Web/emulator UAT: **BLOCKED**, not PASS or skipped acceptance. Execution policy rejected the combined local fixture update/restart command before execution; no reason beyond blocked by policy was supplied. No bypass attempted; requested an approved restart method. Existing fixture must not be represented as running the new tested source.
- Codex Process Jobs was unavailable on Windows (unsupported platform); tests/build ran via ordinary local foreground processes with persisted logs and completed successfully.
- No Merge/Deploy/publication, no production connections/business writes, no accounting or physical warehouse writer changes. Protected receiving/opening/server paths have empty diff against 89065fb.

Recovery: isolated build and pytest logs are in D:/codex-evidence/operational-inventory-current-scope-20261010. Wait for approved fixture update/restart, use dedicated synthetic product/options/location, verify actual save/readback/restart/retry on Web and emulator. The request is not fully complete until that acceptance is performed or explicitly deferred.
