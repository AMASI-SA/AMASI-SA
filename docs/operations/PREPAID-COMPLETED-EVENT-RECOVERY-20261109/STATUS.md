# Prepaid completed-event recovery — WIP, not approved for deployment

Base cedd7158ba1a7fbae13d1503a503c61b19ad7ae6. Scope: prepaid source recognition,
verified completed-event evidence, targeted tests. Deferred shipping defaults and
all existing paid facts remain unchanged. No live source read, financial mutation,
/app operation, merge, lease, deployment or historical reprocessing.

Prepaid order completion previously required both shipment and waybill identities.
Now that prerequisite is optional only for the prepaid caller. Owner, canonical
source, carrier, status-event timestamp, tariff period and cash binding checks remain.
A complete existing label still must not contradict its source. Label-free verified
webhook/history proofs bind the order and carrier, survive a later label, and reject
carrier changes. No date is invented from updated_at; it remains only the source
version bound. Existing order-scoped immutable fact/payment IDs prevent replay.

Intended RED: new eligibility tests8failed/3passed. First implementation broad run
135passed and1environment failure (isolated Mongo unavailable at127.0.0.1:27305),
not a product pass. Additional lifecycle regression added for label arrival and
carrier changes. Final focused run:105passed in2.50s/exit0 (new eligibility, existing prepaid shipping, chronology). New suite named test_operational_balance_prepaid_completed_without_label.py so the existing operational balance CI glob includes it. Deferred cutover suite:30additional cases passed in earlier run; its realMongo case still needs CI because localMongo is unavailable.

## Mandatory release blockers

This source can make previously unposted completed orders eligible on the next
existing worker reconciliation. Therefore it MUST NOT be deployed until a read-only
impact inventory identifies all newly eligible historical orders, amounts, tariff
versions and selected cashboxes, excludes existing facts/manual payment overlaps,
and the reviewed historical processing mechanism prevents implicit bulk catch-up.
Do not change cutover, opening amounts or saved tariffs to avoid this review.
No backfill is authorized by this patch itself. Current schema/source recognition
has no separate reviewed historical allowlist gate: that gate/impact review remains
unimplemented and is a release blocker, not silently assumed safe.

Current production may advance through courier1843; preserve it, rebase only this
unfrozen isolated patch, run integrated financial/realMongo/current courier tests,
then build fresh A/B under V5 only after historical impact requirements are met.
