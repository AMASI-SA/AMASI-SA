# Operational inventory current scope — WIP checkpoint, 2026-10-10

Accounting separation is stopped. This checkpoint only extends existing operational purchase invoices and their existing supplier adjustment identity; it creates no warehouse, reservation, valuation or journal writer.

Implemented: canonical MZ2 product choice/text metadata; raw or ready purchase declaration; canonical MZ2 location reference; multiple independently identified configurations per invoice; component without SKU; readable unit costs, labels and location; report search/date filters. New configured lines require a valid location. Old payload fingerprints retain compatibility. Supplier returns target the exact purchase_line_key.

A (operational purchases) and B (existing physical warehouse stock) remain separate. Invoice and lines return physical_stock_status=unproven. Purchase declaration ready is not proof of physical readiness. No new SKU/catalog product is created for a free name.

Fresh verification: Web nine suites 134 PASS; new isolated Mongo cases 12 PASS; focused backend compatibility 75 PASS before exact 10 x 15 sample adjustment (12 rerun PASS afterward). Native purchase 52, adjustments 16, stock reader 13 PASS; full typecheck/verifier chain PASS. Complete backend regression and actual UI/device UAT remain pending; no final acceptance claimed.

Real Mongo sample: before physical stock 3, operational purchase lines absent. Purchase raw gold 10 x 15 + ready silver/Abeer 100 x 20 = 2,150.00; four concurrent identical submits produce one invoice. Return raw 2 (30.00) and ready 1 (20.00) leaves operational 8/99 and payable 2,100.00; physical stock stays 3. Synthetic intermediate failure before CAS leaves state unchanged; retry saves once. No financial cash movement is created by purchase. Existing receipt, reservation and cost collections remain byte-for-byte unchanged in snapshots.

Not implemented / BLOCKED: creating physical warehouse stock, physical raw-ready conversion or reservation writes; product-specific image upload without a verified contract. Existing product/variant pictures remain supported. Unknown conditional option relationships are not inferred. No production connections, Merge, Deploy or publication.

Next: exact-checkpoint complete regression, isolated APK build, actual Web/emulator save/read-back/restart and A/B checks, then final report. Production business writes = 0.
