# Ready automatic delivery — work in progress

## Outcome and contract
Ready POST retains authentication, live local owner transaction, piece identity, material evidence and inventory checks. It commits the piece and durable outbox together and returns piece_ready_confirmed. No Salla await occurs on the Ready request. Existing current in_progress eligibility is retained.

New outboxes use version 2. Each claimed attempt reads the provider first. Observed completed is readback only. Only an unambiguous in_progress observation followed by the local material/owner fence permits one status POST with transport replay disabled. Attempt markers and dispatch counts persist before IO. Transient failures retry with bounded backoff and a new GET. Success acknowledgement schedules readback rather than claiming confirmed. A lease recovers crashed workers. There is no shipment creation POST: Salla creates the shipment on completed and Mezan reads the current label. Missing labels eventually expose attention/error. v1 and historical enrollment remain readback-only.

Web UI acknowledges the local save, shows pending progress, polls GET completion every five seconds, and stops polling on confirmed label or attention. Android compatibility is being independently reviewed in the owner's existing Audit Android task. It must not replay Ready after an ambiguous response.

API paths unchanged/added:
- POST /preparation-work-v1/assembly/pieces/{piece_id}/ready — immediate local save response.
- GET /preparation-work-v1/assembly/orders/{order_number}/completion — local persisted progress, no provider wait.
- POST /preparation-work-v1/assembly/orders/{order_number}/completion/resume — authorized manual recovery under the same lease.

## Evidence and limitations
Local Mongo replica set on loopback; synthetic data/provider transport only. No Production or real Salla test writes.

Historical d55b1b86 route baseline with a synthetic twenty-second provider: ordinary .171850s -> .142284s, last20.204936s -> .165525s. New Ready performs zero provider calls. This is one local sample per route, not a production SLA.

Historical b99 search baseline, seven interleaved warm samples: 1piece7.845ms ->11.492ms (small regression);10pieces34.055ms ->9.688ms;50pieces150.027ms ->13.928ms. Instruction query count falls from N to1.

Local canonical race suite21 passed. Worker/safety suite28 passed plus9 subtests; a retired AWB-branch injection failed to execute. The original diagnostic remains unchanged by default; opt-in injection at the current shipment refresh boundary passed all3 counterexamples. Local print suite297 passed/1 intentional skip. After latest Production integration, saved-label/inbox/boundary suites87 passed/1 intentional skip. Fresh full HEAD CI remains mandatory; earlier suite results are not final release acceptance.

## Integration and release
Latest source base2a7740359451a538be8df373266c4c92ca1a57c8 includes the parallel saved-label pilot and the subsequently released payroll changes. Its opt-in saved-label reader and provider courier helper are preserved separately from the strict Ready delivery reader; pilot policy is not silently changed. Supplier performance and financial code are unchanged by this patch.

The old integration merge ancestry triggered the release source-only guard despite unchanged intent bytes. History is retained on codex/ready-auto-history-checkpoint-20261017; the same integrated source tree was rebuilt with latest Production as sole parent. No release guard was weakened. Candidate source-base resolution now passes. No new release intent, merge, deployment or lease has been created.

Before release: resolve all final HEAD CI, verify Android, freeze a fresh governed source/intent pair, prepare an explicit rollback candidate against the then-current Production base, and run all release gates. Rollback must preserve inventory commits/outbox records and unrelated Production changes; reverting code does not undo legitimate completed pieces. Restoring an older worker may leave version2 records pending, so recovery/readback must be assessed before executing rollback. Do not delete or reset records to force retry.

## Accepted residual remote race
Salla does not expose a conditional status write. A remote terminal transition after the last GET/local fence but before POST may still receive completed. The owner explicitly accepted this limited race and repeated completed status delivery. Known terminal observations must never dispatch, and current-label publication remains fenced. This is not an exactly-once or remote atomicity guarantee. No provider idempotency guarantee was independently proven here.

## CI corrections after the payroll release
Prior candidate d058f09d1 passed24/26 workflows. The complete shipping command now passes527tests with1intentional mock transaction skip. Courier tests retain rejected status/cache revocation assertions and test historical readback rather than an unsafe unjournaled automatic POST.

The dashboard gate also failed on untouched Production2a774035: its first response matched the historical oracle, with43find on both Production and this candidate versus41on the old oracle. The gate retains all164historical JSON responses and compares exact read/document/collection evidence to a third immutable Production arm. All three arms passed in GitHub on0b80b6fb; a later source check still expected pre-bundle report bytes. That check now pins the exact deployed2a774035 sold-products report; no report, governor or financial implementation changed. Related regression tests80passed with5subtests locally. A fresh complete CI run is still required.

Android9bddf702 was read without modifying its worktree. Native transport is single-shot and uncertain results reconcile with GET. Its Ready acknowledgement currently discards outbox fields, and the completion progress endpoint was not found in that screen. The designated Android task was sent these exact compatibility findings. Backend latency evidence alone does not prove the complete Android progress/error experience.
