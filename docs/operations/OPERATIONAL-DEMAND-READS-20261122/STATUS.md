# Operational demand reads — implementation checkpoint

## Latest verified handoff — final source and prepared intent, 22 November 2026

This section supersedes the in-progress release status in the historical sections
below. **Implementation and source CI are complete; this performance change has
not been merged to Production or deployed.** User publication authorization is
already explicit. The blocker is current trusted access to the shared host and
the original release protocol, not missing user permission.

### Immutable references

| Role | Exact reference |
| --- | --- |
| Production base J, freshly checked on GitHub | `9b4fb716b430c32073b5ea7abaeaff62646d9633` |
| Tested source A | `063611ff163c90d80cdbf96446a28584fe429846` |
| A tree | `383b716d1d39c6556fed5f440d324a9c9b83d301` |
| Source branch / open Draft PR | `codex/operational-demand-release-20261122`, PR 2069 |
| Prepared intent-only B | `5db36bd28ae86acaca8ceeb9cceee8e350916f12` |
| B tree | `057c273506c237af2035c7976cc7fe8e9aacaeb2` |
| B branch, remotely saved without a PR | `codex/operational-demand-intent-20261122` |
| Evidence branch | `codex/operational-demand-evidence-20261122` |

B changes **only** `release/release-intent-v5.json` relative to A. Its bytes are
exactly the successful governed CI artifact, not a new local rebuild. Root fetched
the remote B and passed the original `load_release_intent()` and
`verify_release_intent_git()` validators (exit 0), verified the one-file diff,
and confirmed a clean B worktree. The missing ignored local runtime identity
warning during import is not a runtime verification result.

Prepared runtime identity (not claimed live):
`rg5-90a88a6e7ac6a65670274d6180be0338d689f64d91efbf5cdfbbf651ca182bf7`.

Frontend artifact tree:
`9437b75f8c005e379a7132e62c97e90af71e596d1907ba935f6a2a01da2cd4f4`.
Intent JSON SHA256:
`640a6377f38bc153527943f12c8e8c12c446f33c9bcaaecdf4de580647a33177`.

CI run `45039209765`, artifact `14760874536`, froze two matching clean builds
of A with Node22.23.2/Yarn1.22.22, identical artifact and build-meta hashes.
Reviewed-B adapter rehearsal was skipped for source A. It has **not** yet run for
B and must not be described as complete.

### Final verification and measurements

All **47/47 workflow runs** on exact A succeeded, with no failed or unfinished
runs. The separate job-level count is 74 successful and 6 conditionally skipped;
this is not a skipped-test count. PR2069 remains draft, open, unmerged, and points
to A. Production J was unchanged at the latest read. See
`ci-063611ff-workflows.json` for the full immutable run list.

Operational run `45039209722` / artifact `14761975629`:
6,307 original Backend tests, **93 demand-read tests** including all 6 HTTP cases,
1,485 Frontend tests in95 suites, and14 report/12 simplified-balance browser
scenarios passed. Worker/Mongo, authorization, runtime reference comparison,
assembly, preparation, shipping, Qoyod, stock and release checks also succeeded.

Final same-data controlled evidence (200 suppliers /5,000 movements):

- Filtered register20 pages: reports20→1 and full snapshot reads20→1 including
  initial preparation; **95% less repeated work**, all rows/totals/pagination
  equal and financial source unchanged.
- Register p95: legacy531.962ms → ready11.252ms. Six concurrent ready reads:
  40.493ms total. Cold1,098.065ms is an **in-process counting seam**, not proof
  of CPU isolation.
- Actual child-process home: first correct HTTP200 **1,441.15ms** at250ms
  readiness checks; complete child lifecycle3,417.28ms. The child was still alive
  when the correct result became available.
- Ready home p95 **7.821ms**,20 reads; six concurrent ready reads37.919ms.
  Home payload85,388→3,816 bytes and resources222→3.
- During the real child: health-equivalent route max1.948ms, actual staff
  bootstrap max13.967ms (102 probes each). These are controlled probes, not
  deployed health or browser measurements.
- Child build701.147ms, job wall744.228ms, CPU724.908ms, peakRSS361,872KiB.
  Mongo driver p95=1.19ms, checkout p95=.05ms, zero timeouts. Driver timings
  include driver/network costs and do not establish Mongo executionStats.

The earlier local first-useful2,123.58ms target miss and one unattributed
1,227.84ms staff-bootstrap outlier remain preserved below and in REPORT-ar.md.
The successful final run does not erase them or prove stable Production p95.
No Production stress test, financial write, external sync or speculative
canonical index was performed.

### Current blocker and exact next safe action

The cloud browser renderer failed with error9. A native recovery briefly exposed
old terminal output, then Chrome exited; a newly launched native Chrome showed
`NET::ERR_CERT_AUTHORITY_INVALID` for both Mezan and the Emergent code-server.
No warning was bypassed or TLS protection reduced. The earlier visible
`active:false` terminal output was **old**, not a valid fresh guard check. A
new read-only command was interrupted by the browser crash before a verified
result; no shared /app source write followed.

The final supported control retry also failed: the runtime had reset after
inactivity, and its inventory call timed out and reset again. No page, current
guard result or live identity was obtained by that retry. Latest Issue1006
coordination (comment6238987123 updated2026-11-22T15:55:59Z) also records separate
inventory PR2065 awaiting management-browser recovery, with no deployment.
Its source/intent and permission scope remain unrelated and must be preserved.

The user has now offered to run the terminal command manually. The adjacent
MANUAL-PREFLIGHT.py uses the original guard status, read-only Git identity/ref
inspection and three bounded HTTPS identity reads with normal certificate
verification. It stops on active/incomplete guard state, makes no source or
business changes, and prints only selected identity fields. Await its actual
output; supplying the script does not establish a fresh inactive guard or live
release identity. Do not proceed with rehearsal merely because it was sent.

The original AGENTS.md requires fresh shared `active:false` **before a release
rehearsal or /app changes**. Moving PR2069 from A to reviewed B would automatically
schedule the rehearsal. Therefore B was safely saved on a separate branch with
no PR; release readiness pushes are limited to Production. No workflow-dispatch,
lease, /app update, Production merge or publish was started for this improvement.

Next safe sequence:

1. Restore a trusted browser/admin connection; do not bypass a certificate warning.
   Read current Issue1006 coordination and fetch Production and both task refs.
2. In the real shared host run `cd /app` then
   `python scripts/production_release_guard.py status`. Continue only on a fresh
   `active:false`; never close or modify another conversation's lease.
3. Inspect fresh `/api/health` and canonical/cache-busted `/build-meta.json`;
   compare the live source with current Production, A and B. Old screenshots
   or a prior release do not prove current identity.
4. If Production still equals J, fast-forward the source PR branch A→B with an
   expected-head check, then complete reviewed-B CI/rehearsal. If J has advanced,
   inspect and preserve that change; do not force-merge this stale frozen intent.
5. Follow the original v5 host rehearsal, exact intent/source checks, prepare and
   prepublish; publish using existing authorization. Never use inherited ignored
   outputs as build inputs or `git reset --hard`.
6. Require a newer explicit Deployment Succeeded and the original guard's three
   consecutive matching live probes; then require `active:false` and test the
   actual requested pages without Production load testing.

Still unproven: actual Production recovery/error causes; real user network/paint;
cold multi-owner queue contention; mobile cold deadlines; full Production DB
execution plans; fresh live identity/shared guard after J. Unversioned display
sources retain explicit bounded freshness (catalog up to300s; dashboard/assembly
15s) and are never authorization for a financial mutation.

All current evidence is saved separately so the frozen A/B trees remain unchanged.
The full Arabic report and raw final metrics are adjacent to this document.
PR metadata records the exact evidence checkpoint SHA after the branch is saved.
Production changed by this performance implementation: **no**.

---


## Scope and authority

User requested implementation, testing, merge and publication of a comprehensive
Mezan operational performance improvement. On 22 November the user explicitly
approved evaluating and incorporating the supplied Codex performance handoff.
Keep exact money, currencies, commissions, evidence, refunds, permissions and
Qoyod behavior; preserve employee operations and existing cancellation behavior.
No Production pressure tests, external synchronization, background reconciliation,
or computation for unopened pages is authorized by opening a page.

Repository: `AMASI-SA/AMASI-SA`.
Active source-candidate branch: `codex/operational-demand-release-20261122`.
Preserved implementation/CI history: `codex/operational-load-stage-diagnostics-20261122`.
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


## Integrated continuation checkpoint

Remote implementation milestone: `81a3774958259c163437772495034f35ac207eab`,
tree `92ff1789be1d11f8c836404e774d6882c0777f81` (72 changed files).
Remote integration with newer Production: `cabb3b79560ad29a6c721cee8c224a425b310cd4`,
tree `a47d60a0a740e9439aaf31ff2b31882203ca59c1`. This normal merge preserves
Production `9b4fb716b430c32073b5ea7abaeaff62646d9633` and all its AWB changes.

Subsequent source fixes and verification:

- Each new assembly/card intent may retry failed work once; polls and legacy
  connection renewals do not reset failure. Pending demand wakes only the read
  queue. Assembly preserves its existing persisted is_owner authority separately
  from the dashboard's strict owner role. Family tests22/22; three existing real
  Mongo assembly policy oracles plus canonical assembly child4/4 passed15.85s.
- Preparing UI uses bounded500/1000/2000ms checks while respecting slower server
  hints. Invalid or overflowing delays reject. Full affected frontend394/394,
  21/21 suites,15.379s; no skipped or failed tests.
- Both retained browser harnesses (14 report,12 simple balances scenarios) now
  reflect bootstrap/paged options/demand report wire contracts and validate exact
  preparing-consumer release. Fixtures and Vite builds passed. Local Chromium
  failed before any scenario with socket() EPERM; its sandbox was not disabled.
  Browser execution remains required on the existing CI runner.
- Independent review found explicit ad_bank_fx=False was ignored in one new
  resource reader and ready multi-chunk statements outlived disconnected HTTP
  readers. Both were corrected; the existing visible-report lifetime now owns
  every read and cancels an in-flight chunk. Newly admitted consumer leases are
  released on cancellation or authorization change. Fresh root suite29/29 passed
  in9.67s, including both FX flag values, already-closed/in-flight disconnect,
  and post-admission permission/cancellation cleanup.
- Pinned report snapshot mismatch is rejected before starting a heavy job.
  Added real HTTP cases for complete>300-row statements, report scope/pinned
  revision, scoped position/release, and exact20-page register before/after work.
  These extend HTTP coverage from3 to6 and still require a completed integrated
  run. No20-page reduction result is claimed yet.
- Three workflows explicitly collect the new suites, require no missing/skipped
  cases and preserve the old>=3500 operational and>=41 realLocalAssembly gates.
  All YAML/shell/JS rejection scripts validated. Added the actual child dependency
  python-dotenv==1.2.2.
- Child metrics now include six fixed numeric Mongo driver timing/timeout fields
  from the existing listener, without additional queries or query values. These
  timings include DB-network round trips and are not executionStats. Privacy and
  forwarding tests4/4 passed; the extended actual-child assertion awaits CI.

One combined local run was interrupted (exit130) during host contention without
completed JUnit or a known active test. An initial inference that it had reached
HTTP was withdrawn:16 fixture databases do not establish that. No product
assertion failure or acceptance pass was identified. The raw incomplete-run
metadata is retained in interrupted-local-run.json. Future execution should use
live verbose pytest output and preserve raw timing; do not relax application
limits to turn this run into a pass.

The cloud browser also failed at CDP attach/dispatch and subsequently reset its
kernel. Search-service access to health/build metadata was unavailable. These
are access failures, not evidence of a Production outage. New live identity,
shared Release Guard status and actual pages remain unverified since the newer
Production merge. No shared /app change, new lease, publish or merge to Production
has been performed by this demand-read work.

Next safe action: push this task checkpoint, open a Draft PR against Production
for independent CI, resolve any concrete integration failures, and require the
original current guard/rehearsal/three-probe protocol before publication. The user
already authorized publication; the remaining blocker is evidence/access, not a
missing request for permission.

## CI and performance follow-up on 22 November

Remote checkpoint reviewed by the first full CI cycle:
`fdabb1d2a404433ad2b506d47847850aadd56e3c`, tree
`612c3b5db04296df6fbbf1d3731d169b2768f459`, Draft PR
<https://github.com/AMASI-SA/AMASI-SA/pull/2066>.
All 47 workflows finished: 45 succeeded and two failed. The exact workflow IDs,
URLs and observation time are retained in `ci-checkpoint-fdabb1d2.json`.

- Operational frontend: 1,485 tests / 95 suites passed. Sandboxed CI browser
  acceptance passed all 14 report and 12 simple-balances scenarios, with uploaded
  evidence. This closes the earlier local Chromium execution gap.
- Operational backend completed6,307 original cases,84 new durable-read cases
  (including all six HTTP cases), and16 retained shipping cases. No demand case
  was missing, skipped, failed or errored. GitHub artifact14760567543 was downloaded
  and its SHA256 verified against GitHub:
  `3b109a31f2790f4f94bccd97d046191a44c42adb25328f15ec703fa31ac89b5c`.
  `ci-fdabb1d2-summary.json` binds results to the exact source/tree. Its home and
  register evidence JSON are retained alongside it. In this independent CI run,
  register work again fell20→1 (95%), with exact financial parity; legacy p95
  was555.65ms and ready p95 was10.07ms. Ready-home p95 was8.89ms; staff bootstrap
  maximum26.14ms and health-equivalent maximum1.74ms. The CI cold-home3,681.06ms
  measurement still includes child shutdown; see the corrected first-ready
  methodology below. These results test the earlier29-case read-builder source,
  before the local38-case authority-budget follow-up.
- Worker isolation and retained operations passed: the background-worker gate
  reports 351 real-Mongo lifecycle tests, 88 retained Salla regressions and 107 UI
  tests; the canonical assembly child group reports four passes. Review completion
  passed all five groups (35, 349, 1,081, 125 and 1), including the original
  LocalAssembly minimum and no-skip guards. Preparation, Fulfillment and Dashboard
  fees workflows passed every backend/frontend job.
- Dashboard runtime adoption stopped before comparison because the frozen
  fixture extracts server definitions without imports, leaving the new adapter's
  `_canonical_dashboard` dependency undefined. The runtime-only fixture now binds
  the real canonical module and observes its real parser. Historical reference,
  Production-baseline arms, expected JSON and all 164 read/output assertions stay
  unchanged. Six affected in-memory/oracle suites passed 236/236 in3.99s, including
  tenant binding and cleanup after error/cancellation. This fix still needs the
  full runtime adoption CI on the next source commit.
- Release Readiness rejected the checkpoint's ancestry before compilation.
  The endpoint intent bytes equal Production J `9b4fb716b430c32073b5ea7abaeaff62646d9633`,
  but the normal integration merge `cabb3b79` appears in the intent's full-history
  range. The original protocol rejects that range. Preserve this WIP branch and
  PR; create a separate, clean source candidate directly from J carrying the
  final reviewed task tree and J's unchanged intent. No guard, release rule or
  existing source/intent pair is to be rewritten or relaxed.

### Same-data register work and latency

`controlled-register-read.json` contains the completed synthetic200-supplier /
5,000-movement comparison over20 pages of8 rows, with the same Arabic search and
3–20 November business-date filters. Every row, total and page field matched;
the financial source was unchanged.

| Measure | Legacy | Demand read |
| --- | ---: | ---: |
| Full reports over20 pages | 20 | 1 including first preparation |
| Full financial snapshot reads | 20 | 1 including first preparation |
| Measured redundant report work reduction | — | 95% |
| Ready-page p95 over20 reads | 1,768.50ms | 7.95ms |
| Six concurrent ready reads, total | — | 31.54ms |

The report-count builder uses an in-process injection solely to count canonical
work precisely; production CPU isolation is tested separately with the actual
subprocess. This benchmark uses loopback Mongo and ASGI, not browser/network or
Production timing. Legacy timing outliers are retained, not removed. Do not infer
a platform-wide speed percentage from this table.

### Responsiveness, authority-read budget and first readiness

The expanded local HTTP command completed five cases, then missed a responsiveness
bound in its actual-child case: one staff bootstrap took1,227.84ms among roughly80
probes; health-equivalent maximum was6.78ms. Its initial JUnit is retained in
`controlled-http-initial.xml`. The child did produce the exact expected total and
ran in a distinct process. The slow probe's cause was not localized, so neither
host contention nor a particular Mongo stage is claimed as its cause.

A focused replay preserved timing evidence before enforcing the same unchanged
assertions. It passed (staff maximum81.11ms, health9.96ms). Code review then found
the fresh access loader was redundantly preceded by another identity read.
`load_context` now uses the actor/owner returned by the canonical `load_access`.
It retains explicit browser-page checks, enabled checks, both bootstrap authority
passes and the full post-read fingerprint comparison. Staff identity lookups drop
from8 to4 and owner lookups from4 to2, plus the same small header read. No financial
write or authorization primitive changed. The38-case root suite passed13.53s,
including read budgets, mobile actor recovery and five authority changes during
the header read. An initially incorrect new test assumed empty owner page grants
should deny access; the test was corrected to the existing owning-account policy,
without changing product permission behavior. The surrounding33 access/page-grant
cases passed in that earlier combined invocation.

The prior2,792ms and3,138ms "cold home" numbers waited for complete child shutdown
after publication; they are child-lifecycle-inclusive observations, not first-ready
HTTP measurements. The corrected test polls first HTTP200 separately, continues
health/bootstrap checks until the child exits, and retains ordered per-pass phase
timings even when a bound fails. `controlled-home-first-ready.json` records:

- First correct HTTP200:2,123.58ms, with controlled250ms readiness checks.
- Complete child lifecycle:3,983.48ms; no teardown/cancellation code was changed.
- Ready-home p95:21.76ms; health-equivalent maximum223.61ms; actual staff bootstrap
  maximum440.58ms. All unchanged sub-second assertions passed.
- Response remains3,816 bytes /3 resources versus85,388 bytes /222 legacy resources.
- Child CPU2,018.89ms, build1,288.39ms, peakRSS117,444KiB. Mongo driver command
  p95=1.12ms and checkout p95=.05ms, with zero reported command/checkout timeouts.

This focused test passed in10.65s. The controlled first-useful target below2s
remains **not met** in this measurement; actual browser paint/polling, cold global
queue contention, mobile cold-load deadlines and Production recovery remain
unproven. The synthetic health route mirrors the scheduling boundary only;
deployed health identity and actual employee paths require live verification.

### Release and next safe action

Current Production ref remains `9b4fb716b430c32073b5ea7abaeaff62646d9633` on fresh
GitHub inspection. Browser inventory recovered, but attaching the code-server
page failed with Page.enable timeout; a single fresh-tab attempt also timed out
and reset the control kernel. No live source identity or shared guard state was
obtained. These are control-channel failures, not proof of a site outage.

Production changed by this performance implementation: **no**. No new lease,
shared /app mutation, publish or Production merge occurred. Next: preserve this
follow-up, create the clean source-candidate branch from fresh Production J,
complete corrected CI, then require the original current shared guard status,
governed rehearsal, authorized publish and three live verification probes. Keep
PR2066 as the recoverable implementation/CI history; no other conversation's work
or lease is to be changed.

### Clean source-candidate checkpoint

Created and fetched remote source candidate
`dd1c476717f03b96c2c83ddba8d9187e8cb39bde`, tree
`c23356af18833731e2814128544ce91d3309649d`, on
`codex/operational-demand-release-20261122`. Its direct parent is fresh Production
J `9b4fb716b430c32073b5ea7abaeaff62646d9633`. The complete tree is byte-identical to
the final implementation follow-up, including the bootstrap read-budget fix,
corrected runtime oracle adapter, unchanged assertions and all retained evidence.
No Production source, AWB change, release intent, guard or lease was altered.
The old task branch and Draft PR2066 remain intact as the verified history.

Active local worktree: `/workspace/scratch/b90f3c3040a7/mezan-demand-release`.
The earlier worktree must not be used for further source changes. The present
documentation-only follow-up records this exact remotely recoverable checkpoint.
Next: validate the original candidate-transition rule, open the final Draft PR,
and complete CI on its exact head. A successful candidate build still does not
authorize bypassing the current shared guard or constitute a deployment.
