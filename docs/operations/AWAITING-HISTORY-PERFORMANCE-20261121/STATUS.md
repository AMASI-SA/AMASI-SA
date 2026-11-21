# AWAITING-HISTORY-PERFORMANCE-20261121

State: emergency recovery source revision 2; full exact-head CI and fresh release intent required before publication. Earlier PR2051/PR2052 are superseded checkpoints.
Reviewed production base: 88af38f01cf979acbca59b95a95e661828f2306f.
Release branch: codex/awaiting-history-recovery-r2-20261121.

Latest validation: PR2052 frozen B94f9e037c07898ca3546859d28bfd8fee20dded4 passed both Node20/24 release rehearsals, frontend, frozen runtime and security gates. Its stock gate passed5498tests+97subtests but failed four dashboard expectations in test_operational_balance_awaiting_cohort_readers.py that still assumed synchronous financial-history authorization. Publication was blocked, not bypassed. Revision2 updates those expectations to retained costs, explicit review/incomplete profit, zero owner-state reads, and transition back to complete after payment. The two operational-profit tests still require verified unpaid exclusion and preservation of prior shipping costs, unchanged. No runtime source change relative to sourceA f7455ecb7df1332e50af983348f2f208809381ad. Old A/B remain immutable on their original branch; a new A/B release is required.

Original shared guard lastobserved active:false; /app remains88af with no tracked edits. No own lease/merge/new publication. Live remains emergency rollback db141c0438edf04aa77ccd1122bc9c5008cf39d6. Readonly runtime logs20:55UTC showed health9–15ms, product operations56–68ms, reviewcompletion1049ms and no errors among100logs; aggregate Monitoring still includes incident windows, so sustained full recovery is not yet certified. PR2049 remains separate. Owner authorizes publication after corrected tests pass. Next: exact-source CI, fresh reviewed intent, normal merge, shared adapter and official guard publication/verify3.

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
