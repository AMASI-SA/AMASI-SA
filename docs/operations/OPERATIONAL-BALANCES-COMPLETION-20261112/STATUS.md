# Operational balances completion — 2026-11-12

## Current state

CORRECTIVE RELEASE MERGED, PUBLISHED AND OFFICIALLY VERIFIED; SAVED-TABLE AND
MODAL ACCEPTANCE PASSED; FRESH-REFRESH TIMEOUT REMAINS OPEN. OWNER'S NEW DELIVERY
RULE AND A MEASURED SOURCE-CONVERSION OPTIMIZATION ARE BEING IMPLEMENTED ABOVE M3.
The owner explicitly authorized public upload, merge and publication after the
public destination was identified. PR1898 reached production and passed the
official three-probe release verifier, and that first lease closed. Its first
authenticated live financial report load exposed a timeout recovery gap and
redundant bank-refresh work. Corrective PR1899 completed its source and intent
gates, then was superseded to preserve an additional Production source merge.
PR1901 now preserves that merge and the verified operational fixes in exact
source A3, intent-only B3 and normal merge M3 below. All required B3 gates and
all three exact-M3 workflows passed. The official operator build,
prepare/prepublish passed, and one **Publish changes** submission was clicked
at approximately 19:55 UTC on 2026-11-12. Emergent subsequently displayed a new
green **Deployment finished**, November 12 at 11:05 PM Asia/Riyadh, version
`275e412`. The official verifier returned **verified:true, checks:3**, exact
A3/M3/runtime/artifact identity and 582 canonical/cache-busted Frontend requests
per probe. The lease closed. A fresh visible financial-page request still hit
`408 operational_refresh_timeout`; its new recovery displayed 21 saved tables
with the dated warning, preserved known balances and working paginated order
dialogs. This is successful saved-state recovery, **not** a successful fresh
calculation. Follow-up implementation remains in progress.

Preview-service correction: a prior start command reported both services
started, but this did not establish sustained frontend health. Before this
corrective publication the legacy preview frontend was **FATAL** because its
old launcher referenced a missing Node binary. Preview backend was running and
was stopped by this task for the governed build. After the lease closed, root
restored backend and directly observed RUNNING with tracked source clean. The
legacy frontend was not changed or restored. The operator workspace later
suspended; reopening its IDE now reaches a sign-in form. The restored backend
status is an observation before that suspension, not proof of current service
state in the resumed workspace.

- Repository: [AMASI-SA/AMASI-SA](https://github.com/AMASI-SA/AMASI-SA).
- Release PR: [1898](https://github.com/AMASI-SA/AMASI-SA/pull/1898), normally
  merged into `hotfix/prod-snap-meta-final` without squash or rebase.
- Final corrective PR: [1901](https://github.com/AMASI-SA/AMASI-SA/pull/1901),
  normally merged at M3 without squash or rebase.
- Frozen release branch: `codex/operational-balances-completion-20261112`.
- Corrective task branch:
  `codex/operational-balances-current-production-20261112`, based on staged
  Production merge `8379ca2560381bd3c626586d17aa63eb47e2a16c`.
  The initial frozen A/B and superseded corrective A2/B2 remain unchanged.
- Implementation worktree: `/workspace/scratch/e00762959313/mezan-operational`.
- Corrective integration worktree:
  `/workspace/scratch/e00762959313/mezan-operational-current-production`.
- Documentation-only handoff branch:
  `codex/operational-balances-published-handoff-20261112`, based on exact M3.
- Handoff worktree:
  `/workspace/scratch/e00762959313/mezan-operational-published-handoff`.
  The prior remote documentation checkpoint is
  `e5bd5cad5f0777ad4550922f89d905359ce2da38`; this follow-up records verified
  publication and the remaining live failure without editing frozen A3/B3/M3.
- Root owns final integration, operator rehearsal, lease, publication and live
  acceptance. Other contributors have no publication or lease action.

### Initial release immutable identities — PR1898 history

| Identity | Exact value |
|---|---|
| Reviewed Production base J, PR1895 | `88589786f4c30ed78a5c9d185fae6ce7786ca9da` |
| Source A | `b23bed75f09e52b6faf4f985de00dd56a170d59c` |
| Source A tree | `949b43ce781162e2685f67cf0ff137d9f1f897f9` |
| Intent-only B | `00c7ad9f9c0643065718751d84317972b24bb7db` |
| Normal merge M, PR1898 | `54b6fcf56fbe329a0fba7200b130eeca8ce5983c` |
| Reviewed B and M tree | `d908b7fb33adeae95efaa661d0049d42f18f5103` |
| Runtime release ID | `rg5-b275cea8a671baf43897993c2925f54ed3cc85a1cc454e927dda14ee67ab4f34` |
| Frontend artifact tree SHA256 | `47e182eb6c963fadeef16d9606c22fed381c5a75be24495b9533fe746c2fdee1` |
| Tracked intent Git blob | `e01eac8b75feb9a2f8d54ac490b509ae4c1f89be` |
| Tracked intent SHA256 | `5ddc906e60cf4d5f966cdf61a3182c0e35127ebb5842a9b465c30d8c717fe062` |

B has exactly one parent, A, and changes only
`release/release-intent-v5.json`. Its 1,289,972 bytes came unchanged from the
successful source-build artifact. The intent continues to identify A as source
and J as its reviewed base. M has exactly J and B as parents, and its complete
tree equals B. No governed source was added after A. Do not squash, rebase,
amend or append documentation to the frozen release pair.

### Provenance and preserved work

- Original implementation base: `06d0002fab257e1976dd2344865da4de77c158c3`
  (PR1893 merge). The 200 implementation paths were initially committed at
  `b2a2d818eba388ab1f94de771e3f87e6db6fb128` and verified locally.
- First authenticated integrated remote checkpoint:
  `3cd0b7ba93343032180f27f03ba3aa45991ce3e1`, tree
  `72c0a14d1a4b765e46ab31d0f0d14b71fd484c13`. Its source tree matched the
  local integration while preserving the release-intent ancestry contract.
- PR1894/PR1895 changed 15 paths with no overlap with the original 200 task
  paths. Their supplier embroidery and dashboard changes are included in J/A.
  PR1892 demand-only report behavior and PR1893 changes remain preserved.
- The initial invalid CI definition was corrected before A by placing
  runner-dependent browser paths at step scope. Successful exact-A and exact-B
  runs supersede it; that correction changed no application source.
- The operator checkout was tracked-clean when root inspected it; unrelated
  untracked files are preserved. This task has performed no financial approval,
  settlement, advertising write, migration or backfill.

## Owner requirements

1. Complete all remaining operational-balance issues in one reviewed release.
2. Recognize an evidenced customer-paid amount using the Salla order source
   behind the dashboard payment totals; missing capture time alone must not erase
   an otherwise established paid amount. Provider payout to a bank is a separate
   settlement. Do not fabricate a timestamp or duplicate an opening/capture.
3. Show each provider/carrier/driver's established amounts even when individual
   orders are unresolved. State partial coverage alongside those amounts.
4. Show counted deliveries and a count of distinct unresolved orders. Clicking
   that count opens a modal listing order number and concrete reason(s). Multiple
   issues for one order count once; unidentified owners are not guessed.
5. Isolate completeness per source/table/party, including actual noncash review
   facts, rather than propagating an unrelated issue to every table.
6. Complete shipping-charge recognition from established completion evidence
   after the configured start, preserving carrier/tariff identity, prior charges,
   separate COD/cash evidence, and reviewed historical impact.
7. Complete daily movement filters, ten-row pagination, actor/account/party,
   after-balances and permission filtering with bounded reads/calculation.
8. Complete read-only forecast/profit computations with explicit evidence scope,
   matching period and no invented zero for missing inputs.
9. The owner's latest instruction is strict demand-only operation: no background
   balance worker, scheduled refresh, hidden-page loading or polling. Calculate
   only for a visible page request; cancel reads/calculation when it leaves.
   Keep one refresh per owner across processes and preserve completed snapshots.
10. New explicit owner rule: when the current order state is delivered and its
    creation is strictly after the operational start, recognize the carrier or
    driver delivery without requiring an actual delivery timestamp. Preserve
    unknown delivered_at, separate recording/recognition basis and COD evidence.
    Orders created at/before the start still require evidence of post-start
    completion. Keep tariff/identity gaps individual and keep existing known
    delivery counts compatible with older snapshots.
11. Subsequent explicit owner request: show a per-order confirmation button in
    the unresolved-order dialog when the unresolved fact is whether delivery
    happened after the start. The owner can attest that boundary without
    providing an exact delivery timestamp. Preserve actor/time/source binding,
    permissions, replay protection and all independent tariff, cash and opening
    blockers. This development task must not click a live financial confirmation.

## Live observations before implementation

- Financial-position page displayed start 2026-11-01 00:30 Asia/Riyadh.
- Latest successful calculation displayed 2026-11-12 01:07 Asia/Riyadh while a
  later report generation displayed 18:32. A source-refresh failure warning was
  visible. Expanding the saved page's diagnostics confirmed
  `operational_capacity_requires_archive`; a collection row limit was not proven.
- Previous implementation published in PR1892 removed UI polling and reused the
  COD allocation index. It did not change background refresh cadence, shipping
  eligibility, profit/forecast placeholders or daily activity.
- Existing PR1846 and PR1847 are unfinished reference implementations. Do not
  merge them wholesale or reuse an old release intent.
- PR1893 was merged while this review continued. Current work starts above it.

## Verification contract

- Evidence-based amounts, same-owner identities, currencies and existing opening
  balances/settlements must remain correct and idempotent.
- Prove paid/full/partial/split/refunded and missing-time cases; unsettled money
  must not enter bank cash merely because an order is paid.
- Prove known carrier/driver amounts survive unresolved orders, distinct order
  counters and modal row/reason correctness, with cross-owner denials.
- Prove shipping charge/event/time/tariff and historical-review boundaries.
- Prove daily activity pagination/filters/after-balances and limited account
  access before counts or totals are computed.
- Prove complete and incomplete forecast/profit cohorts and no double-counting.
- Prove source-refresh recovery diagnostics and no recurring report polling.
- Run existing relevant suites and independent integrated review before release.

## Implemented behavior

- The scheduled operational worker is removed from runtime registration, and
  its legacy entry points perform no work. The visible POST refresh owns its
  reads, cancellation, short distributed lease and final atomic save.
- There is no polling, hidden-page preload, focus refresh after a completed
  read, scheduled acquisition or second reconciliation loop. A cancelled read
  may resume only when the user returns to the visible page. A completed read
  remains completed until another explicit action.
- Reports, daily activity, supplier workspaces, allocation choices, supplier
  invoice/native purchase previews and completion-fee previews own their server
  work and join cancelled CPU/source tasks. Explicit approvals and financial
  writes do not carry the read AbortSignal; an atomic commit already started is
  observed to completion before its request releases ownership.
- The same lifetime now covers historical shipping/platform reviews, salary
  and opening-inventory previews, inventory/catalog/invoice reads, customer
  panels, account statements, old movement feeds and audit/context reads.
  The final child-panel audit added 27 read-only route lifetimes. Small ad
  settings dialogs and supplier-adjustment lookups also defer hidden reads.
- Packed snapshot/issue decoding and audit-page validation run outside the
  event loop in request-owned preparation. The request joins them on departure.
  Visible refresh also owns its large-state copy, comparison and storage plan;
  cancellation before commit admission cannot persist a candidate snapshot.
- Source conversion cooperatively yields within the existing CPU budget, and
  its payroll/ad/recurring preparation is cancellable and joined. A real parser
  regression previously consumed all 20,000 synthetic orders after a disconnect
  at order ten; it now stops early. Separate deadline regressions prove HTTP
  408, stopped preparation before lease release, and no remaining tasks. A
  preparation helper also owns a stop event when no read scope was inherited.
- Missing native bank, courier and paid-SAR evidence is acquired under the
  visible request's lease, at most four candidates per source within a shared
  25-second acquisition budget, then one reconciliation. Newly discovered gaps
  remain disclosed until a later user refresh; no background retry is queued.
- Financial snapshot maps use a lossless owner-bound BSON packet under the same
  revision CAS when the 12 MiB guard is exceeded. Opening balances, movements,
  idempotency records, salary guard indexes and audit history remain intact.
- Evidenced full/partial paid amounts, known provider subtotals, unique pending
  order details, per-party shipping coverage, reviewed completion recognition,
  scoped daily activity and saved profit/forecast sources are implemented.
- Paid-order counts are separate from recognition/fee-complete claim counts.
  A proved unpaid order is not an unidentified exception. Pending-only parties
  remain visible with unknown amounts, and one order with multiple reasons is
  counted once. The modal lists all available order identities/reasons, with
  search and ten-row pagination; the export preserves those details.
- Unproved fees cannot become zero fees or a fully known provider net. A missing
  wallet statement cannot become a zero liability or an invented foreign wallet
  ledger. Known amounts remain separately available.
- COD source cohorts are reconciled by identity: missing canonical sources and
  uncovered current waybills block a complete total without erasing known rows;
  a covered order is not also counted as missing in another cohort.

## Local verification

- Pinned Node 22.23.2 / Yarn 1.22.22 toolchain verified.
- Frontend consolidated run: **640 passed, 47 suites passed**, zero failures.
  Covers all operational UI suites plus suppliers, employees and navigation.
- Earlier shared payment/shipping compatibility run: **919 passed, 168 subtests passed**;
  one intentionally skipped memory-only transaction-rollback case, with the
  corresponding replica-set cases enabled. Uses the existing auto asyncio
  fixture mode and a disposable Mongo 7.0.16 replica set.
- Final consolidated backend run: **3585 passed**, exit **0**, in 331.46 seconds.
  It covers the balance/shipping suites and affected report, inventory
  eligibility, background-worker and noncash review consumers. Its single
  skipped case needed `QOYOD_TEST_MONGO_URI`; that exact canonical worker startup
  case then passed separately, exit **0**, in 13.66 seconds. Thus all **3586**
  selected backend cases have passed; there is no outstanding skipped case in
  this selected scope.
- The canonical startup case boots the actual child runtime against an empty
  isolated database, proves `operational-balances` is absent from registered
  jobs, and drains the child successfully. Initially missing local runtime
  packages were installed at the existing repository pins (`xlsxwriter`,
  `openai`, `webauthn`); no dependency file or application source was altered.
- Mongo 7.0.16 used a disposable `operationalphysical` replica on loopback
  port 27462, with all test URIs pointing to that process. A 1 GiB WiredTiger
  test cache supported retained large legacy snapshots. The earlier 0.25 GiB
  fixture failed during seeding before the source under test; the corrected
  fixture and final capacity/snapshot cases passed.
- Additional request-lifetime regressions cover actual ASGI disconnection,
  no financial writes, cancellation during packed-state preparation, and
  inventory CPU cleanup before its snapshot session closes. Independent focused
  runs passed and the final consolidated gate includes these regressions.
- The actual financial-report/dialog component fixture built successfully with
  pinned Node. Its data uses three priced deliveries and seven unique unresolved
  orders. Local Chromium exited before opening a page because this environment
  denied its Unix socket; no browser result is claimed from that attempt. The
  later sandboxed CI execution passed all six scenarios, as recorded below.
- Independent reviews covered source/party completeness, worker removal and
  live UI consumers, request cancellation, wallet unknowns, provider counts,
  snapshot packet integrity and the driver bank/POS writer. Concrete findings
  were fixed and checked before this source freeze.
- Final source/test manifest verification covered 199 files with zero hash
  mismatches after the local backend gate. Later changes integrated current
  Production and the scoped CI gates before immutable source A was frozen.
- Earlier intermediate failures were addressed, including the daily query
  parameter type, unrelated fact payload parsing, unknown wallet totals,
  provider pending counts and visibility/cancellation gaps. Final results above
  supersede intermediate counts. The separate exact-source CI gate subsequently
  passed on A and B as recorded below.
- CLI previews distinguish a partial known provider net from a complete net.
  A pure reducer result without a service-stamped snapshot timestamp is also
  explicitly incomplete. Mobile history retains the account ACL and redacted
  display contract; granted read accounts never confer write permission.

### Local reproduction scope

The local disposable runner supplied `OPERATIONAL_TEST_MONGO_URI`,
`MZ2_TEST_MONGO_URI`, `BUILD37_TEST_MONGO_URI` and (for the supplemental startup
case) `QOYOD_TEST_MONGO_URI`. Each suite creates and drops its own synthetic
test database. No application database configuration or production fallback is
used. The backend invocation used `PYTHONPATH=backend:backend/tests` and:

```bash
python -m pytest -q \
  backend/tests/test_operational_balance*.py \
  backend/tests/test_operational_completed_shipping*.py \
  backend/tests/test_operational_driver_review_mongo.py \
  backend/tests/test_operational_driver_review_packed_state.py \
  backend/tests/test_operational_financial_review_groups.py \
  backend/tests/test_operational_read_runtime.py \
  backend/tests/test_operational_open_cost_exposure.py \
  backend/tests/test_operational_bundle_cost_projection.py \
  backend/tests/test_operational_supplier_invoice_cutover.py \
  backend/tests/test_operational_inventory_projection_eligibility.py \
  backend/tests/test_background_worker_process.py \
  backend/tests/test_store_delivery_noncash_review.py \
  --asyncio-mode=auto --tb=short -rs
```

Frontend verification used the pinned Node binary, React Scripts Jest,
`--watchAll=false --runInBand`, all `src/pages/operational` suites, suppliers,
employees, supplier services and navigation shell tests. The existing local
Python environment is not a claim of full production dependency-lock parity;
the later exact-source CI supplies the repository-pinned release evidence.

## Exact-source CI and browser acceptance

All **24 workflows on exact source A succeeded**, including CodeQL, Security
Gate, operational stock eligibility and the affected compatibility workflows.
The expanded Operational Balance Web run
[43276337970](https://github.com/AMASI-SA/AMASI-SA/actions/runs/43276337970)
executed the following gates without skipped cases or failures:

| Gate | Observed result |
|---|---|
| Operational backend, including completed shipping, request lifetime, financial review and packed driver state | **3,590 passed** |
| Actual canonical server-child boot/drain on isolated Mongo | **4 passed**; `operational-balances` absent from registered jobs |
| Shared shipping summary and calculator compatibility | **16 passed, 18 subtests passed** |
| Operational/supplier/employee frontend | **666 passed, 50 suites passed** |
| Sandboxed Chromium actual-component acceptance | **6 of 6 passed**, zero browser errors |

The six browser scenarios establish:

1. The known 75 balance remains visible alongside seven distinct unresolved
   orders and reasons; keyboard dismissal restores focus.
2. The dialog fits a mobile viewport and every pending order remains reachable.
3. A completed visible page sends no repeated requests.
4. A source-refresh failure retains the dated saved money and shows a warning.
5. A hidden mount sends zero requests; one visible activation reads once.
6. A denied refresh does not read a financial snapshot.

The browser fixtures are synthetic, run without production financial access,
and retain exact source commit/tree/component hashes. Desktop and mobile
screenshots were visually reviewed. These results complement the real-Mongo
and actual-process lifetime tests; they do not claim a live financial refresh.

### Exact intent and browser artifacts

| Evidence | Exact source and artifact |
|---|---|
| Candidate intent from two clean frontend builds | A; [run 43276337510](https://github.com/AMASI-SA/AMASI-SA/actions/runs/43276337510); artifact **13970766734** |
| Browser JSON, source hashes and screenshots | A; run **43276337970**; artifact **13970866667** |

- Candidate ZIP SHA256:
  `ce0ab52b9df04ffe849fe6ba4ac62f021c8c1d4b9c999bf080ea386afa757b0f`.
- Browser ZIP SHA256:
  `743a0e5758dabbb0c62a067a5a731852735978c26cf96757e9b8130f1207dcf0`.
- Both downloaded ZIPs matched GitHub's artifact digests exactly.
- The candidate payload, runtime identity, frontend files, Backend files and
  release-control files passed official repository validators against a clean
  exact-A export. The official J-to-A transition and independently derived Git
  manifests also matched. No generated local artifact substituted for the
  downloaded candidate intent.
- Manifest file counts: frontend **1,169**, Backend runtime **1,194**, release
  control **282**. Governed toolchain: Node **22.23.2**, Yarn **1.22.22**,
  Vite **8.2.4**, approved client origin **https://mezansalla.com**.

## Reviewed-B release rehearsal and merge

All applicable exact-B workflows completed: **24 success, zero failures or
pending workflows, and one expected TikTok branch-specific skip**. That
conditional workflow skip does not skip the operational acceptance cases.

Both **Host Node 20.20.2 and Host Node 24.19.0** clean-clone adapter rehearsals
passed in [release-readiness run 43277778171](https://github.com/AMASI-SA/AMASI-SA/actions/runs/43277778171).
Each exercised the governed clean installation/build, exact reviewed intent,
reproducibility proof, isolated frontend/Backend package boundaries and actual
runtime serving of exact JSON build metadata. The adapter used the pinned
Node 22.23.2/Yarn 1.22.22 toolchain; host versions were dispatcher inputs.

| Artifact | ID |
|---|---|
| Host 20.20.2 clean-clone evidence | `13970903190` |
| Host 24.19.0 clean-clone evidence | `13971013205` |
| Host 20.20.2 generated outputs | `13971028004` |
| Host 24.19.0 generated outputs | `13970323822` |

PR1898 was marked ready and normally merged at M. Root independently checked
the J/B parents, complete B/M tree equality and intent-only A/B diff. These
checks establish a reviewed, merged deployment input; live acceptance remains
the separate step below.

### Exact merge-M CI

All **three workflows on exact M succeeded**, independently queried by root:

| Workflow | Successful run |
|---|---|
| Mezan Release Readiness | [43279458043](https://github.com/AMASI-SA/AMASI-SA/actions/runs/43279458043) |
| Security Gate | [43279458121](https://github.com/AMASI-SA/AMASI-SA/actions/runs/43279458121) |
| CodeQL | [43279458306](https://github.com/AMASI-SA/AMASI-SA/actions/runs/43279458306) |

M's clean-clone adapter rehearsals also passed: Host Node 20.20.2 job
`131397159682` and Host Node 24.19.0 job `131397159658`. These are the final
merged-source CI results, in addition to the exact-A and exact-B gates above.

## Evidence semantics

The report distinguishes known amounts from complete totals. Missing source
evidence, fees, wallet statements and historical shipping approval are not
fabricated. Daily-only expense sources do not prove partial-day allocation;
financing and income-tax inputs with no available source contract remain
explicitly unknown. A partial known subtotal is not certified as full net profit.

## Publication authorization

Earlier automatic approval review required explicit authorization to disclose
the task source and operational documentation to the verified public repository
`https://github.com/AMASI-SA/AMASI-SA.git`. The owner answered the concrete
destination-specific request with "ارفع وادمج وانشر". Upload, merge and governed
publication are now authorized. The previous authorization blocker is resolved.
The task PR records exact remote candidate, CI, intent and merge identities.
The separate documentation branch records the handoff without changing the
frozen source/intent pair.

## First publication — VERIFIED

**Production changed through this task: YES.** One Republish → Publish changes
submission was made after a successful official operator rehearsal and
prepare/prepublish. At that first publication the platform UI showed the newer green
**Deployment finished — Nov 12, 09:40 PM**, and published platform version
`7112316`. This is the exact current UI label; the platform version is separate
from the Git deployment SHA M.

### Operator and publication evidence

- Shared `/app` was tracked-clean and its official guard reported `active:false`
  before the fast-forward to M. Unrelated untracked files/services were preserved.
- The official adapter passed in **67.78 seconds**, with source A, deployment M,
  the frozen runtime identity and isolated package/JSON metadata contracts.
- Two generated Backend bytecode files were preserved outside governed paths at
  `/tmp/mezan-operational-1898-preserved-54b6`; preview Backend was stopped for
  the governed build. No tracked source was discarded.
- Official prepare actor: `operational-balances-1898-20261112`.
- Prepared at: `2026-11-12T18:29:29.890577+00:00`.
- Official prepublish returned `ready_to_publish:true` for exact A/M/runtime.
- Official live verify completed with **`verified:true`, `checks:3`**, source A
  `b23bed75f09e52b6faf4f985de00dd56a170d59c`, deployment M
  `54b6fcf56fbe329a0fba7200b130eeca8ce5983c`, and runtime
  `rg5-b275cea8a671baf43897993c2925f54ed3cc85a1cc454e927dda14ee67ab4f34`.
- Verified live boot: `2026-11-12T18:40:16.192653+00:00`.
- Verified Frontend artifact tree:
  `47e182eb6c963fadeef16d9606c22fed381c5a75be24495b9533fe746c2fdee1`.
- Each probe checked **582 Frontend requests** across canonical and cache-busted
  URLs, exact metadata JSON/MIME, HTML shell and retirement service workers.
  All three probes passed the full Backend/source/identity/hash contract.
- The official final guard reported **`active:false`**. Under another fresh
  idle check, `supervisorctl start backend frontend` returned both **started**.
  This is the observed start-command response, not proof that the frontend
  remained healthy. A later prepublication check found the legacy frontend
  **FATAL** with an old launcher referencing a missing Node binary. Backend was
  running before this task stopped it for the corrective build. The first
  publication lease closed; corrective lease closure and backend-only
  restoration remain separate pending steps below.
- UI runtime logs exposed healthy new backend starts and zero displayed error
  logs. They did not expose the complete Cloud Build argv/cwd/package transcript;
  those platform internals are not asserted from the operator rehearsal.

## Corrective acceptance investigation — PR1899 history

The first visible load of `/operational-balances/financial-position` returned
`408 operational_refresh_timeout` after the governed refresh deadline. The UI
showed the exact saved-snapshot timeout message but no tables. Source inspection
confirmed that the refresh helper omitted this exact recoverable408, so the
component never issued its saved-report GET. This observation does not establish
that the saved GET itself timed out.

Superseded corrective draft: [PR1899](https://github.com/AMASI-SA/AMASI-SA/pull/1899).
Remote branch: `codex/operational-balances-timeout-fallback-20261112`.
Remote recoverable checkpoint: `c0811f99944b7138094da52e108356db2f72bf1c`, tree
`44d02410a6c958c6669c55bf4c013d15373db416`, parent M.

Completed corrective checks before final integration:

- Local `5221bfd6729cc27b214528b32a9dfc5f38f4e22f` has the same tree as that
  remote checkpoint. It recovers only HTTP408 plus `operational_refresh_timeout`.
  An actual first-mount component regression failed before the fix with zero
  saved GETs, then passed with one scoped GET, dated saved money/rows, warning,
  no repeat/hidden/focus/online refresh and preserved403 denial. **129 tests in
  four suites passed** under pinned Node22.23.2.
- Local `6f7b448fbf7f8dfb7b501b66de7ea75019b89198` raises only the saved financial
  report client budget from45s to60s, giving headroom beyond its optional45s
  source reader. AbortSignal and explicit overrides are preserved; refresh85/90s
  limits are unchanged. **47 tests in three suites passed**. Actual live saved
  GET duration remains unobserved.
- A bounded harness proved that B already-recorded bank orders added **5B serial
  Mongo commands and B×M movement visits**. At B500/M2000, the full mutation
  fixture used2,595 commands before the indexed preparation and105 after it;
  movement visits dropped from1,000,000 to2,000. This is synthetic evidence,
  not a production latency measurement. Every new claim and final commit still
  require fresh authorization/lease admission. The bank fix is local commit
  `466a3b0d799d46eda16d0c6d55b977b3532ecc5e`, followed by compatibility commit
  `f807aa04846d7e7f26f391e807683e4e2b502602`. **83 affected cases and12 fresh
  isolated Mongo cases passed on the final code**;50 Mongo cases had also
  passed before the narrow legacy metadata follow-up.
- Independent review found unrelated historical dict/list identity metadata
  could break the new hash index. The compatibility correction ignores only
  the malformed index key while preserving the other valid key of that same
  movement. Four actual-refresh regressions pass, and the independent reviewer
  reran **20 focused tests successfully** on the final file hashes. An additional
  deterministic comparison matched the old decisions in5,003 cases.
  A source-bounded20,000-conflict loop took15.36ms in review and returned499
  with zero pending tasks on disconnect; no detached task or financial write
  escaped that cancellation case.

The frontend budget follow-up was cherry-picked into the bank integration at
`e1818a86a490a98e172948c0520b38e32457d94c`, tree
`ea638e55fd3b22cd08f6612304c6002c56ae0186`, before this status-only update.
Final reviewed bank-service SHA256 is
`1b45adc8decdd113569afeaa7c585da4f9b576119c33ff849bd6c3c44ef2ff89`;
its regression-file SHA256 is
`6e1beb4de59409d210775d833cfea78b28c23fc269fed7ad3111aac85b0b6718`.
Root verified these hashes survived the frontend integration unchanged.

At those local checkpoints, a corrective source/intent pair, merge and
publication were not yet complete. The later A2/B2 and final A3/B3/M3 evidence
is recorded separately below. Original A/B/M and its frozen branch remain
unchanged. No manual financial approval, payout or advertising operation was
performed for the release or acceptance.

## Corrective pair A2/B2 — VERIFIED, SUPERSEDED BEFORE MERGE

- Source A2: `ccb56a3e17bde24dc120f0cbbe1cdfa83c3ee31a`, tree
  `ad24e4502f56eed0703b37aafbda78daa2b1e47b`.
- Intent-only B2: `12fd16617436206485d5a583b0ecde19968f67d2`, tree
  `75dbb8ab300b4731bb73b078cfd834d011b63caf`.
- All11 exact-A2 workflows succeeded. Root independently read logs showing
  3,610 backend cases,4 canonical-worker cases,16 shipping cases plus18 subtests,
  669 frontend tests in50 suites, and all6 Chromium scenarios passed.
- Exact-B2 completed11 successful workflows and one expected branch-specific
  TikTok candidate skip. Both Host20.20.2 and24.19.0 clean-clone adapter rehearsals
  passed in run43289660856, jobs131432661172 and131432661123.
- Root validated the exact candidate ZIP digest, candidate payload/Git manifests
  in a clean A2 checkout, then the official tracked-intent contract in clean B2.
  B2 has one parentA2 and changes only the intent. No squash/rebase occurred.
- B2 was not merged or published. Its frozen branch and PR1899 remain recoverable.

## Current Production integration — MERGED (PR1901)

The final pre-merge ref read found Production at
`8379ca2560381bd3c626586d17aa63eb47e2a16c` (PR1897 campaign hover source merge),
not at the previously verified deployment M. Its six source paths have **zero
intersection** with the seven operational corrective paths. The merge retains
M's exact old intent blob `e01eac8b75feb9a2f8d54ac490b509ae4c1f89be`.
At that pre-merge checkpoint the authenticated platform still displayed
published version `7112316`, and no corrective publication had been submitted.
The later corrective submission is recorded below without claiming it finished.

The current-production branch started at 8379 and applied the corrective
source changes without their frozen B2 intent. All six campaign-hover files
remain exactly as merged in 8379. The operational application/test files remain
exactly as tested in A2. The trusted prior reviewed deployment J3 remains
`54b6fcf56fbe329a0fba7200b130eeca8ce5983c`.

The existing automatic PR candidate-base resolver deliberately rejects staged
non-documentation changes. Root used the repository's documented local governed
A/B source build and explicit trusted J3 to freeze the new intent, then required
the new B3 gates. The reviewed-intent path permits the staged Production 8379
predecessor because it is an ancestor of A3. Release guards/workflows were not
changed, and no CI markers were forged.

### Final immutable identities — A3/B3/M3

| Identity | Exact value |
|---|---|
| Trusted prior reviewed deployment J3 | `54b6fcf56fbe329a0fba7200b130eeca8ce5983c` |
| Preserved staged Production predecessor | `8379ca2560381bd3c626586d17aa63eb47e2a16c` |
| Source A3 | `549c6c733e8f93f5c632f6473263f3eb37f01767` |
| Source A3 tree | `6e0c9d87543ca3d4311a374bf231a6dfb619a146` |
| Intent-only B3 | `854dc1b6920228300346e5ec91396562eb765235` |
| Normal merge M3, PR1901 | `2c875b5f16b0d58e46effdd7acd28a91041b62fb` |
| Reviewed B3 and M3 tree | `706980488f7d42f75d40bd789922b6ea7be69ac0` |
| Runtime release ID | `rg5-e5641d97ab8215c663458630c2af0dcd839b3aed89a05fcdc66be465b036c60e` |
| Frontend artifact tree SHA256 | `e47bec1b5e0170d843cce0f37e08668566a38513eefe84b9fa1098964250d4fe` |
| Tracked intent Git blob | `de17bc5e98c0161c3d1233506ba94e037ef189b1` |
| Tracked intent SHA256 | `9c54d20f9c633d107e7b7323207170d1563435f098685774080a00b7de015d0c` |
| Tracked intent bytes | `1,291,050` |

B3 has exactly one parent, A3, and changes only the tracked release intent.
M3 has exactly two ordered parents: staged Production
`8379ca2560381bd3c626586d17aa63eb47e2a16c` and B3. Its entire tree equals B3.
The handoff worktree verified these merge parents/tree from the fetched exact
M3 object. No source or intent in the frozen pairs was rewritten for this
documentation draft.

### Final B3 gates

The pre-merge B3 gate completed **11 successful workflows plus one expected
TikTok branch-specific skip**, with no failed or pending required workflow.
The skip does not omit operational acceptance cases. The exact-B3
[Operational Balance Web run 43292182652](https://github.com/AMASI-SA/AMASI-SA/actions/runs/43292182652)
passed the full affected gates recorded by root:

| Gate | Observed result |
|---|---|
| Operational backend and affected compatibility suites | **3,610 passed** |
| Canonical server-child boot/drain on isolated Mongo | **4 passed** |
| Shared shipping summary and calculator compatibility | **16 passed, 18 subtests passed** |
| Operational/supplier/employee frontend | **669 passed, 50 suites passed** |
| Sandboxed Chromium actual-component acceptance | **6 of 6 passed** |

Exact-B3 [Mezan Release Readiness run 43292182835](https://github.com/AMASI-SA/AMASI-SA/actions/runs/43292182835)
also succeeded. The synthetic tests and CI browser scenarios remain distinct
from the pending authenticated live acceptance of the corrective publication.

### Final merge-M3 CI — independently verified

The all-event GitHub query restricted to exact M3 at
**2026-11-12 19:54:39 UTC** returned `total_count=3` and all three records.
Each had the exact M3 head SHA, event `push`, attempt 1 and
`status:completed`, `conclusion:success`:

| Workflow | Successful run |
|---|---|
| Mezan Release Readiness | [43293557946](https://github.com/AMASI-SA/AMASI-SA/actions/runs/43293557946) |
| Security Gate | [43293558027](https://github.com/AMASI-SA/AMASI-SA/actions/runs/43293558027) |
| CodeQL | [43293557903](https://github.com/AMASI-SA/AMASI-SA/actions/runs/43293557903) |

Final exact-M3 count: **3 success, 0 pending, 0 failed, 0 skipped**. CodeQL
completed at `2026-11-12T19:54:19Z`. B3's results were not substituted for this
merge-specific evidence.

## Corrective publication — OFFICIALLY VERIFIED, LIVE REFRESH STILL OPEN

Root owns the operator/build/lease/publication evidence below. This handoff
records those operations without changing the frozen source or intent.

- The operator's tracked source was clean; **120 unrelated untracked paths**
  were preserved. **Two generated Backend bytecode files** were backed up
  outside governed source before the build. No tracked source was discarded.
- Preview backend was running and was stopped by this task for the build.
  The legacy preview frontend was already **FATAL**, with an old launcher
  referencing a missing Node binary; it was not a healthy service stopped by
  this task. After corrective lease closure, backend alone was restored.
- The official governed operator build passed in **74.43 seconds**, bound to
  exact source A3, deployment M3 and the runtime identity in the final table.
- Official prepare actor: `operational-balances-1901-20261112`.
- Official prepare succeeded at `2026-11-12T19:54:39.613387+00:00` for exact
  A3/M3/runtime. Official prepublish returned **`ready_to_publish:true`**.
- One **Publish changes** click was submitted at approximately **19:55 UTC**
  on 2026-11-12. Emergent subsequently displayed green **Deployment finished**,
  November 12, **11:05 PM Asia/Riyadh**, platform version **275e412**. No repeat
  publication was submitted while this build was running.
- Official `verify --url https://mezansalla.com` completed with
  **verified:true, checks:3**, exact A3/M3/runtime/artifact identity above,
  backend boot `2026-11-12T20:04:58.384183+00:00`, and **582** checked canonical
  and cache-busted Frontend requests in each probe. Official status then
  reported **active:false**.
- Root independently read the verifier result, obtained fresh idle status,
  restored the task-stopped preview backend, observed **RUNNING**, and asserted
  tracked source remained clean. Legacy preview frontend stayed untouched.
- A fresh signed-in, visible financial-position load again reached the refresh
  deadline. The corrected fallback displayed **21 tables**, the last saved
  calculation time and the explicit refresh-failure warning. Known carrier and
  driver balances, delivery counts and distinct unresolved-order counts were
  retained. No real financial amounts, customer data or order identifiers are
  recorded in this public handoff.
- Actual driver and carrier dialogs showed order numbers and specific reasons.
  The carrier dialog paginated correctly across ten rows then five rows;
  Escape dismissed each dialog. This validates the real saved-report UI.
- The displayed saved calculation was still **01:07 Asia/Riyadh on November 12**.
  Fresh refresh and current forecast/profit completeness are **not accepted**.
  The follow-up must produce a successful new visible calculation before the
  overall operational-balance task can be marked complete.

Production changed through the overall task: **YES**, established by the
verified initial and corrective publications above. No new follow-up source
has been merged or published at this documentation checkpoint.

### Follow-up work above verified M3

- Follow-up Draft PR: [1902](https://github.com/AMASI-SA/AMASI-SA/pull/1902).
  Remote WIP checkpoints `82bf5f1bbe2f961e9635bcabea8c7bf197ae1c97` and
  `e6499740721e0f981ce2e5f61a5d67809368429e` are recoverable. The first passed
  its complete operational gates (3,628 Backend cases, four canonical-worker
  cases, 669 Frontend cases in 50 suites, and six Chromium scenarios). The
  second includes the automatic delivery rule but **is not release ready**:
  exact-head runs 43308794842 and 43308794827 failed the same eight legacy
  driver-COD report cases and one prepaid totals assertion. Their passing
  counts were 3,672 and 3,481 respectively; all ten other exact-head workflows
  succeeded. The isolated regression lane found an order-number-only legacy
  assignment no longer reaches the native collection adapter. A narrow
  compatibility correction must retain strict new delivery-proof identity
  checks and all established COD amounts. The added `delivered_orders` totals
  field also requires the existing prepaid contract to be checked. These
  failures supersede any inference of release readiness from focused tests.
  The new manual confirmation is still separate work in progress. Root's
  latest Production ref read remains exact M3. No follow-up release has been
  merged or published.
- The nine compatibility failures were subsequently fixed at isolated local
  commit `988f4abda7df5727f7b203999c6a9b999cef4baf` and root-integrated as
  `a7fd7f5`. The collector preserves the established native collection adapter
  only for a single owner-scoped legacy assignment with a unique assignment
  identity; it does not fabricate the canonical order identity needed by the
  new delivery proof. Held-evidence COD guards remain unchanged. All **76
  affected cases passed on disposable real Mongo**, including the prior nine
  failures, ambiguous/foreign/wrong-order/reused-assignment rejection and
  preservation of established cash, bank-transfer and card collection evidence.
  The prepaid totals assertion now includes the legitimate zero delivery count.
  Fresh combined CI is still required after the manual feature is integrated.
- Root integration branch/worktree:
  `codex/operational-delivery-refresh-completion-20261112` /
  `/workspace/scratch/e00762959313/mezan-operational-delivery-refresh-completion`.
  The piece index was integrated from local commit
  `2c74cb0eeb90d70d19a82ad063eac6d07ce2d9f9` after review; its 78 focused cases
  passed. Root added fixed-name, duration/outcome-only timing records around
  existing native acquisition, source collection, calculation, profit, claims,
  catalogs and snapshot phases. They emit only within a visible refresh, add no
  task or read, and omit identities, amounts and exception details. The actual
  route/deadline/privacy regressions and existing demand/bank gates passed
  **55 tests in 2.97 seconds**. An initial invocation named a nonexistent test
  path and ran zero tests; the corrected final invocation passed. This is a
  WIP checkpoint; the automatic delivery rule is now integrated, while the new
  manual confirmation and final exact-source CI remain pending. Root's combined
  collector/index/rule/timing/demand/bank run passed **184 cases in 7.48 seconds**;
  the two real-Mongo rule cases were deliberately excluded from this local
  in-memory run and passed in the author's separate 516-case Mongo gate.
  Independent review of the timing diff found the original read/auth/CAS order
  unchanged and reran its five new tests successfully.
- Latest documentation-only publication checkpoint:
  `8ae41338d1c8133f94fd324e9984a5ce2ef593e5` on the handoff branch. PR1901's body
  now records verified publication, real saved-page acceptance and remaining
  timeout; it no longer describes that publication as pending.
- Delivery-rule branch/worktree:
  `codex/operational-delivered-created-after-start-20261112` /
  `/workspace/scratch/e00762959313/mezan-operational-delivery-start-rule`.
  The implementation preserves unknown actual delivery time, separates COD,
  counts proved deliveries independently of fee completeness and avoids native
  history reads solely to fill a timestamp for a qualifying post-start order.
  Completed and independently reviewed at local immutable commit
  `5185a01d9992b04dca87f6eaa02d3758d7645644`, tree
  `56baae522240dbb4410683330e50f2322cac020a`, then integrated by root without
  conflicts. Author verification: **516 backend cases** with isolated real
  Mongo, **65 UI cases / 3 suites**, then **19 DomainDetails cases** after the
  final label clarification. Independent review reran **51 cases**, excluding
  the two Mongo cases covered by the author's gate, and found no outstanding
  concrete defect on that exact commit. Existing contradictory timestamps,
  future/newer negative status history and the first-observation boundary stay
  explicit conflicts; older in-transit history remains allowed. Source/COD
  holds preserve known rows instead of rebuilding a false zero.
- The new manual boundary-attestation feature is a separate following change,
  still in progress. Plan: one-order bounded visible preflight, owner-only
  version-bound/idempotent confirmation and one visible reconciliation. If the
  proof is saved but refresh fails, show that saved confirmation as awaiting
  calculation rather than invite duplicate approval or claim fresh money.
  Root owns the new synthetic Chromium scenario and CI gate; the delivery
  implementer owns the proof/routes/service and the assigned UI contributor
  owns the dialog/API integration. No live confirmation is authorized here.
- Narrow piece-index branch/worktree:
  `codex/operational-refresh-stage-analysis-20261112` /
  `/workspace/scratch/e00762959313/mezan-operational-refresh-stage-analysis`.
  A repeated item-by-piece scan in the actual source collector is replaced by
  one request-owned, cancellable and joined index build. On identical synthetic
  20,000-order/20,000-piece inputs, exact M3 took 22.897864 seconds and the
  candidate took 2.295125 seconds, with the same 20,000 known-cost results and
  354 source reads. This is local performance evidence, **not** proof that it
  is the sole cause of the live 85-second refresh timeout. Existing fresh
  authorization, bank claims, deadlines and final CAS remain unchanged.
- Root owns integration, new source/intent freeze, exact-head CI and the next
  governed publication. Do not modify the frozen A3/B3/M3 release.
- The operator IDE was suspended after the completed restoration observation.
  A normal Code -> Open in VS Code action reopened its code-server sign-in page.
  No credential has been read, copied or entered by this task. If sign-in still
  blocks the final operator stage after code/CI are reviewable, use the secure
  browser authentication flow. Do not use paid Emergent agent chat or Copilot.
- A read-only diagnostics command was pasted before the suspension but no
  execution/result was observed. Do not claim the diagnostics token exists or
  that runtime phase profiling succeeded. No token value was exposed.

## Continuation

Issue1006 is at its comment limit. PR1898 records the verified first publication;
PR1899 records the preserved A2/B2 verification and the reason it was superseded;
PR1901 records the verified A3/B3/M3 corrective release. Its comment6835728773
records the completed official verification, live recovery acceptance and
remaining timeout. This documentation-only handoff preserves that state and
the newly authorized delivery rule. Root owns the next integrated checkpoint.
No unrelated draft PR, including the campaign's separate intent draft, is merged
or changed by this task. No financial approval, payout or advertising operation
was performed.

Next safe actions, owned by root:

1. Finish and independently review the two bounded follow-up implementations.
   Integrate above a fresh Production ref, preserving unrelated changes and
   using M3 as the last officially verified source base J.
2. Run integrated calculation, historical-boundary, delivery count/export and
   cancellation checks. Keep all new work request-owned and visible-page only.
   Preserve known saved counts and balances when individual evidence is missing.
3. Preserve a remote task checkpoint and Draft PR, then freeze new source A and
   intent-only B using the repository's governed build. Require exact-B gates
   and both host clean-clone rehearsals before normal merge.
4. Recover operator sign-in if needed; inspect fresh guard/head/clean status and
   service state before any /app change. Continue the already authorized normal
   merge/publication protocol with one prepare/prepublish/publish and official
   three-probe verification. Never reuse the closed M3 lease.
5. Accept a fresh visible calculation on the live site, actual delivery-rule and
   modal behavior, and no hidden/background work. If the refresh still times out,
   obtain current bounded runtime evidence and finish the defect instead of
   substituting the old saved snapshot for fresh acceptance.
