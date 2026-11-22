# Operational demand reads — implementation checkpoint

## Scope and authority

User requested implementation, testing, merge and publication of a comprehensive
Mezan operational performance improvement. On 22 November the user explicitly
approved evaluating and incorporating the supplied Codex performance handoff.
Keep exact money, currencies, commissions, evidence, refunds, permissions and
Qoyod behavior; preserve employee operations and existing cancellation behavior.
No Production pressure tests, external synchronization, background reconciliation,
or computation for unopened pages is authorized by opening a page.

Repository: `AMASI-SA/AMASI-SA`.
Task branch: `codex/operational-load-stage-diagnostics-20261122`.
Implementation started at Production base `c302943e330bcdd534a4a5697b8ef813dfb7cb6e`.
Fresh fetch during integration found newer Production `9b4fb716b430c32073b5ea7abaeaff62646d9633`
(PR 2062, strict external-carrier AWB reprint). Its seven changed files do not
overlap this task. Preserve and merge it before freezing any release. Its new
live deployment identity has not yet been checked in this performance phase.
This is an explicitly incomplete implementation checkpoint, not a release.

## Fresh source and live evidence

The Production branch was fetched and matched the base above. Live health and
the browser-visible canonical `/build-meta.json` both identify source
`d396049f8fedf72c394169a451d45b227ad52376`, already deployed through the protocol-v5
intent-only/merge handoff in PR 2059. Frontend artifact is
`1aa92ab8ee890e1deb23f3c6742d75bb5748401b73ac45eb17e7d86aee378a88`.
Health reported verified identity, critical backend hashes and frontend build true.
Runtime ID: `rg5-aceb392af7809d9099ba92b27d4bb3d802999148c91013a5bba1cca5ad5f6c8e`.
Local HTTP access to build metadata returned 403; browser JSON succeeded. No
cause is inferred from the local status. This diagnostic work has not deployed.

Production Mongo viewer authentication completed after the user's specific
authorization through secure credential entry. Only collection metadata and UI
capabilities were inspected; no financial mutation, index change, executionStats
or explain was performed. The viewer did not expose a query-plan interface.
No credentials, customer documents or private URLs are retained here.

The Windows study files named by the user (IMPLEMENTATION-HANDOFF-ar.txt,
REPORT-ar.txt, saved-read-profile.json) are not available in this workspace.
Their contents were not read. Implementation uses the supplied message and
fresh repository evidence; historical reported test counts are not new results.

## Completed diagnostic foundation

- Fixed-label sampled read phases for context/home and assembly, including worker
  queue wall time, synchronous thread CPU, unchanged cursor advancement and
  bounded row counts. No user IDs, amounts, queries or exception text are logged.
- At most one active sample and one sample **start** per minute per scope/process.
  This is not a promise of one completion log per minute when reads run long.
- No financial formulas, queries, cursor ownership, cancellation, existing dispatch
  or DTO ordering were changed by this foundation.
- New diagnostic cases are explicitly collected and guarded against missing/skipped
  suites by operational CI.

Fresh integrated command (exit 0):

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHON_DOTENV_DISABLED=1 PYTHONPATH=backend:backend/tests \
  /workspace/scratch/81f2839b514c/courier-test-env/bin/python -B -m pytest \
  --noconftest -p no:cacheprovider -q --tb=short \
  -o asyncio_default_fixture_loop_scope=function \
  backend/tests/test_read_path_diagnostics.py \
  backend/tests/test_operational_context_read_diagnostics.py \
  backend/tests/test_assembly_read_diagnostics.py
```

Result: **40 passed**, 6.61 s, one existing fitz deprecation warning. An initial
invocation omitted PYTHONPATH and failed collection; corrected invocation above
is the integrated behavioral result. Agent affected suites additionally passed,
but are not substitutes for final integrated verification.

## Implementation plan in progress

1. Lightweight bootstrap independent of financial/catalog reads; current-page-only
   home, server-paged directories and intent-specific entry choices.
2. Durable demand-only read artifacts, separately scoped by fresh actor, effective
   permissions, client/page, exact snapshot revision and query filters. Serve
   bounded indexed pages; keep opening/closing balances from the complete period.
3. One bounded CPU child process through the supported worker, with demand expiry,
   duplicate coalescing, claim/generation fencing and crash recovery. Do not revive
   the legacy operational reconciliation worker or interfere with Qoyod/AI jobs.
4. Integrate existing canonical dashboard card and assembly calculations. For
   sources without a complete revision, expose dated display freshness honestly;
   never treat a cache as payment authority.
5. Controlled same-data cold/warm/concurrent before/after correctness and work/latency
   measures, final CI, protocol-v5 A/B build/rehearsal, lease check and authorized
   publication. Verify live pages afterward without load testing Production.

Acceptance targets (unproven): zero closed-page loading; no full rebuild for each
stable filtered ledger page; >=80% measured redundant-work reduction; ready-read
p95 <1 s and useful home data <2 s in a controlled environment. No platform-wide
improvement percentage is claimed.

## State and next safe action

Production changed by this task checkpoint: **no**. Prior PR 2059 publication is
already complete; do not repeat it. No new lease, merge or publish occurred.
All other conversations' work and leases must be preserved.
Next: remotely checkpoint the verified working slices, merge the newer Production
commit without rewriting its history, complete integrated CI and source review,
then freeze a new protocol-v5 source/intent pair. Re-read current Production and
guard state immediately before a release, not from this historical note.


## Demand implementation milestone (WIP; not deployment-ready)

Previous remote diagnostic checkpoint: `ffcd27fcb2b737de45d2fdea26b67dac0bddf07b`.
The next checkpoint captures the following independently implemented slices:

- Demand-only immutable Mongo read generations, bounded indexed rows and focus
  lookup, actor/owner/effective-permission/source/filter binding; bounded consumer
  leases, one global CPU child, cancellation, fencing, retries and crash recovery.
- Fresh lightweight bootstrap; home summary/three visible cards/four movements;
  server-paged/searchable directories and intent-specific form selectors. Financial
  write routes still independently authorize and read canonical facts.
- Stable filtered register, account ledger, history, whole-period statement,
  financial-position and explicit report builders. Canonical arithmetic is reused;
  period balances are computed over the complete period before pagination.
- Explicit source-generation time, separate financial as-of time, preparing/error
  UI, scope-bound response validation and on-hide/query/unmount cancellation.
- Dashboard cards and assembly use the same supported child process. Assembly
  preserves plain clients' original eventual-200/error contract while connected;
  no newly introduced 14-second deadline remains. Existing client deadlines are
  unchanged, and the mobile client's 15-second cold-load success is unproven.
- Assembly-only removal of the unused Salla display timeline from DB payload,
  preserving every other canonical DTO field and normal detail/mutation reads.
- Strict stored builder-to-route/filter validation, and Riyadh month in historical
  group keys, preventing reuse across a month boundary.
- Cross-wire review found and fixed missing permission_version/owner_override on
  the demand report early return. A bootstrap-to-report contract test covers it.

### Verification completed at this milestone

Commands use `PYTHONPATH=backend:backend/tests`, `PYTHON_DOTENV_DISABLED=1`,
`PYTHONDONTWRITEBYTECODE=1`, isolated pytest with `--noconftest`, and the dedicated
local test interpreter. Real Mongo tests only use fixture-owned loopback replicas.

| Scope | Result | Evidence/limit |
| --- | --- | --- |
| Root bootstrap/home/catalog/ledger/register/filter/source tests | 23 passed, 9.17 s | test_operational_workspace_demand_reads.py |
| Real HTTP/store/actual child on same data | 3 passed, 9.00 s | test_operational_workspace_demand_http.py; controlled-home-read.json |
| Prior affected operational contracts | 119 passed, 36.30 s | workspace, grants, catalog, snapshot and diagnostics; before latest metadata/validation additions |
| Canonical financial report/position extraction | 101 passed, 42.99 s | same-body extraction and affected financial position suites |
| Durable store/job lifecycle | 25 passed, 8.93 s | real Mongo, no skips; independent agent result |
| Assembly mapper/cursor/preparation/adapters | 189 passed, 4.77 s | canonical DTO/board and original contract; independent agent result |
| Dashboard lifecycle affected UI | 115 passed | includes delayed release versus a new consumer; independent agent result |
| Operational affected UI | 267 passed, 16 suites; final targeted53passed/5suites | immutable per-attempt consumers fix hide/resume delayedDELETE; independent lifecycle review passed |

An intervening real-Mongo replay failed all three tests at fixture startup while
another owned Mongo was starting: no listener became available before the fixture
budget. No application assertion ran. A sequential replay passed all three;
application timeouts were not changed to resolve this test-environment overlap.

### Controlled measured result (not Production or browser paint)

Same synthetic 200 suppliers/5,000 movements, isolated Mongo and ASGI transport:

| Measure | Legacy | Demand read |
| --- | ---: | ---: |
| Home resource rows transferred | 222 | 3 |
| Home response bytes | 85,388 | 3,816 |
| Repeated ready-home p95 (20 reads) | not sampled at20 | 8.40 ms |
| Legacy home read samples (5) | 193–471 ms | — |
| First cold home including actual child | — | 2,792 ms |
| Six concurrent ready reads, total | — | 34.50 ms |
| Health-equivalent scheduling during child, maximum | — | 2.81 ms |
| Actual staff bootstrap during child, maximum | — | 77.41 ms |

Child PID differed from the web test process. Actual canonical financial state
was unchanged; four recent rows matched the legacy result. The queue test also
proves three concurrent filtered-page demands coalesce and four ready register
pages reuse one report. Eight-row indexed Mongo acquisition is separately tested
with eight documents examined. This is not yet a full benchmark of all target
pages, Production CPU/DB behavior, or real browser first useful content.

The cold-home target (<2 s) is **not met** by this measurement. The actual browser
also waits between preparing responses; do not present process completion as UI
paint timing. A global single child may queue cold home behind another long job.
Catalog/display facts without a complete revision have bounded dated freshness;
no saved display result may authorize a financial mutation.

### Required before release

Complete final frontend visibility/offline consumer-lifetime regression, combined
HTTP statement/report/position and permission checks, explicit CI collection with
no skipped real-Mongo demand suites, controlled repeated ledger work/latency data,
review of the full resulting source, clean A/B build and adapter rehearsal. Then
fresh guard status and an authorized v5 publish, followed by real pages and all
three release probes. Keep all unproven cold-load/mobile/production limits visible.

Post-checkpoint compatibility fix identified: preserve assembly existing persisted
`is_owner=True` authority where the canonical actor boundary permits it; dashboard
owner-role semantics must remain separate. Also review a bounded initial preparing
poll cadence; current two-second polls can add extra time to browser first data.
