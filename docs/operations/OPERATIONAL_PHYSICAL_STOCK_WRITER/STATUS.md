# Final contract fix — current phase

Owner approved the three independent-review gaps on a033fa60. Fixed only fulfillment_v2_routes.py plus tests; see FINAL_CONTRACT_FIX.md. Prior broad eligibility completion is superseded by this contract verification. Local proof completed; final immutable CI/HEAD/TREE results recorded on PR #1317 and Issue #1006 after this checkpoint. No writer, projection/index change,110-cycle, accounting or Production action. Stop after final review report.

# Operational physical stock writer — isolated Draft

## Current approved-unblock phase

The owner approved four functional files: fulfillment_v2_routes.py,
stock_component_consumption_service.py, product_inventory_receipt_routes.py and
salla_inventory_sync_routes.py (read-only evidence loading in the last two).
Implementation now adds shared owner-scoped bounded receipt evidence, legacy
adoption proof, separate physical/eligible quantities, reservation identity checks,
transactional revalidation and location CAS fencing. No fifth functional file.

Final integrated verification on implementation e20d5b60d3b583f0205cddb246b9f19bd7d434b4:
136 PASS, 0 FAIL, 0 SKIP, 1 explicitly deselected optional standalone case,
7 subtests PASS, exit 0 in 140.26 seconds. Includes 37 new real-Mongo acceptance
cases, corrected diagnostic regressions and actual component crash/retry lifecycle.
See ELIGIBILITY_FIX_FINAL.md for the exact command, supported transaction boundary,
legacy policy, read-only Salla proof, limitations and next authorized review gate.
The original 12 diagnostic sources are archived unchanged in diagnostic-baseline;
their active tests now assert the corrected result. Separate new acceptance cases
are in backend/tests/test_stock_eligibility_acceptance.py.

No receipt writer, 110-piece cycle, Salla sync, accounting, opening or Production
action. The older stopping-point sections below are historical, superseded by the
four-file approval. Eligibility is verified; physical receipt writer and the
110-piece cycle remain unimplemented and require their separate review.

Base: `b93ec3e53d883a69b18941d53dc507656f6158dd` from `codex/operational-app-20261006`.
This separate branch/PR targets that branch so the review contains only this work,
not the accumulated changes in PR #1271. It is not a Production merge candidate.

Scope: first prove stock eligibility and transactional safety before implementing
a physical writer using existing `warehouse_locations.occupancy.items` authority.
No accounting journals, approved valuation, opening/cutover, fulfillment/order,
preparation, Salla or Production modifications are authorized.

Current phase: pre-implementation replica-set verification. Product reader
`fulfillment_v2_routes._inventory_rows` and component reader
`stock_component_consumption_service._available` do not currently enforce item
condition/receipt confirmation. If isolated tests reproduce this, stop at the
consumer-integration dependency rather than publish an unsafe writer.

Dedicated local test replica set: `operationalphysical`, loopback port 27462;
temporary `operational_physical_test_<uuid>` databases only. Server is independent
of Preview 8135 and all other running Mongo instances.

No API writer implemented yet. Production business writes = 0.

## Verified stopping point

Dedicated replica-set diagnostics: 10 PASS in3.56s, zero final errors/skips.
Six of these reproduce unsafe current eligibility (product/component x
unconfirmed/damaged/quarantine condition); they prove a BLOCKER, not acceptance.
Four validate existing rollback/source allowlist/financial boundaries.
See REVIEW.md for results, initial environment/fixture corrections and exact scope.

No writer exposed. No110-piece acceptance claim. Web/Android physical-writer UAT
not run. Existing Preview8135 untouched; no retry of denied restart command.

Update: the user approved eligibility edits in those two files only, requiring a
stop if a third functional file is necessary. Fresh evidence finds that external
_inventory_rows callers in product_inventory_receipt_routes.py and
salla_inventory_sync_routes.py do not supply current receipt/adoption evidence.
No functional edits were made. See ELIGIBILITY_REVIEW.md for the exact dependency.

Fresh combined diagnostics: 12 PASS in 4.08s (original 10 unchanged, 2 new real-Mongo
receipt-state visibility diagnostics). This proves the remaining blocker, not a
successful fix. No corrected eligibility, lifecycle or 110-piece acceptance claim.

Next: review permission for the two read-only evidence-loading call sites, then
implement and verify the eligibility rule and transactional rechecks. Physical
receipt writer implementation remains a separate subsequent review. No authority
to broaden operational_atomic, financial writers or opening behavior is inferred.

PR1317 is Draft and stacked on1271 for review isolation. No changes to1271.
Production business writes=0; no merge/deploy/publication.
