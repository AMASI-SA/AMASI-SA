# Operational balances completion — 2026-11-12

## Current state

CORE RELEASE MERGED, PUBLISHED AND VERIFIED; LIVE ACCEPTANCE FOLLOW-UP OPEN.
The owner explicitly authorized public upload, merge and publication after the
public destination was identified. PR1898 reached production and passed the
official three-probe release verifier. The matching lease is closed and preview
backend/frontend services were restored. The first authenticated live financial
report load exposed a timeout recovery gap and redundant bank-refresh work.
Corrective PR1899 completed all source and intent gates, but pre-merge checking
found an additional source-only Production merge. A new current-production
branch preserves that merge and all verified operational fixes. No corrective
publication is claimed yet.

- Repository: [AMASI-SA/AMASI-SA](https://github.com/AMASI-SA/AMASI-SA).
- Release PR: [1898](https://github.com/AMASI-SA/AMASI-SA/pull/1898), normally
  merged into `hotfix/prod-snap-meta-final` without squash or rebase.
- Frozen release branch: `codex/operational-balances-completion-20261112`.
- Corrective task branch:
  `codex/operational-balances-current-production-20261112`, based on staged
  Production merge `8379ca2560381bd3c626586d17aa63eb47e2a16c`.
  The initial frozen A/B and superseded corrective A2/B2 remain unchanged.
- Implementation worktree: `/workspace/scratch/e00762959313/mezan-operational`.
- Corrective integration worktree:
  `/workspace/scratch/e00762959313/mezan-operational-current-production`.
- Root owns final integration, operator rehearsal, lease, publication and live
  acceptance. Other contributors have no publication or lease action.

### Immutable release identities

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
prepare/prepublish. The current platform UI shows the newer green
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
  No lease remains for this publication.
- UI runtime logs exposed healthy new backend starts and zero displayed error
  logs. They did not expose the complete Cloud Build argv/cwd/package transcript;
  those platform internals are not asserted from the operator rehearsal.

## Corrective live acceptance — OPEN (PR1899)

The first visible load of `/operational-balances/financial-position` returned
`408 operational_refresh_timeout` after the governed refresh deadline. The UI
showed the exact saved-snapshot timeout message but no tables. Source inspection
confirmed that the refresh helper omitted this exact recoverable408, so the
component never issued its saved-report GET. This observation does not establish
that the saved GET itself timed out.

Corrective draft: [PR1899](https://github.com/AMASI-SA/AMASI-SA/pull/1899).
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

No frozen source/intent pair, merge or publication for the corrective release
is declared complete by these local checkpoints. Original A/B/M and its frozen branch remain
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

## Current Production integration — OPEN

The final pre-merge ref read found Production at
`8379ca2560381bd3c626586d17aa63eb47e2a16c` (PR1897 campaign hover source merge),
not at the previously verified deployment M. Its six source paths have **zero
intersection** with the seven operational corrective paths. The merge retains
M's exact old intent blob `e01eac8b75feb9a2f8d54ac490b509ae4c1f89be`.
The authenticated platform still displayed published version7112316 when last
observed; no corrective task publication has occurred.

The current-production branch starts at8379 and applies the two corrective
source commits without their frozen B2 intent. All six campaign-hover files
remain exactly as merged in8379. The operational application/test files remain
exactly as tested in A2; this status record records the new integration.
The trusted prior reviewed deployment J remains
`54b6fcf56fbe329a0fba7200b130eeca8ce5983c`.

The existing automatic PR candidate-base resolver deliberately rejects staged
non-documentation changes. Use the repository's documented local governed A/B
source build and explicit trusted J to freeze the new intent, then require the
new B's normal CI and clean-clone adapter rehearsals. Its reviewed-intent path
already permits the staged Production8379 predecessor when8379 is an ancestor
of the new source. Do not alter release guards/workflows or forge CI markers.

## Continuation

Issue1006 is at its comment limit. PR1898 records the verified first publication;
PR1899 records the preserved A2/B2 verification and the reason it was superseded.
The new current-production task branch will carry the new source/intent pair.
No unrelated draft PR, including the campaign's separate intent draft, is merged
or changed by this task. No financial approval, payout or advertising operation
was performed.

Next safe action: checkpoint this integrated source above8379 remotely; perform
the official pinned local A/B build with the reviewed public client origin;
freeze an intent-only child using trusted J; require all applicable new-B gates
and both host clean-clone rehearsals; recheck the current Production ref; normal
merge with exact reviewed tree; fresh shared guard idle check; operator rehearsal,
prepare/prepublish, one corrective publication and official three-probe verify.
Repeat authenticated live financial-report/dialog acceptance and restore preview
services under a fresh idle check. Do not claim completion from CI or the first
published release alone.
