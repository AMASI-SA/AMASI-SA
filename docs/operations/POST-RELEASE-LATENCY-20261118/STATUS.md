# Post-release availability and balances latency — 18 November 2026

## Scope and current state

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
`git diff --check` passes. Exact-source official CI and any release remain
pending; local test success is not hosted performance acceptance.

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

## Continuation

Issue1006 has reached its comment limit. Preserve its history; this document and
the task PR are the continuation record. Preserve the original bulk-permissions
checkout and PR1968 unchanged.

Next: checkpoint the exact integrated source and tests remotely with a Draft PR,
and inspect exact-source CI. Continue
correlating the already observed live resource spike with production route/stage
logs. No hosted recovery or performance improvement is accepted until measured
after an authorized, governed release.
