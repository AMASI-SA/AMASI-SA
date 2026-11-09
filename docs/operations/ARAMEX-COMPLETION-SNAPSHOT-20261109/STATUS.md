# External carrier completion snapshot repair

Task branch: codex/aramex-completion-snapshot-20261109
Reviewed Production base: 720194f447a4b33a478faaa29baabfe59c896037 (PR1853).
User authorizes the printing fix and publication. Preserve later Qoyod startup,
report and courier handover releases; no mobile source or APK changes.

## Reproduction and change
Android75 fetch uses /carrier-label/v1; Android76 fetch uses
/carrier-label/v1/complete-and-refresh. The latter acknowledges a completed
status POST, which can publish newer carrier observation clocks while reading
the same official external PDF. The manual fence incorrectly treated that
observation as a changed document. Four deterministic tests failed with
shipping_snapshot_changed before the patch; the old read passed on the same
synthetic Aramex data. This proves a source defect consistent with the report,
not a trace of the user's physical device.

assembly_manual_label_refresh.py now excludes observation time/provenance from
the material comparison for external carriers as it already did for merchant
couriers. Carrier identity, shipment ID/status/tracking/URL, external shipment
freshness, address, raw items, custody, component flags and cancellation remain
fenced. Keep one explicit completed POST and the existing provider lookup,
timeouts and lease cleanup. No automatic retry, polling or new shipment API.

## Evidence
Local six-suite pytest exit0: 149 passed, 64 subtests passed, 88 real-Mongo tests
skipped because no replica set is available locally (not acceptance).
Suites: test_manual_courier_label_refresh_race, test_mobile_assembly_refresh_actions,
test_amasi_shipping_compat, test_shipping_label_current_guard,
test_unified_completed_carrier_label, test_shipping_pending_snapshot_recovery.
New tests: four Aramex status/timing permutations, unchanged old read,
ten actual material-change races, five shipment/freshness fields, and 40
sequential fetches with one GET/one POST per action and no leaked lease.
git diff --check passed. Existing fixture-only captures omit shipping; the
normalizer handles that without weakening captures in production.

## State and next action
Source candidate only. Production changed by this task: NO.
Next create Draft PR, require exact-source real-Mongo CI and readiness,
freeze new intent-only B, require exact-B checks. Recheck Production head and
shared guard; never alter another release lease. Normal merge and governed
rehearsal before owned prepare/prepublish, one Publish changes, newer success,
official three-probe verify and inactive guard. Do not claim physical Android
or printer acceptance: ADB exists on the user's Windows PC, not this runtime.
