# Automatic cost option recovery — WIP

Scope: recover completely absent Salla item selections for existing orders whose
catalog has options/bindings. List Order Items only; no Order Details, provider
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

Local verification: 41 passed, 11 skipped in cost recovery, report identity/raw
recovery, sold report, and source-fence suites. Twelve new unit/worker tests pass.
Two real Mongo integration cases added to the existing source-fence suite; they
require CI. Local mongod cannot start (storage open: Operation not permitted).

Remaining before readiness:
- Verify independent new-order (20 scanned / two fetches) and historical
  (80 scanned / two fetches) lanes under concurrent arrival and restart.
- Verify real owner transactions and operational effects in CI.
- Review paused owner behavior, payload bounds and partial-selection scope.
- Production source has concurrent updates; refresh/reconcile before release.
- Freeze v5 A/B intent, required CI, rehearsal, merge/publish and runtime verify.

Production changed: NO. No release lease acquired; other deployments unblocked.
No claim that this prevention is live. This is a recoverable draft checkpoint.
