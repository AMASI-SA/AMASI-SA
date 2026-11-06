# Assembly completion delivery — 2026-11-07

Task branch: `codex/fulfillment-completion-hotfix-20261106` in AMASI-SA/AMASI-SA.
Base: `3bd31d56a93ce30f43d514008f545eb2ca3563c3`.

Owner requested an urgent minimal fix and publication because finishing the last
assembly piece appeared not to advance Salla to completed. Production read-only
inspection established that the assembly worker is running and status dispatch
lagged local completion by approximately 45–128 seconds in the reported examples.
One outbox subsequently rejected a source fingerprint after dispatch. Its workflow
fingerprint was unchanged; the exact source difference is not established. Do not
disable evidence checks or replay Ready to recover that record.

The queue awaited each entire delivery sequentially, including provider/label
waits up to 90 seconds, and stopped polling new work until that batch finished.
The source fix retains at most four active delivery tasks, polls available slots
every five seconds, and orders first attempts before older retries. A committed
last-piece Ready also sends a dedicated assembly wake message through the existing
parent/worker pipe, interrupting that polling interval without waking financial
jobs. Old readback/label retries cannot occupy the final first-attempt slot. Ready
still waits only for the local commit and bounded pipe drain, not provider IO.
Existing
durable claims, provider read-before-write, source/component checks, attempt
journal and label proof remain unchanged. Shutdown cancels and joins child tasks.

Fresh local evidence uses only an isolated loopback Mongo replica set:

- Existing delivery baseline: 15 passed in 116.836 seconds.
- New scheduler regression before change: 1 failure and 2 timeouts. A blocked
  provider prevented new completion dispatch; old retries excluded the new order.
- After change: all 3 scheduler tests passed in 0.450 seconds. They cover arrival
  during a blocked provider, four-task bound and shutdown, and first-attempt order.
- Owner emphasized immediate dispatch while retaining fast Ready acknowledgement.
  Added targeted wakeup and saturated-label regressions; both initially failed,
  and all 5 scheduler tests then passed in 0.792 seconds.
- `git diff --check`: passed. Wider affected integration validation is pending.

Changed source: assembly delivery/Ready route and the existing managed-worker
process, pipe reader and task composition. Regressions cover scheduling, real pipe
isolation and the last-piece post-commit boundary.

Production changed by this task: **no**. No business data/provider write, lease,
intent freeze, merge or publication has occurred. `/app` latest checked guard was
inactive and tracked files clean. Unrelated local Qoyod work and Emergent files are
preserved. This checkpoint is not release acceptance.

Next: run affected completion/status integration suites, inspect final diff and
CI; freeze a new v5 source/intent pair, rehearse on Linux, then use fresh production
fetch and the prepare/prepublish gates before the authorized publication. Require
new Deployment Succeeded and three matching official verification probes. Existing
source-fingerprint failures require fresh evidence and remain excluded from any
automatic repair or data migration in this scheduling patch.


## Accepted source and reviewed intent checkpoint

Release PR: https://github.com/AMASI-SA/AMASI-SA/pull/1792 (ready, unmerged).
Source A: 6ffa622c04f2359dc99f501d16e13c942ab5b1d0.
Intent-only B: bcb4d10e08b1dd80e126ea975a64e90f795b9c34.
A..B changes only release/release-intent-v5.json. Runtime identity:
rg5-75858ae3554d19d11fe40214537a2f02eec0292f6b73e9c6e5e2e8d451155873.
Candidate artifact SHA256:
21347861bd19309a504adc38d400708a16384c8e141bd433acd13c75c890eb8d.

Final affected isolated-Mongo validation: 32 tests passed in 180.026 seconds,
exit 0, including post-commit Ready wake and actual child-pipe isolation.
Exact B CI: 34 successful / 10 not-applicable skipped / no pending or failed.
Readiness run42300441669 passed, including clean-clone governed adapter rehearsals
under both Host Node20.20.2 and24.19.0. Original readiness42299903849 generated the
candidate intent; exact source/base and six critical Git blob hashes matched.
The owned local Mongo fixture process has been stopped after verification.

Shared release is still owned by android75-selected-groups-20261107:
deployment3bd31d56a93ce30f43d514008f545eb2ca3563c3,
sourceebb69057d25d6578ae0b28eb4e6aab1ab71f44e3,
runtime rg5-03f70cc373f6f22c5b7b904ac6de8278d736978c30b6ce74a8094d4dba9ff96c.
Its official detached verification is running. Our read-only guard status is
waiting on that operation lock. Do not clear it or start another release.
Production changed by THIS task: no. No live business/provider write.

Next: observe inactive guard after the owner's proof, fetch fresh Production and
confirm unchanged base, normal-merge1792 preserving the reviewed B tree, ff /app,
materialize and verify the governed build, prepare/prepublish, publish and obtain
the new platform success plus official verify3/statusinactive.

This document's continuation is on the dedicated evidence branch
codex/assembly-completion-release-evidence-20261107. It is documentation-only and
must not be deployed or merged into the frozen A/B release pair.


## Publication started — 2026-11-06 22:14 UTC

PR1792 merged normally as a2487c339fce1c3c6f022d96e91d7d892b66f663;
its tree exactly equals reviewed B. Previous Android75 publisher finished its
official3-probe proof and closed its lease before our app update. App fast-forward
preserved unrelated files. Preview backend is temporarily stopped for packaging.

Initial app adapter refused prepackaged backend/__pycache__. Only generated,
untracked bytecode was preserved intact outside the governed roots at
/app/.release-preserved/assembly-completion-bytecode-c4ecdcb313fd465b8bb50f9252872bb8.
Rebuild with PYTHONDONTWRITEBYTECODE=1 passed completely; no source/intent change.
Use python-B for later guard commands to avoid regenerating packaged bytecode.

Prepare passed with actorcodex-assembly-completion-20261107 at
2026-11-06T22:12:52.251970+00:00, source6ffa622c04f2359dc99f501d16e13c942ab5b1d0,
deploymenta2487c339fce1c3c6f022d96e91d7d892b66f663, runtime
rg5-75858ae3554d19d11fe40214537a2f02eec0292f6b73e9c6e5e2e8d451155873.
Both prepublish gates passed, including the second immediately before the final
Publish changes click. Clicked exactly once. Emergent visibly shows Publishing,
Deployment Started Nov7 01:14Riyadh, then Workspace ready / Packaging latest changes.
Deployment success and actual new Production revision are not yet verified.

Our exact lease remains active. Do not prepare another release, clear this lease,
or alter app while publication/proof runs. No live business/provider test write.
Next observe a new explicit successful deployment, run the unchanged official
verify--urlhttps://mezansalla.com (three rounds internally), require exact source,
runtime identity and critical/Frontend proof and inactive status. Restore only the
Preview backend we stopped, after checking no successor lease and unchanged appHEAD.



## New platform completion and official proof running

Emergent shows a new GREEN Deployment finished at Nov7 01:24Riyadh and a new
published version292fa73 (previous374bf08). The current platform calls its success
marker Deployment finished; this was observed visually, not inferred from an
enabled Republish button. Screenshot saved locally at
D:/codex-test-runtime/assembly-completion-20261107/deployment-finished.jpg.

Initial public /api/health returns the intended source6ffa622c04f2359dc99f501d16e13c942ab5b1d0
and runtime rg5-75858ae3554d19d11fe40214537a2f02eec0292f6b73e9c6e5e2e8d451155873;
verified identity, all critical hashes, Backend runtime source and embedded
Frontend proof all true. Boot2026-11-07T01:24:05.384631+03:00. This single read is
not the required three-round public Frontend/replica proof.

Started unchanged official guard verify--urlhttps://mezansalla.com exactly once,
detached, at2026-11-06T22:26:10.678754+00:00. Parent shellPID83534.
Job directory /root/assembly-completion-proof-75858ae3554d; files job.json,
official-verification.json, error.log, launcher.log and exit-code (appears only
after official process exits). A separate read-only guard status waits on the
same operation lock and writes post-status.json after verification releases it.
No duplicate official verify. Launcher SHA256
8244ba215793bb9e3e950638cc6710310b90eea8561f05253f52ca61d6813955.

Next read this existing job only. Require exit-code0, verifiedtrue, checks3, exact
source/deployment/runtime identities and post-statusactivefalse. Then restore the
Preview backend only after fresh inactive status and unchanged appHEAD. Do not
start another verify or publication while this job is alive. No live business,
financial, advertising, stock or Ready test write by this task. Release acceptance
remains pending the official result; our lease is still active until it passes.

