# AWAITING-HISTORY-PERFORMANCE-20261121

State: incident mitigation in progress; partial source fix, NOT ready for release.
Base: e08d0e489eded39961d374e4eb7bfcc0544b6e90.
Branch: codex/awaiting-history-performance-20261121.

The owner reports work stopped following PR2046 and authorizes emergency rollback to the previously published version before repairing and redeploying. The owner additionally requires expensive financial scans to run only when using/opening operational balances or explicitly refreshing them, not as work caused by unrelated pages. No periodic two-hour job has been added. Unified workspace PR2049 is on hold.

## Production observations

- Before rollback, live source e08d0e489eded39961d374e4eb7bfcc0544b6e90, release rg5-45eb8f2d79e0912a1277e1e14816e9d9c5e3a40d0a6d784834c6fbb54996974f (PR2039, includes PR2046).
- Emergent 22:30–23:00 Riyadh monitoring: response 10s, errors16%, CPU93% (peak100%), memory77% (peak100%). Earlier severe intervals also existed, so temporal correlation alone does not prove a sole cause.
- Runtime logs 23:14 Riyadh: assembly/orders106818ms, integrations/overview20065ms, attribution/live-sources17332ms; multiple nginx health upstream timeouts. Direct public health10.870s and login-config11.354s.
- PR2046 added whole-owner financial-history scans to dashboard readers. The history guard scans all facts/obligations/movements synchronously for every250candidatebatch and reads15sourcecollections perbatch. It is request-triggered, not a newly added background scheduler.
- This is a demonstrated performance regression/contributor, not proof that it accounts for all observed production load. Runtime diagnostic credential was unavailable; preview database is not production and was not used as production evidence.

## Partial fix and validation

- Scan the whole financial history once per cohort, outside the event loop, with existing cancellation checkpoints and owned-worker cleanup. Reuse only request-local protected order identities; no cross-request cache of empty evidence, source changes or financial writes.
- Warm synthetic comparison (100000facts/1000candidates, empty mocked source reads,3runs each): before541–550ms total and546–556ms event-loop gap; after191–219ms total and11–16ms gap. Identical1000excluded rows; source readcount remains60. This is synthetic evidence, not production latency.
-203unit tests passed (history/eligibility/cohort);2real-Mongo tests deselected explicitly. Attempted actual Mongo8 startup failed host CPU discovery; Mongo7 failed `open: Operation not permitted`. Full integration run did not complete because local Mongo was unavailable. No test or production data was modified to hide this limitation.

## Emergency rollback

Owner explicitly chose fastest recovery. Emergent confirmation current100thpublish289b0ca -> selected98thpublish2c25f9a was submitted ONCE. UI progressed from Rollingback to BuildingPackage(newversion0ef4d22). Selected version predates PR2046 and temporarily also removes PR2039 code. No database rollback, /app source change, Git production-ref change, foreign lease clear, or paid chat.

The original guard status subsequently returned active:false. Do not prepare or republish the current production branch automatically: its code still contains the incident changes. Await explicit rollback completion and verify actual runtime source, frontend metadata and response times. Expected pre-PR2046 source from the previous reviewed intent: db141c0438edf04aa77ccd1122bc9c5008cf39d6. Confirm against live artifact rather than assuming success.

## Remaining

1. Verify rollback outcome and service recovery; preserve original financial data.
2. Implement the owner's requested on-demand boundary: unrelated dashboards must not trigger heavy ledger scans. Design a bounded, revision/source-bound saved read model or explicit pending state; do not silently certify stale results or omit incurred costs. Current partial fix DOES NOT yet enforce this boundary.
3. Complete real-Mongo and exact-head CI, concurrent request/cancellation/resource tests and production-size profiling before a new release. Do not bundle unpublished PR2049 designs into emergency recovery.
4. Publish any subsequent fix only through a fresh verified v5 A/B release and normal lease checks. Never reuse or clear another task's lease.
