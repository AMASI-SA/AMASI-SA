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


## Intent checkpoint
All 10 applicable source workflows passed. Real-Mongo job129432631407 ran
272 Android cases plus59subtests, 658 retained cases plus345subtests, and1
acceptance guard; mandatory zero-skips/failures checks passed.
SourceA b954c00b7e837d8832ba1be98f00e079fd1ee9ef.
IntentB 3b5b1527a0158a2ecd228c1bd5203b85e1b36d14, tree3c2a4b002d5605875e54090ce7782a725d339543.
Runtime rg5-a24e66ae8e57e5bb49784372c02fa3c121986c1ad634252c01510d7d3decb580.
Readiness42697353647 artifact13709184161, ZIP SHA256
51ee45178ecc2c7ae00b6ff742e23c768acfde841bb65cb8a303c2287d634651.
Candidate and A-to-B validators exit0. Exact-B CI started. Terminal auth
restored; fresh shared guard inactive, /app at reviewed720194f, only unrelated
untracked files. No merge/publish/lease. Next isolated B adapter rehearsal and
exact-B checks, then recheck prod/guard. Evidence-only branch must not deploy.


## Merge / rehearsal checkpoint
Exact B all10 applicable workflows passed; unrelated TikTok candidate skipped by
its own condition. Real-Mongo again272+658+1cases/404subtests, zero executed skips
or failures. Both Node20/24 clean-clone rehearsals passed in readiness42701577281.
Isolated Emergent adapter initially failed ENOSPC on the9.8GB /root mount.
Moved only this task's clean worktree to /tmp/mezan-aramex-1854, repaired Git
registration, retained the failed log. Same source retry exit0 in67.92seconds,
exact intended rg5 identity. No unrelated files deleted.
Fresh /app guard inactive and HEAD720194f with only known unrelated untracked
files. Normal merge c1259613df33f06c534a8e6081209a90ff31eb65 completed. Shared
/app fast-forward and governed build now started; log
/tmp/aramex1854-shared-adapter.log. No new lease or Publish click yet.
Next require sharedbuildexit0, owned prepare/prepublish, Publish changes once,
new green success, official verify3 and inactive guard. Production runtime
changed by task:NO yet. Keep /app at this merge until release completes.
