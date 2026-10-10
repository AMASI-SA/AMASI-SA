# Operational physical stock writer — isolated Draft

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

Next: explicit user approval for narrowly scoped inventory-eligibility changes in
fulfillment_v2_routes.py and stock_component_consumption_service.py. After that,
implement inventory-specific restricted transaction profile and phased writer,
then real replica-set lifecycle tests and isolated UI/device UAT. Future final
snapshot opening remains a separately reviewed transition, not per-invoice replay.

PR1317 is Draft and stacked on1271 for review isolation. No changes to1271.
Production business writes=0; no merge/deploy/publication.
