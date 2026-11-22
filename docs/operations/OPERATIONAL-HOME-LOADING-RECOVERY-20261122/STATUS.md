# Operational home loading recovery — 22 November 2026

**Latest checkpoint, 13:01 UTC / 16:01 Riyadh:** PR #2059 is merged and
published as platform version `d8c03a2`. Original Release Guard verification
passed 3/3 and the lease is inactive. The page renders, but intermittent latency
remains open. Private Production database inspection was denied by automatic
approval review; explicit read-only diagnostic access is the next permission
boundary. See the final section below and `evidence/postpublication-latency.md`.
This continuation branch is evidence-only and must never be deployed.

## Scope and current state

The owner reports that `https://mezansalla.com/operational-balances` remains on
the balances loading screen. Prior explicit authorization to complete and publish
this repair remains active. This is a new follow-up incident after the completed
PR #2058 publication; the prior publication must not be repeated as if unfinished.

Repository: `AMASI-SA/AMASI-SA`.
Task branch: `codex/operational-home-loading-recovery-20261122`.
Verified production base: `1c4008e315c35ec4419e76400a9faddb94b9d5a2`.
Prior deployed source: `a8e655aabbeaef64486a1a80f13551dc97dd9259`.
Prior release: `rg5-9d69963c0ee7f5c299595495519175af5074f703f4e318b82b870b0407554a49`.
Prior completed evidence: `73b2ec5ce57a48e496929d2a9d212afb2cba17a8`,
on `codex/operational-context-release-evidence-20261122`.
The new checkpoint's exact SHA is the containing commit, retrievable from this
task branch; subsequent checkpoints will record its full predecessor explicitly.

Production changed by this incident task: **no**.
New lease, publication, rollback, resource upgrade, financial/provider/sync/backfill
write: **none**. Emergent administration and code-terminal access only; no paid
Emergent agent chat.

## Fresh observations

Observed on 22 November 2026, approximately 11:16–11:29 UTC
(14:16–14:29 Asia/Riyadh):

- Cold live navigation reached the application's unavailable-session recovery
  screen before the finance route could mount. This is an availability signal,
  not evidence that credentials were rejected or that the user's session ended.
- Production branch remains the base above. Emergent's latest displayed completed
  publication is 22 November 06:10 Riyadh, consistent with the previous release.
- Emergent Monitoring displays severe resource pressure over its last-24-hours
  window: CPU 84% with 100% peak, memory 65% with 100% peak, error rate 13% with
  16% peak. These are platform display values, not per-request measurements.
- Production View Logs at 11:27:28–11:27:50 UTC show internal upstream timeouts
  for `GET /health`, recurring at five-second intervals. Application timing logs
  in the same window show health requests around 0.9–1.9 seconds, with one
  32-millisecond sample.
- Other concurrent, independently occurring requests are slow: assembly orders
  reads around 112–114 seconds, a delivery status write around 64 seconds, account
  summary reads around 16–26 seconds, and multiple ordinary delivery/event
  requests around 4–7 seconds. This investigation did not initiate those writes.
  These observations establish broader service pressure, not its specific cause.
- Two local-executor public liveness/readiness samples reached an eight-second
  transport timeout. Executor transport/proxy time is not a backend measurement.

The exact screenshot text comes from the initial workspace-context gate.
Its initial request is
`GET /api/operational-balances/workspace/context?ad_bank_fx=true&provider_positions=true&home_preview=true`.
This route already uses the scoped current financial-state reader and does not
read historical audit-page bodies. PR #2058 repaired the distinct legacy context
route; repeating that change would not address this new home path.

## Source investigation and bounded repair plan

Read-only frontend review identified recoverability gaps in this context gate:
the default visible-read lifecycle has no overall deadline while waiting for a
prior physical request; hidden/offline deferral and an invalid authentication
gate may display the same loader with no active request. Normal Axios rejection
already produces an error. These gaps are confirmed in source but not yet the
demonstrated trigger in production.

A narrowly scoped frontend follow-up is being prepared using the existing
single-attempt policy, preserving identity checks, physical-request serialization,
and explicit retry after cancellation/offline interruption. Other balance reads
retain their current default behavior. It will require focused red/green tests.

The current financial report still decodes/copies current state and builds full
provider detail before compact home cards. Catalog queries are largely serial.
Cancellation/deadline coverage has gaps in those helpers. These are investigation
candidates, not established live causes or approved claims of a speedup.

The CodeServer workspace is Preview, separate from Production. Its process,
memory, or database timings must not be described as live Production metrics.
No internal diagnostic credential will be introduced or security policy weakened.

## Verification, preservation, and next action

Completed: fresh production ref and PR #2058 metadata comparison; exact prior
evidence branch fetch and SHA verification; applicable AGENTS.md, task continuity,
and Issue #1006 inspection; read-only independent frontend/backend path reviews;
production administration and live-page observations described above.
No new implementation test has completed at this checkpoint. No new PR or CI
result exists. This is a **WIP diagnosis checkpoint**, not release-ready code.

Next: obtain bounded current runtime evidence through supported administration or
the authorized terminal; resolve the service pressure demonstrated in the logs;
finish the scoped loader recovery and its focused regression tests; independently
review any patch and follow protocol v5 with a fresh source/intent pair, actual-host
rehearsal, owner lease, exactly one authorized publication, and three original
guard probes. Live acceptance must include a cold initial home navigation.

Preserve unrelated shared /app files and leases. Check the original guard's
fresh inactive status before any /app mutation or rehearsal. Do not reset hard,
clear another owner's lease, call paid chat, or test with real financial writes.
Durable task documents and PR metadata carry this handoff; no Issue #1006 comment
or other outbound message was sent.

## Verified catalog checkpoint (still WIP)

Predecessor diagnosis checkpoint: `584e6baea30d011f114648c6b266221a2521d9bc`.
Two backend files now have a focused, independently reviewed repair:

- `backend/operational_balance_sources.py`: the courier directory reads only
  owner identity and complete courier subdocuments, excluding unrelated setup
  history. Other financial/contract readers of that collection stay unchanged.
  Advertising account matching indexes ordinary exact-string identities while
  preserving duplicates, case, provider checks and legacy non-string comparisons.
- `backend/tests/test_operational_catalog_read_scope.py`: 21 in-memory cases
  cover output equivalence, real query projection size, ambiguous setup rejection,
  owner filtering, identity/error semantics and matching operation counts.

Red baseline: four failures and 17 passes. The failures expose excess shipping
payload in both directory modes and quadratic ordinary account matching. Green:
21 focused cases, followed by 153 passing cases in 19.79 seconds across the new
file, existing operational source, ad funding, workspace, and context-reader
modules. The test interpreter is an existing isolated scratch environment;
`PYTHONDONTWRITEBYTECODE=1`, `PYTHON_DOTENV_DISABLED=1`, and
`PYTHONPATH=backend:backend/tests` prevent live configuration use. The first
backend-only PYTHONPATH attempt failed collection on a sibling test import;
correcting the fixture import path resolved it without code/dependency changes.
Whitespace validation passed. Independent read-only review found no material
semantic mismatch. Tests use synthetic data only and no live writes.

Measured synthetic matching work drops from N-squared to 2N identity reads for
ordinary strings: 16,384 to 256 at 128 rows, and 262,144 to 1,024 at 512 rows.
Malformed/non-string-heavy catalogs deliberately retain the old fallback;
there is no universal linear-complexity claim. A separate synthetic shipping
example reads 194 bytes instead of 1,330,137 with identical directory output.
These are not measurements of production payload, latency, or memory.

Fresh hosting-terminal probes at 11:33:31 UTC returned HTTP 520 for both public
`/api/live` and `/api/health`, in 0.34 and 0.27 seconds respectively. No runtime
identity could be obtained from those failures. Later Production logs around
11:39:45–11:40:25 UTC show assembly reads at 160.9 seconds, DB-free health at
4.9–5.9 seconds, and multiple other routes at 13–25 seconds, plus recurring
upstream health timeouts. Broader pressure persists and is not declared fixed.

The original shared `/app` guard was read freshly: inactive, tracked source
clean at the production base, 417 untracked files preserved. The restored
Preview supervisor uses a separate retained supplier service; it was not changed.
Its configured database returned no current operational-state samples and its
existing diagnostics token is absent. No credential was created or extracted,
no protected diagnostic request was sent, and no Preview measurement is being
treated as Production evidence.

Frontend recovery tests and an investigation of cooperative scheduling inside
the measured slow canonical order-batch path remain in progress. Do not freeze
release intent or publish this checkpoint. Next: complete scoped red/green
verification, preserve canonical order/permission behavior, review independently,
then follow the existing fresh protocol-v5 release sequence and live acceptance.

## Reviewed source candidate

Predecessor catalog checkpoint: `35512ed7b09a5c6ef6e5a550a018559e3e5fe4af`.
All intended runtime edits are complete and independently reviewed:

1. The courier/ad catalog read reductions documented above.
2. The existing CPU budget now yields between complete canonical order
   hydration/mapping units in `order_engine/repository.py` and `service.py`.
   Raw projections, mapping, invalid-row handling, duplicates, page totals,
   grouping, pagination and monetary data remain unchanged. Cancellation at
   the new repository checkpoint joins one cursor-close operation even through
   repeated cancellation. The existing active-driver cancellation path is not
   changed. This is cooperative scheduling, not a hard single-order deadline.
3. Only the initial finance context opts into a 30-second complete visible
   attempt. Hidden/offline entry, interrupted attempts, timeout and missing
   authentication ID show explicit recovery instead of an indefinite loader.
   A still-pending physical request retains its lane; stale identities cannot
   publish a result. Other balance reads retain their original lifecycle.

Verification at this complete-source boundary:

- Frontend baseline: nine failures and two passes; after correction, all 11 new
  cases pass. Five focused suites pass all 94 cases on pinned Node 22.23.2 and
  Yarn 1.22.22. Exact commands and report hashes are in
  `evidence/frontend-verification-summary.json`.
- Backend catalog affected suites: 153 passed. Order-batch focused baseline:
  seven failures; after correction, seven passes. The affected order-engine,
  mapper, fulfillment and assembly batch has 122 passes and 41 explicit
  isolated-Mongo skips. Exact commands, warnings and limits are in
  `evidence/backend-verification.md`; skipped tests are not claimed as passing.
- Root integration on the combined current code: 72 passed, zero failures or
  skips, 16.04 seconds. Command: the same isolated Python/disabled-dotenv setup
  with `--noconftest -p no:cacheprovider -q --tb=short
  -o asyncio_default_fixture_loop_scope=function`, running
  `test_operational_catalog_read_scope.py`,
  `test_operational_balance_workspace.py`, and `test_order_batch_cpu_fairness.py`.
  The only warning is the existing fitz deprecation.
- Actual-diff independent reviews for frontend, catalog and order fairness found
  no material blocker. No real browser/transport or production-recovery result
  is inferred from these local checks.

The code-server `/app` volume has only about 92 MiB free. An initial default
toolchain preparation failed with no space; no source or existing outputs were
removed. A second preparation in an isolated `/tmp` cache on the available
filesystem succeeded using the original verified toolchain code, after a fresh
inactive guard check. The actual-host rehearsal will use an isolated checkout
and cache there, preserving shared services and the 417 unrelated untracked files.

No ASGI lifecycle wrapper, driver deadline, new cache, truncation or speculative
client polling change is included. The assembly response's 15-second polling
hint does not prove the absent mobile client's request behavior. Broad production
pressure and the cause of HTTP 520 remain unproven until live verification.

Next safe step: freeze this complete source as A, run two clean pinned frontend
builds with the reviewed public client origin, freeze intent-only B, require
fresh exact-B CI including isolated database coverage and host adapter checks,
then perform the original guard's actual-host release protocol. Publication is
already authorized, but this source checkpoint itself is not a deployed result.

## CI coverage correction before intent freeze

The first complete-source checkpoint is preserved as
`e9f5f85d7a9a8a1198ef593d564c20c2144a20e9`. No intent was frozen for it.
Independent release-readiness inspection found that two new backend filenames
fell outside the existing CI test list. Four workflow lines now collect them
explicitly and require at least 21 catalog and seven fairness cases, retaining
the global no-skip/no-failure guard. YAML and embedded Python validation passed.
`evidence/ci-readiness.md` records the complete gate map. In particular, the
existing exact-head Review Completion Resumable job runs the 41 locally skipped
assembly cases on its isolated Mongo replica set and rejects skips.

An initial local build invocation consumed the previous tracked intent and
correctly rejected the new frontend source member before Vite ran. The original
documented `MEZAN_RELEASE_BOOTSTRAP_INTENT=1` mode is required for source-A
bootstrap; it will be used for the fresh A2 build. No release rules were changed
or bypassed. Final B and actual-host adapter rehearsal must consume reviewed
intent through the unchanged governed path.

The isolated actual-host source checkout and frozen dependency prefetch completed
successfully in 48.746 seconds; Yarn installation itself took 29.78 seconds.
That is preparation only, not an adapter rehearsal or publication. The shared
source, existing services, data, untracked files and lease remain unchanged.

## Publication checkpoint — 22 November, 12:24 UTC / 15:24 Riyadh

**Evidence-only continuation branch; do not deploy this branch.** The reviewed
release is PR [#2059](https://github.com/AMASI-SA/AMASI-SA/pull/2059).
Source A is `d396049f8fedf72c394169a451d45b227ad52376`; intent-only B is
`8e5d0113be0daabe0989037c67315f2fc3d46574`. Normal merge
`c302943e330bcdd534a4a5697b8ef813dfb7cb6e` has exactly B's tree
`2eaeb022fa3cfc087026302158b35bcbc33ac990`. A remains an ancestor and only
the intent path changes after A. The production branch now points to this merge.

All applicable PR gates passed: 28 successful checks and six expected
condition/path-scoped skips, with no pending or failed checks. The six required
release executions all actually passed on B. Operational backend ran 6,265 tests
plus 90 subtests, with the new catalog/fairness minimums enforced. The isolated
Mongo review gate executed at least 41 LocalAssemblyTests and rejected all
skips/errors/failures. Stock eligibility ran 5,525 tests plus 97 subtests with
the same zero-skip protection. Counts overlap and must not be summed.

The unchanged deployment adapter also succeeded in the isolated actual-host
checkout at B: 67.103 seconds, exit 0, Host Node 24.19.0. It produced the exact
reviewed source A, runtime identity and frontend artifact; both package-boundary
proofs and backend health passed. The host proof covered 1,290 backend and 1,758
frontend files. Its retained log SHA256 is
`cd7f9cff2bd74b99db7043ab93da296e5ed8ecd2132bd59a85d67242cccc9d84`.

After a fresh inactive original-guard check, the shared workspace fast-forwarded
to the reviewed merge and received the verified generated outputs. The old
generated output was backed up; all 417 unrelated untracked files and the
separate supplier Preview service were preserved. The original guard prepared
the lease at 12:21:13 UTC for actor
`codex-operational-home-loading-recovery-20261122`; original prepublish exited 0.

**One authorized publication was submitted. Do not click Republish again.**
Emergent shows Deployment Started at 15:22 Riyadh and Building your website.
There is not yet an observed new Deployment Succeeded. Runtime recovery and
authenticated cold-home acceptance remain unverified. The release ID is
`rg5-aceb392af7809d9099ba92b27d4bb3d802999148c91013a5bba1cca5ad5f6c8e`.
Machine-readable state, source/artifact identities and check links are in
`evidence/release-state.json`.

Next safe action: observe this in-progress publication. After an explicit newer
success, run the original `/app/scripts/production_release_guard.py verify
--url https://mezansalla.com`, retain its three matching probes, then obtain
fresh inactive status and cold initial home acceptance. The owner lease must
not be cleared manually or replaced. No Production recovery is claimed yet.

## Verified publication; latency investigation remains open — 13:01 UTC

The single publication finished at **15:32 Riyadh / 12:32 UTC**. Emergent showed
a newer green `Deployment finished` entry and the current published version
`d8c03a2`, with a green success-status marker. The UI label is recorded exactly;
the available administration screen did not expose a complete Cloud Build
command transcript. The unchanged actual-host adapter rehearsal and subsequent
public artifact/identity verification provide the retained handoff evidence.

The original guard ran from 12:35:05.613091 UTC to 12:49:54.824478 UTC, exit 0,
**verified=true, checks=3**, and 760 public-file requests per probe. All probes
matched source A, the reviewed runtime identity and the frontend artifact.
Elapsed time was 889.211 seconds. Guard stdout SHA256:
`3923cdff9252f54098ec18ae4e85cf6326216dd5e3ceaf4670fb4067ac4bc147`.
The original guard closed its matching lease; fresh status was **active=false**.
The production branch was rechecked at 13:01 UTC and still points exactly to
`c302943e330bcdd534a4a5697b8ef813dfb7cb6e`.

The user's earlier complete-update source
`eeef3913ae2e1b8fa4b58f3835478e1ed17b2988` is an ancestor of this release.
No rollback was performed. The first cold home navigation displayed authenticated
cards, accounts and recent movements. A repeat reload still showed the context
loader at the 25.198-second observation, then eventually rendered without retry.
After guard completion, a fresh entry document was observed ready by 6.255
seconds; entry-to-home forced a fresh full-context read and was observed ready
by 15.592 seconds. These are UI observation bounds with tool overhead, not HTTP
or database timing benchmarks. The final review tab displays the home at
`https://mezansalla.com/operational-balances?view=home`.

**The performance incident is not declared resolved.** Postpublication logs
included four upstream health timeouts and assembly completions of 126–185
seconds despite fast application health samples. A later displayed window had
no upstream timeout among its visible lines, one 2.820-second context completion
and one 82.156-second assembly completion. Those unpaired snapshots do not
establish the dominant phase, current stream state or route percentiles.
The complete evidence, timing caveats, source trace and existing index
declarations are in `evidence/postpublication-latency.md`.

### Permission boundary and next safe action

Automatic approval review rejected opening/snapshotting the private Production
Mongo viewer, citing absent explicit authorization for that database surface
and possible exposure of schema/production data. The viewer was closed without
reading database contents, schema or query plans. Mongo connection credentials
were not revealed or copied. No index or financial/data mutation was attempted.
Source review, bounded public health reads, ordinary app navigation and
administration-log review continued as safer alternatives.

The next step requires the user's explicit permission for **read-only
Production database diagnostics: relevant indexes, query plans and bounded
statistics**. This is not permission to create an index or change financial/data
records. Do not republish this already verified candidate, reopen its lease,
repeat the completed checks, or make a speculative projection/cache/index fix.
Resume from the evidence branch, recheck current production and guard state,
then obtain the smallest missing diagnostic evidence within the approved scope.
