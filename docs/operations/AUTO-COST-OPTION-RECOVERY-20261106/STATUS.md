# Automatic cost option recovery — WIP

Scope: recover completely absent Salla item selections for existing orders whose
catalog has cost bindings or a composite definition. List Order Items only; no Order Details, provider
writes, price changes, assumed-zero selections, or financial observers.

Observed cause: a known order had empty canonical AND raw item options. Explicit
Mezan refresh fetched List Order Items and restored the selected adult surcharge.
Report raw recovery cannot manufacture evidence absent from both projections.
The legacy light auto-sync explicitly skips existing orders' item acquisition.

Implementation: bounded background worker with durable global lease/cursor,
per-order retry/backoff, four fetches per cycle, connected-owner scoping, strict
native line/product/quantity matching, and compare-before-write under the
existing component owner transaction. Only empty choices are filled; all other
source/cost/status fields remain unchanged. Existing component revision handling
invalidates material operational contracts rather than executing fulfillment.

Local verification: 42 passed, 11 skipped in cost recovery, report identity/raw
recovery, sold report, and source-fence suites. Thirteen new unit/worker tests pass.
Two real Mongo integration cases added to the existing source-fence suite; they
require CI. Local mongod cannot start (storage open: Operation not permitted).

Remaining before readiness:
Independent new-order (20 scanned / two fetches) and historical (80 scanned /
two fetches) lanes tested: a newly inserted order is picked up while a historic
cursor is active; a foreign tenant binding is never reused.

Remaining checks:
- Verify real owner transactions and operational effects in CI.
- Review paused owner behavior, payload bounds and partial-selection scope.
- Production source has concurrent updates; refresh/reconcile before release.
- Freeze v5 A/B intent, required CI, rehearsal, merge/publish and runtime verify.

Production changed: NO. No release lease acquired; other deployments unblocked.
No claim that this prevention is live. This is a recoverable draft checkpoint.

## Integration checkpoint 2026-11-07

All 22 CI workflows passed on 0fef62325c309e1d276427e52fa4bcd1fd01adb4.
Real-Mongo review: 657 passed + 345 subtests; separate source-fence run
37 passed including both new recovery transaction/concurrent-source tests;
acceptance guard 1 passed. No skips/failures in that CI job.

Integrated production a2487c339fce1c3c6f022d96e91d7d892b66f663. Its worker
isolation supersedes the old web-process startup. Recovery now starts/stops
inside background_worker_runtime, not server.py. Added child-readiness and
web-isolation assertions. Exact integrated HEAD needs CI/release validation.
The operational fence deliberately permits local corrections during financial
pause while prohibiting financial writes; recovery uses the same boundary.
