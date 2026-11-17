# Post-release availability and balances latency — 18 November 2026

## Merged-release checks accepted — 17 November 2026, 22:55 UTC

All **11 checks on actual merge M
`f2d806efc18964a2cc7c3a475346590cbe8b1262` are complete and successful**, with
zero pending, failed or skipped merge checks. Root freshly read the full check
inventory after independent inspection of completed logs and both actual host
rehearsal artifacts. The accepted M tree remains exactly reviewed B's tree.

Completed workflows: Readiness44193306056, Security44193305930 and
CodeQL44193305929. Both Node20.20.2/job134428558258 and
Node24.19.0/job134428558347 rehearsals prove the configured adapter, no-Git
materialization, governed Node22.23.2/Yarn1.22.22, isolated packages and exact
canonical/cache-busted JSON metadata. Both complete CodeQL analyses uploaded
and processed results successfully. The explicit no-new-alerts differential
verdict belongs to the accepted exact-B PR aggregate, whose tree equals M;
M's push checks contain no separate PR-differential aggregate.

The complete sanitized record is `evidence/accepted-merge-ci.json`, retained
byte-for-byte from the independent reviewer, SHA256
`52d1c14b0e8d87ad1772e94a5dcd0d91780be0df8357190030dd1925e183b177`.
Backend package digest:
`249b2fda1fb2e4d9084355fb8c185868425abc93c3092b1bc0b49ca971b3937f`;
Frontend package digest:
`641d6b159c3a25f278c126fe1827c6115052efef2126ed1ec7fd9986ac016c10`.
The runtime, source, metadata and reproducibility-proof identities remain those
of the exact frozen candidate below.

The preceding Android owner records one official verification started at
22:46:11 UTC, verifier PID104351, with final acceptance and lease closure still
pending. Do not issue another verifier or a status request while that operation
holds its lock. Root has recovered an independent CodeServer tab and Terminal7;
only `pwd` was executed there. No shared source, service, release lease or live
publication has been changed by this task. Next: wait for the owner's three
successful probes and inactive handoff, freshly check official inactive status,
preserve unrelated files, then run the actual configured adapter on exact M.

The prior durable evidence checkpoint is
`3d1246edfa9de91be63fd89bb9b56dc76d2862e6` on
`codex/balances-latency-final-evidence-20261118`. This branch remains evidence
only and is not deployable. CI acceptance is complete; hosted performance is
still unverified.

## Current accepted final candidate — 17 November 2026, 22:47 UTC

[PR1980](https://github.com/AMASI-SA/AMASI-SA/pull/1980) was merged normally at
22:46:33 UTC as `f2d806efc18964a2cc7c3a475346590cbe8b1262`.
The accepted source A and intent-only B were not rewritten. This is a completed
Git merge; this incident task has **not started its own live publication**.
No hosted speed improvement is accepted yet.

| Identity | Exact value |
| --- | --- |
| Reviewed production base | `b009ee50a6b932368be8ad104c8c1b75e584b4e2` |
| Source A | `a48657447dfb5bff48013ece455b745920403458` |
| Source A tree | `6d8c4218f5a7e567f32bb27ba49b8461c67194b3` |
| Intent-only B | `89690ba6fbff16d54b01a101e0c0de58803f2cab` |
| B and actual merge tree | `cc07de471d47eab2dc0a2e695fb54fe4a302b444` |
| Actual ordinary merge M | `f2d806efc18964a2cc7c3a475346590cbe8b1262` |
| Runtime release ID | `rg5-5903cd2b08dd34d72afa632fce3c189767c18b4428bb9e403cc45d25b20543fa` |
| Frozen intent SHA256 | `47a061752182ec37622935afa1582f4b6b5b38b640dd530f31c2b796758edf2c` |

B's only parent is A, and A-to-B changes only
`release/release-intent-v5.json`. The reviewed base is an ancestor of A.
The actual merge's ordered parents are the reviewed base and B; its tree is
exactly B's tree. Fresh GitHub reads confirmed these identities, the merged PR,
and no issue comments, inline review comments or review submissions. The exact
deployable task branch remains `codex/balances-latency-final-20261118`.

### Exact-B CI acceptance

The final recorded snapshot has **35 completed checks: 25 successful and
10 intentional skips, with no pending or failed checks**. The first acceptance
snapshot had 29 checks (23 successful, six intentional skips). Moving the PR to
ready added two successful Snapchat scope guards and four intentionally skipped
out-of-scope jobs without changing B. There are 14 workflow runs: 13 successful
runs covering 11 distinct applicable workflows, and one intentionally skipped
TikTok workflow. Every recorded run has attempt 1; this verification did not
request any retry.

The skipped jobs are explained by the exact workflow conditions: Snapchat
settings/publication paths are outside this patch, the TikTok candidate requires
a different head branch, and the manual Emergent redeploy notice runs only on a
push. None of the required balances acceptance cases was skipped.

[Operational Balance Web 44189042475](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44189042475)
passed all four jobs. Seven downloaded CI artifact ZIPs were independently
hashed and parsed; all seven hashes match GitHub artifact metadata.

| Evidence | Direct result |
| --- | --- |
| Backend job 134413387799 | 5,163 actual JUnit cases; zero failures, errors or skipped cases |
| Nine required financial/permission modules | 194 cases, all passed; module counts are retained in the JSON record |
| Separate shipping preservation step | 16 passed plus 18 passed subtests |
| Frontend job 134413388141 | 62 suites, 1,000 passed; zero failed or pending tests |
| Canonical worker job 134413388030 | Four passed; zero failures, errors or skips; exact B/tree recorded |
| Browser job 134413388150 | 14 operational and eight simple-balances scenarios passed; no browser errors; synthetic fixtures |

The actual
[backend artifact 14375967971](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44189042475/artifacts/14375967971)
has SHA256
`ab93ca3867999c78aaf43ee082398ce3a44b5093bbd824329ba1199a6c5a985c`.
Its source files identify B and the B tree above, and its XML suite count equals
the actual number of test-case elements. The job's missing/skipped-test guard
also passed.

Actual backend logs show the isolated Mongo 8.0.12 replica set starting,
successful ping and initiation, and the mandatory writable-primary check passing
before pytest. `OPERATIONAL_TEST_MONGO_URI` was set for the actual pytest step.
At exact B, the workspace and integration database helpers use Motor and ping
that local server. Database-backed scenarios therefore exercised real isolated
Mongo; individual pure unit cases do not necessarily access Mongo. The previous
local Mongo CPU-discovery limitation did not apply to this CI acceptance.

The official
[CodeQL check 134414440085](https://github.com/AMASI-SA/AMASI-SA/runs/134414440085)
reports **“No new alerts in code changed by this pull request”**, with zero
annotations. Both language jobs and the Security Gate passed. This does not
claim that all historical repository alerts are absent.

### Governed adapter and package acceptance

[Mezan Release Readiness 44189042673](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44189042673)
passed both clean-clone host rehearsals and all their stages:

| Host | Job | Rehearsal evidence artifact | Artifact SHA256 |
| --- | --- | --- | --- |
| Node 20.20.2 | 134413887176 | 14375493173 | `d7f7d4da489cd7eded371550cd035312a4c7102613b14252b46bc464b431395b` |
| Node 24.19.0 | 134413887219 | 14376266242 | `5fd243e1ca5b43b1d3ad9f704615c2c097682de20766f61d41ab39c47886822c` |

Both actual adapter build logs identify the same accepted A/runtime, host Yarn
1.22.22, and absent Git metadata during the adapter invocation. The separate
initial environment JSON was captured before Git metadata removal and correctly
reports Git present at that earlier stage; it is not the no-Git invocation proof.

Both hosts produced identical package proofs: isolated Backend health/identity
verified without Git or a sibling Frontend package; isolated Frontend artifacts
verified; canonical and cache-busted build-metadata routes returned exact JSON
bytes with `application/json`; production and Preview host cases passed. These
are GitHub-hosted rehearsals, not observations of the actual Emergent package
snapshot or a live deployment of this incident fix.

The existing root integration XML above b009 independently records **140 passed,
zero failures/errors/skips**, exit 0, 53.10 seconds as recorded by root. It was
read and retained unchanged for this checkpoint; tests were not rerun.
The reviewed balances source and test hashes remain unchanged from the original
accepted fix.

### Publication handoff and durable evidence

Immediately before merging PR1980, root observed the preceding Android
shipping-label publication finish in the native UI at 01:42 Asia/Riyadh, version
`dd9f327`. Root's public-health observation at 22:45:43 UTC identified source
`7f6bc85333b1189e40dd5f1138b841cf2241ee23` and runtime
`rg5-c13e7f152e61e9ba1f799a1a78744bb998e3cdfb8be7cfdc09f97886868fa17e`,
with all identity-verification flags true. This is the preceding publisher's
release, not this fix. Its owner's official three-probe verification and lease
closure remain pending; **do not assume that lease is inactive**.

No shared `/app` source, service, lease or live publication was changed by this
incident task. The evidence reviewer made no shared-`/app`, status, UI or live
calls. The current permitted next boundary is for the prior owner to complete
official verification and lease closure. Before any shared workspace change or
release rehearsal, root must read official guard status, require
`active=false`, and revalidate current production Git state. Do not publish
while another owner holds the lease. A later authorized publication still needs
the newer explicit deployment-success result and the official three consecutive
identity probes before it can be accepted.

Complete machine-readable evidence, full artifact hashes, check/run IDs, exact
ancestry, module counts and limitations are in
`evidence/accepted-final-intent-b.json`. This status and that new record are the
only changes on `codex/balances-latency-final-evidence-20261118`, based exactly
on B. This is a documentation checkpoint branch and **must not be deployed or
merged into the frozen candidate**. Root will checkpoint it remotely through the
approved GitHub path. Historical evidence files remain byte-for-byte unchanged.

PR1976 is closed unmerged and superseded; its original A/B and evidence branch
remain preserved. PR1968 is a separate, unmerged bulk-permissions task and was
not modified. Issue1006 has reached its comment limit, so the task status,
evidence branch and task PR carry continuation.

The benchmark and browser fixtures are synthetic. No real transaction, provider
recovery, sync, backfill or advertising write was used for verification. The
source repair removes demonstrated repeated work, but hosted performance and
the cause of the earlier whole-system resource spike remain separate questions.

## Integration checkpoint above the newer production base (historical)

The following sections preserve earlier diagnosis and integration evidence.
Their historical pending states are superseded by the exact final checkpoint
above.


The accepted original source A86452cd1a82ae9528b89c51edbc0ee86b2984bd3 and
intent-only B6c75c3edf131d1a39155d245604860dc66d02266 remain preserved in PR1976.
All11 applicable exact-B workflows passed, including both governed host adapter
rehearsals,5163 backend cases with zero failures/errors/skips,1000 frontend
cases, and the official CodeQL no-new-alerts verdict. The complete accepted
handoff is preserved on evidence branch
`codex/post-release-latency-evidence-20261118`, commit
8ad0939142fafc0f54d0a84a113a733e2036caab. That evidence branch is not deployable.

While the previous publisher completed its official3probe verification and
restored Preview backend, a separate Android shipping-label PR1974 was merged
into production as b009ee50a6b932368be8ad104c8c1b75e584b4e2. Its source is
7f6bc85333b1189e40dd5f1138b841cf2241ee23 and its complete reviewed tree is
129beb5d3e5121a9f2ed3a8130dc26a3f5cee8d8. It changes fulfillment routes and their
shipping tests/workflow; it does not overlap the two owned balances files.

A new candidate is being built directly above b009 on
`codex/balances-latency-final-20261118`. It copies the accepted balances source,
tests and existing incident evidence exactly, while preserving all Android
source and the new base's tracked intent. The older A/B are not rewritten or
merged across a changed source base. Fresh integrated verification and a new
source/intent pair are required. No source, lease, service or publication in
shared /app has been changed by this incident task.

Fresh root integration verification on b009 completed: **140 passed, zero
failures/errors/skips, exit0,53.10seconds**. The same six-module affected matrix
ran with dotenv and bytecode writes disabled, explicit backend/test PYTHONPATH,
--noconftest and --asyncio-mode=auto. Every case is retained in
`evidence/root-integrated-b009.xml`. An independent integration review confirmed
all six b009 Android paths, including its intent, are byte-identical; accepted
balances source/test hashes and all eight earlier evidence files match the
original accepted source exactly. Only the task status and fresh result were
added for this integration. At that checkpoint, fresh exact final-candidate CI
remained pending; the current exact-B acceptance above supersedes that state.

The full staged whitespace check identified a pre-existing trailing-space line
in the retained failing pytest XML `evidence/workspace-projected-red.xml`.
That historical RED evidence is deliberately preserved byte-for-byte. The
source/documentation/new-evidence whitespace check excludes only this exact
historical XML path and passes; no implementation or release rule was altered.


## Original diagnosis checkpoint (historical)

The owner reports that Mezan became slow and then appeared frozen after the
latest publication, and separately confirms that the balances application is
very heavy. Diagnose the actual production incident and repair demonstrated
balances request amplification. Financial amounts, access controls, source
evidence, event precedence, pagination and cancellation ownership must remain
unchanged. No real financial transaction, provider recovery, sync or backfill is
part of verification.

Repository: `AMASI-SA/AMASI-SA`.
Task branch: `codex/post-release-latency-20261118`.
Initially verified production merge/base: `d7c9ed77137bed0948f4d32a34c6bf571d75e6f8`.
Published source A: `d9964df9f07cee22806202311712d92e6bd717ba`.
Published runtime: `rg5-6ebdaece1a49b074f899db8024e68a790da8cc641b66206a2e4abd18d4be6d9b`.
The source checkpoint and PR are recorded in the task PR after creation.

During diagnosis, the production branch independently advanced to
`b92d3eb8d12920663a052744c9c0d2773a785e1a` (PR1972, bounded Qoyod manual sends).
The native administration UI showed a new publication beginning around 00:32
Riyadh. This task did not prepare or initiate that release. Its work must be
integrated above the new reviewed base before a new release intent is frozen.
The task checkout was fast-forwarded to that base with the reviewed task files
byte-identical. At 21:40 UTC the public health route still identified the earlier
d996 source.

Production changed by this incident task: **no**. No release lease, deployment,
rollback, resource-plan change, live data write or background-job change was
performed. PR1968 remains a separate, unmerged bulk-permissions task.

## Direct observations and limits

- Public `/api/health` returns HTTP200 and identifies the source/runtime above;
  verified identity, critical source hashes and frontend build are all true.
- Emergent's live Monitoring page displayed CPU99%, response time2.4s and
  error rate8%; memory was54% with a98% peak. These are displayed platform
  metrics, not a measured breakdown by endpoint or process. A later display
  showed response time1.7s.
- The platform issued a resource-pressure alert at17 November19:19UTC
  (22:19Asia/Riyadh), before the latest publication around18 November00:00
  Riyadh. Pressure therefore predates that last courier release; this does not
  exonerate earlier releases or prove which request caused the current spike.
- Production runtime logs around17 November21:26UTC showed successful light
  requests: `/health`9.408ms, `/api/orders-v2`282.665ms, notification count37.910ms,
  and order-instruction19.670ms. They demonstrate that the service responds at
  those moments, not that every route or the user's earlier incident is solved.
- A single ordinary signed-in opening of the daily-movements page completed.
  No precise navigation duration was captured; do not derive one from tool-call
  elapsed times.
- Executor curl samples took6–8s overall, but5.8–7.8s were spent establishing
  TLS through its local proxy. Time from request-ready to first byte was about
  0.25–0.57s. The total executor times are not backend execution measurements.
- CodeServer is the Preview environment, separate from production. Its quiet
  processes cannot be used to explain production CPU. The existing internal
  diagnostics credential is unavailable in its configured environment; no
  diagnostic-token request was sent, no credential was exposed or introduced.
- Browser DevTools are organization-disabled; that restriction was respected.

## Demonstrated source defect

The already merged simple-balances workspace builds the full saved-state report
and resource catalog for each context/parties/ledger/movements read. Its daily
movements projection then scans the complete openings, obligations, movements
and returns for every resource, before returning five rows. The projection had
no cooperative cancellation checkpoints, so a disconnected reader could leave
its owned thread doing unnecessary work until all scans completed.

The bounded regression through the actual module and ASGI route failed before
the repair:500 synthetic movements were visited22,500times over45 resource
passes for a five-row result. A separate cancellation regression failed because
the stopped operation did not raise cancellation. These are deterministic local
operation counts, not a live incident attribution or a production speed claim.

PR1970 introduces no new global polling or balance background worker. Courier
matching remains an explicit request-owned action. Source inspection cannot
exclude its increased bounded work during provider failures as one contributor
without live request/stage correlation. The sorted retry claim's production
query plan has not been measured and has not been changed speculatively.

## Confined repair and acceptance

The candidate groups ledger inputs once per request by kind, identity and
currency, preserving source order. Both the primary party and cash counterpart
are associated once. Existing row filters, monetary formulas, refund evidence
validation, first-event precedence, redaction and post-computation permission
revalidation remain authoritative. Cooperative checkpoints are added to
indexing, ledger traversal, event projection and focused-page lookup.

The read-only workspace now also uses the existing official financial-position
snapshot reader. It preserves the complete financial packet and decoded issues,
including provider refund evidence, while omitting audit/idempotency records
which these projections never consume. It validates financial packet integrity,
owner/revision binding and archive-manifest metadata. Historical audit-page
contents remain the responsibility of the unchanged full/audit reader. Thus a
valid financial snapshot remains viewable if an unrelated historical audit page
is missing, while full audit reads and writers still reject that missing page.

Only `backend/operational_balance_workspace.py`, its existing test module and
this task's status/evidence are changed. No cross-request cache, dependency,
worker cadence, persisted-state or financial-policy change is included.

Both original RED regressions are now GREEN. The second actual-ASGI regression
compares five workspace responses against the full-store reference for a packed
financial snapshot containing eight audit pages. Before the reader change it
loads40audit pages; afterward it loads zero, with exact JSON parity. Corrupt
financial packet/hash/marker and corrupt archive-manifest cases still fail.
Provider-refund proof and persisted state are preserved.

Root's fresh complete workspace execution passed 32 tests, exit 0, in 13.13 s:

```bash
PYTHON_DOTENV_DISABLED=1 PYTHONPATH=backend \
  /workspace/scratch/81f2839b514c/courier-test-env/bin/python -m pytest \
  --noconftest -q backend/tests/test_operational_balance_workspace.py \
  --junitxml=docs/operations/POST-RELEASE-LATENCY-20261118/evidence/root-workspace-final.xml
```

After integrating production base b92, root reran the complete affected local
matrix: **140 passed, zero failed/skipped, exit 0, 40.67 s**. The matrix covers
workspace, refunds, pending commands, financial-position state, snapshot
encoding and archive-read lifetime. `evidence/root-integrated-final.xml` records
every case. It used the same isolated Python environment, dotenv disabled and
`--noconftest --asyncio-mode=auto`; no live database or provider was contacted.

The retained six-run counterbalanced synthetic ASGI benchmark uses 250 suppliers,
6,000 movements and 16 audit pages of 250 events each. Complete response hashes
match in all runs. Median combined context/movements time is 1,075 ms before and
583 ms after; process CPU time is 1,045 ms before and 584 ms after. Historical
audit-page reads fall from 32 to zero for the two requests. These local results
demonstrate less computation; they do not measure hosted improvement.

Independent source review of both slices found no blocking issue. The final
reviewed source SHA256 is
`8e3c9ea408dc081a69b47081efea50b77b0e749933260f2ca6ddaf37b939a126`;
test SHA256 is
`e91fd011ceebd49be5e02f840c6b080de3daf8d47e4d81b351853c54296be934`.
`git diff --check` passed at that checkpoint. Exact-source official CI and release
were then pending; the current exact-B CI and ordinary merge are recorded above.
Local test success is not hosted performance acceptance.

The attempted broader local matrix has an environment limitation: its real
Mongo fixtures require a local replica set. The available Mongo8.0.12 binary
aborts during CPU discovery in this executor before serving requests. Those
cases must run through the existing Operational Balance Web CI with its
isolated Mongo replica set. No production database is a fallback test fixture.

Remaining source risk: concurrent workspace reads still prepare independent
results; no admission limiter was added speculatively. The current changes
remove demonstrated repeated work. Correlate a residual live spike with actual
requests before proposing a bounded heavy-read admission change. Do not reuse
an owner-only estimate cache for employee results without complete permission
fencing.

## Historical continuation note

Issue1006 has reached its comment limit. Preserve its history; this document and
the task PR are the continuation record. Preserve the original bulk-permissions
checkout and PR1968 unchanged.

The next step at that earlier checkpoint was to preserve the exact integrated
source and tests remotely with a Draft PR and inspect exact-source CI. Both are
now complete for PR1980; follow the current publication handoff above. Continue
correlating the already observed live resource spike with production route/stage
logs. No hosted recovery or performance improvement is accepted until measured
after an authorized, governed release.
