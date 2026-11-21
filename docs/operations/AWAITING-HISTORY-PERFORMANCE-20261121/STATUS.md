# AWAITING-HISTORY-PERFORMANCE-20261121

## Current recovery release (supersedes historical checkpoints below)

PR2052 https://github.com/AMASI-SA/AMASI-SA/pull/2052 is DRAFT, exact reviewed intent B94f9e037c07898ca3546859d28bfd8fee20dded4. Source A f7455ecb7df1332e50af983348f2f208809381ad, base J88af38f01cf979acbca59b95a95e661828f2306f, B tree67694b8247b4c15da98c89fb83d8a17607301f75. Release rg5-80f3644dbd92e68f8077f293a32568e5d5874eb5057bd669a7079bb3a5971fc4. Source and frozen intent are immutable; this separate evidence branch must never be deployed.

Both dashboard readers now defer the owner-wide history scan and explicitly mark unchecked waiting orders incomplete, retaining prior costs. Full history checks remain in operational balance calculation, once per cohort and off event loop with cancellation checkpoints. No periodic background job is added. This does not provide a compact saved history projection yet. COD policy and source order statuses are unchanged.

272focused local tests passed; two realMongo tests could not run locally. Exact A operational frontend1258tests passed; frozen runtime comparisons and368regressions+5subtests passed. A's remaining backend jobs were superseded/cancelled when B started; do not call all A checks passed. All required exact B checks and both host adapter rehearsals must pass before normal merge/publication.

Artifact14724576263/run44948088722 independently verified: ZIP SHA256ad3653193290971a34e2143fe911a512a3bddac8369c2158d59e4b55541c5664; raw intent SHA256bb145da6d680e65cd9451967b9554f4477edbdf65e35f1ceb34fc03cc5189bda; original load_candidate_release_intent and load_release_intent PASS with all source/runtime bindings. No own lease, merge or new publication yet. Last shared original guard active:false; /app88af clean tracked. /app volume45MB free; use /tmp226GB free for isolated build and preserve existing files. Owner renewed publication authorization. Live remains verified emergency rollback db141c0438edf04aa77ccd1122bc9c5008cf39d6 / rg5-500857bae382758ba238083f1f7f417b23a7e2dfb298aa3e2e99e7f4baf56b45. Latest bounded health0.535s but monitoring aggregate still critical; sustained recovery not proven. PR2049 separate and on hold. Financial acceptance read-only.

Next: exactB gates, fresh production head/lease check, normal merge preservingA/B, actual shared clean-clone adapter, guarded materialization and prepare/prepublish, one Publish, explicit success, original verify3 and active:false, bounded latency/read-only UI acceptance. No unrelated record changes or lease clearing.

## Historical checkpoints


State: rollback identity verified; service latency still unstable; partial source fix NOT ready for release. Draft PR2051.
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

### Observed rollback result, 2026-11-21 around 23:36 Riyadh

- Emergent deployment0ef4d22 stopped showing Publishing and appeared as the current published version. No literal success toast was captured. ConfirmRollback was clicked once only.
- Three bounded, cache-busted health reads through the unchanged release-guard read helper all returned source `db141c0438edf04aa77ccd1122bc9c5008cf39d6`, release `rg5-500857bae382758ba238083f1f7f417b23a7e2dfb298aa3e2e99e7f4baf56b45`, verified identity and frontend build true. Public build-meta returned200 application/json with the same git_sha. These are bounded identity probes, NOT the full 710-requests-per-probe guard verify operation.
- Initial post-rollback health0.567s/login-config2.147s improved over pre-rollback10.870s/11.354s, but subsequent health probes8.511s,10.034s,3.213s show persistent unstable latency. Do NOT claim performance incident resolved or attribute all load solely to PR2046. Actual dashboard browser attachment timed out; no interactive acceptance claimed.
- A concurrent PR2048 release was prepared at20:32:41UTC by actor `codex-aramex-pr2048-authorized`, sourcebc01a705f5bbcb58be8dab5eef35ace8516cface/deployment88af38f01cf979acbca59b95a95e661828f2306f. Latest guard is ACTIVE for that foreign actor; untouched. Latest production Git branch still includes PR2046. Emergency HOLD documented in Issue1006: republishing that branch would undo the rollback. No new publication by this task.
- PR2051 partial code checkpoint88f93f6fdc9def06b44ae826a5a32ae9f956a399 remains unpublished. PR2049 remains on hold. Preserve financial data and concurrent release ownership.

1. Diagnose remaining production pressure after the verified rollback; service recovery is not yet proven. Coordinate concurrent release hold before any new publication; preserve original financial data.
2. Implement the owner's requested on-demand boundary: unrelated dashboards must not trigger heavy ledger scans. Design a bounded, revision/source-bound saved read model or explicit pending state; do not silently certify stale results or omit incurred costs. Current partial fix DOES NOT yet enforce this boundary.
3. Complete real-Mongo and exact-head CI, concurrent request/cancellation/resource tests and production-size profiling before a new release. Do not bundle unpublished PR2049 designs into emergency recovery.
4. Publish any subsequent fix only through a fresh verified v5 A/B release and normal lease checks. Never reuse or clear another task's lease.

## Recovery implementation checkpoint

Owner renewed instruction to complete and publish after rollback. Latest shared guard observed active:false. Production branch base88af38f01cf979acbca59b95a95e661828f2306f is merged into this isolated branch, preserving PR2048. Dashboard and dashboard-v2 now call a pure deferred-history decision instead of scanning financial state or15sourcecollections. Unverified waiting candidates retain their recorded costs and block final profit; UI explicitly says the financial history check is pending in operational balances. This is conservative incident behavior, NOT a cached assertion of no prior money. Operational balance calculation retains the full history guard, now one off-thread scan per cohort. No periodic scheduler was added. Reader version changes invalidate incompatible dashboard caches.

272focused non-Mongo tests PASS,2real-Mongo deselected. Dedicated local Mongo7 attempt failed host permissions; real-Mongo and exact-headCI still required. No new production publication; no financial writes. Full unified workspace PR2049 remains separate from this emergency performance release.
