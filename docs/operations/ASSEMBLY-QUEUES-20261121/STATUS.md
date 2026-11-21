# ASSEMBLY-QUEUES-20261121 — continuation record

Status: implementation verified locally and preserved in two draft PRs. No production change by this task. Not merged or deployed. This is a recovery/handoff record, not a release intent.

## Recoverable source

| Component | Repository | Reviewed base | Source checkpoint | Draft PR |
|---|---|---|---|---|
| Backend | AMASI-SA/AMASI-SA | `88af38f01cf979acbca59b95a95e661828f2306f` | `0a6b4baaf5a545a592bb5321e3bcb59ebd490743` | [#2050](https://github.com/AMASI-SA/AMASI-SA/pull/2050) |
| Employee app | AMASI-SA/amasi-mobile | `e06ce56faf467d5e8ec06ce023f51f86f9cabf21` | `2868b19c70a49b0cabd4b77476d8b018cc0dd5c6` | [#373](https://github.com/AMASI-SA/amasi-mobile/pull/373) |

Task branch in both repositories: `codex/assembly-queues-fast-read-20261121`.
Backend target: `hotfix/prod-snap-meta-final`. Mobile target: `codex/assembly-print-lifecycle-20261108`.
Source checkpoint SHAs above are immutable and recoverable. The later documentation-only checkpoint on each task branch adds this record; verify its parent/source diff before continuing.

## Accepted behavior

| User-visible page | Required membership |
|---|---|
| مكتملة التجهيز (`supplier_received`) | Current canonical order status is `in_progress`; every required supplier product unit is received. Mixed direct/supplier orders are allowed when all supplier units are received. Employee custody receipt and marking Ready are not prerequisites for listing. |
| جاهزة للشحن (`direct_preparation`) | Current canonical order status is `in_progress`; every current order product is routed directly to preparation; no physical supplier-route product remains. |

- Each page requests 10 order rows. Global red footer badges count orders and are independent of search and pagination.
- Tables mount lazily. No hidden-tab list prefetch or hidden/background list polling. Visible-board polling remains visibility-bound.
- Footer counts come from the accepted active-board response. Search can read only the summary once per visible visit/foreground; it does not load either table or run a separate count timer.
- Successful empty queues show clear Arabic empty text. Failed/timed-out reads show retry; unavailable counts are not converted to false zero.
- A table row/order-number press opens `/assembly/search` and automatically loads all products for that order. A unique navigation intent handles cached tabs, queries arriving before focus, and reopening the same order after another manual search.
- The existing receipt, Ready, service completion and shipping/printing write contracts are retained.

## Backend read design

Current source orders are filtered by tenant and conservative active-status facts, using a small projection and workflow/piece batches of 100. The old active-workflow history cap of 5,000 is removed. The canonical mapper is reused for status/payment parity without hydrating full customers, images, timeline or order items.

Queue membership validates current source item identities and quantities, ignores operational rows in that comparison, and requires distinct valid supplier unit positions. Contradictory terminal statuses fail closed. Exact counts are computed over active membership facts before search/page selection; only the requested table rows are returned. This is not a claim that only 10 database facts are read.

`GET /preparation-work-v1/assembly/order-counts` returns only global counts, polling hint and source identifier. Active reads, including index initialization, have a 10-second deadline and explicit retryable failure. The preparation reader reuses the existing nonunique `ix_preparation_piece_order_cost_v1` index on `(user_id, order_number)`.

## Original queue verification

| Check | Result and scope |
|---|---|
| Backend focused suite | 133 passed, exit 0, after the shared-index correction. Covers queue membership, status aliases/conflicts, current item/quantity drift, unique supplier units, global counts, pagination, timeout, index reuse in both startup orders, and report index/reuse regressions. |
| Mobile runtime regression | 12 groups passed against actual Board/layout/repository/route/Stage code with a synthetic API boundary. |
| Mobile lifecycle/read gates | Board loading, P0 visibility, on-demand read cancellation, workspace routing and Ready-priority checks passed. |
| Mobile static checks | Scoped TypeScript and default ESLint on all eight changed production modules passed. This is not a whole-repository TypeScript claim. |
| Existing on-demand/print preservation gate | Passed, including 10 guarded label cases, no extra active-board count request, background/focus behavior and Ready/PDF protections. |
| Independent review | Projected/canonical payment/shipping parity cases and focused membership regressions passed. |

Backend final command (from repository root, using an environment with the pinned test dependencies):

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=backend:backend/tests python -m pytest --noconftest -q \
  backend/tests/test_assembly_board_read.py \
  backend/tests/test_assembly_board_groups.py \
  backend/tests/test_fulfillment_read_performance.py \
  backend/tests/test_preparation_piece_operations.py \
  backend/tests/test_supplier_invoice_reporting_indexes.py \
  backend/tests/test_supplier_invoice_reporting_reuse.py
```

Mobile behavioral commands (from `frontend`):

```bash
node scripts/verify-assembly-queue-counts.cjs
node scripts/verify-assembly-board-loading.cjs
node scripts/verify-p0-assembly-visibility.cjs
node scripts/verify-on-demand-read-cancellation.cjs
node scripts/verify-assembly-workspace.cjs
node scripts/verify-on-demand-assembly.cjs
```

The last local command used an in-memory replacement only for six historical `git show` baseline reads because the local checkpoint lacked commit `64c6f1378829b274b03907dc52005894308cf0b2`. All six replacement files were fetched at that exact ref and Git blob hashes verified. Gate source and assertions were unchanged. Two current courier dependencies initially came from an older local checkpoint; restoring their exact pinned mobile-base bytes resolved the unrelated foreground assertion. None of those unchanged baseline restorations is included in the PR.

An additional local supplier benchmark-oracle attempt could not load historical commit `bcdd6e501e629ee715667c39d1b43aab5b263914`. Its oracle was not edited or bypassed. The full-history CI gate is the authority for that benchmark.

## Original queue CI snapshot and limits

Snapshot: 2026-11-21 23:35:48 Asia/Riyadh (UTC+03:00). Verify current PR checks again before any merge or release.

- On backend source `ac8826821121903f0933d5834e6eb2dc1c4f275b`, [Supplier cost report performance](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44946405995/job/136974590140) passed on real Mongo 8.0.12. The oracle/index phase passed 33 tests; the benchmark passed with 6,000 orders, 6,000 history rows and 5 repetitions. This proves the shared-index conflict is resolved against real Mongo, and does not measure the production assembly API latency.
- [Page and operational read performance](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44946406124), [Production Preparation Piece Operations](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44946405980), Assembly settings/direct receipt, Fulfillment V2, G47, Shipping Print Boundary and Release Readiness also passed on that exact source. Broader independent checks were still running at this snapshot; do not treat this as all repository CI being green.
- The initial backend checkpoint `777a0be07ea3dd6f7aa6cdcdb4984a1e113fc869` failed the supplier benchmark at Mongo index initialization with error 85 `IndexOptionsConflict`: the same keys already existed as `ix_preparation_piece_order_cost_v1`. The corrected source reuses that canonical name, without deleting indexes or changing report/financial logic. A regression now checks both initializer orders and repeated initialization.
- Mobile CI is **not green**. Across seven initial workflows, eight failed jobs on source `986983c71a7b67aeadd057ee13b97f02187215b5` had no recorded steps and no runner assignment. A single controlled retry of [AMASI Mobile CI](https://github.com/AMASI-SA/amasi-mobile/actions/runs/44946024689/job/136975199082), attempt 2, failed again before execution (`steps=[]`, `runner_id=0`). It ran from 23:30:12 to 23:30:14 Asia/Riyadh on 2026-11-21. No checkout, compilation or tests executed in that attempt.
- The exact mobile job-start cause is unresolved. Each failed check has one annotation, but annotation bodies were unavailable through the connector; title/summary/text were null and log retrieval returned BlobNotFound. Do not label this as billing, quota or a source regression without the missing evidence. No CI gate was disabled or weakened. Local passed tests are separate evidence.

No live production reads/writes, real Android-device UAT, native printer testing or production latency measurement was performed. Optional transaction integration tests requiring `MZ2_TEST_MONGO_URI` were unavailable locally. Do not infer a measured production latency from synthetic test timings.

## Original queue checkpoint integrity and preserved work

All changed source files were read back from the remote commits and matched their local Git blob hashes. The backend parent advanced while work was in progress; unrelated carrier-label recovery and release-intent changes were preserved by building on `88af38f01cf979acbca59b95a95e661828f2306f`. At the original queue source checkpoints (`ac8826821121903f0933d5834e6eb2dc1c4f275b` and `986983c71a7b67aeadd057ee13b97f02187215b5`), the code changes contained the 5 backend and 15 mobile files below. These tables are historical; the follow-up files and replacement hashes are recorded separately below. Local read-only baseline overlays were excluded.

### Backend files

| Path | Git blob SHA |
|---|---|
| `.github/workflows/page-performance-regressions.yml` | `1041ee97aa5a5e6458cad613ca0803ceeb1f1f1f` |
| `backend/assembly_board_read.py` | `7884a8e81f864d28298f0676ad1a7640a8577d8e` |
| `backend/preparation_piece_operations.py` | `1b05a2af01b74a19bccff9039b3a713c259881f5` |
| `backend/tests/test_assembly_board_read.py` | `396f0f637ebed4d6ea13a79ec517b94349c75fe9` |
| `backend/tests/test_fulfillment_read_performance.py` | `53fe7d255322b2a53971f11124cf785d33834476` |

### Mobile files

| Path | Git blob SHA |
|---|---|
| `.github/workflows/mobile-ci.yml` | `5c111c29811df8c32b0d15c739d1f747e2cf1af1` |
| `frontend/app/assembly/_layout.tsx` | `0f998cd81770866d25206e4944947eaca3f4d0a7` |
| `frontend/app/assembly/search.tsx` | `655601be7ed2008bc89f9feeb464e5736e99ffaa` |
| `frontend/scripts/ui-contract-harness.cjs` | `c00e76b337d3c17328b80904560053f5a995151f` |
| `frontend/scripts/verify-assembly-board-loading.cjs` | `d8c5b9135a9ee7ad1464df0a65b8da4f51d4b0d5` |
| `frontend/scripts/verify-assembly-queue-counts.cjs` | `6f2fdfdec88504bea93201beda8a9c65b4048a7e` |
| `frontend/scripts/verify-assembly-workspace.cjs` | `1922eb9196844d6674e2fa593679bfe8a350be91` |
| `frontend/scripts/verify-on-demand-assembly.cjs` | `27e5560927904a72bab696ee82aff33fa4624b9d` |
| `frontend/scripts/verify-p0-assembly-visibility.cjs` | `44cb4dd839e2bfb20b0befcc80de29584b5156fc` |
| `frontend/src/features/assemblyShipping/AssemblyOrdersBoard.tsx` | `5361f9336987f244e5df5237cabe1df5d60a76ef` |
| `frontend/src/features/assemblyShipping/AssemblyQueueCountsContext.tsx` | `e217d291422f6afdd9ed98b8b57a629d0821682f` |
| `frontend/src/features/assemblyShipping/AssemblyShippingStage.tsx` | `27219a63078c9dba91555aadc4f04eaab9f3e981` |
| `frontend/src/features/assemblyShipping/assemblyQueueCounts.ts` | `097caaf4c1986cad32594999708d2cef9eb53259` |
| `frontend/src/features/assemblyShipping/repository.ts` | `6bdf42dfbc4fee47afbc041ddac9ff00ae70f11b` |
| `frontend/src/features/assemblyShipping/useAssemblyVisiblePolling.ts` | `c80317d9017d6e575cf98bcefa1e82b483aaec9b` |

## Follow-up: personalized product lock notices — 2026-11-22

The recoverable-source table at the top now points to the reviewed lock-notice source commits. These build on the original queue handoffs without replacing their lazy loading, counts, pagination, receipt or shipping changes.

### Accepted notice behavior

- Use the already-loaded product, workflow, current source units and merchant settings to describe the first applicable missing stage: preparation employee assignment, product assignment to supplier, product receipt, named service receipt, or required employee custody receipt. Product assignment and service receipt remain distinct.
- A partial approved supplier receipt can prove physical receipt even when the existing approval path removes `received_at` while services remain. Draft scans and old history after rejection/redispatch do not manufacture receipt.
- Missing or contradictory assignment, receipt or source-unit data produces an uncertainty message. Stale removed source units never get an instruction or a claim of completion.
- When all applicable product stages are complete and the order is under review, show «المنتج مكتمل — حالة الطلب غير مؤهلة», the actual Arabic order state, and a short instruction to move it to «قيد التنفيذ». Only review states receive that instruction.
- Cancellation and advanced order states take precedence over new receipt/assignment guidance. Cancelled orders show «الطلب ملغي» / «لا يحتاج شحن». `completed` means «تم التنفيذ», not proof of shipment. Shipped, delivering, delivered and refunded states show their actual state without instructions to receive the product again or roll back the order. Payment-pending states do not instruct bypassing payment.
- The salutation uses the currently authenticated assembly account's `AuthUser.displayName`; «الختاعي» is an example of the viewer, while «هلال» is an example of the employee assigned to the product. Neither name is hardcoded in production. Missing, blank or email-only names omit the salutation.
- Example: «يا الختاعي، لم تُستلم خدمة تطريز الاسم» followed by «كلّم هلال يستلم خدمة تطريز الاسم من المورد». The actual assigned employee comes from the backend; the frontend does not substitute the viewer's name.
- The cause is bold dark blue (12 / line height 18), with a readable dark navy instruction (11.5 / line height 17), on a pale blue notice. The orange Ready lock/button stays distinct. A friendly blue smiling-face Pressable has a 44×44 touch target, Arabic accessibility label and press-only scale/opacity feedback. It opens the same read-only personalized notice; it does not mark Ready, receive products, poll or start a network request.
- A saved-ready product with a current terminal order notice retains its saved readiness as «تم اعتماد جاهزية المنتج», while the order notice remains visible. It does not simultaneously claim «جاهز للتغليف والشحن» when the order is cancelled. Unrelated stale stage hints remain hidden on confirmed-ready products; existing busy/reconciling ownership is preserved.

The backend adds optional display metadata `assembly_pending_stage` after existing search gates. The client validates this display payload and falls back safely for older responses. No Ready, receipt, service, source-membership, print or permission predicate was expanded. No extra database query or provider call was added. Existing two-column product layout is retained; the notice does not truncate or cap the reason/action text.

### Follow-up verification

| Check | Result and scope |
|---|---|
| Root backend affected suite | 135 passed, exit 0: new pending-stage tests, source-pending-message tests and preparation piece operations. |
| Independent backend suite | 56 passed against the actual `_assembly_search` path. Covers partial service receipts, rejection/stale source, status precedence, unchanged snapshots and read counts. |
| Mobile actual-component regression | 8 groups passed: stage priority, named actions, malformed payloads, truthful fallback, viewer/assignee separation, account changes, missing names, saved-ready terminal states, face/lock Alert equality and no action/read/timer side effects. |
| Mobile static and existing gates | Scoped actual-graph TypeScript, default ESLint on all three changed production modules, existing status/settings and Ready-priority gates passed after the final face/style update. |
| Independent mobile review | Reran all 8 groups against the exact frozen bytes. The discovered saved-ready/cancelled display contradiction was reproduced before correction and verified after correction. |
| CI coverage additions | New backend helper/test are covered by the existing preparation CI and external-write boundary; new mobile behavioral script is included in Mobile CI. No gate was disabled. |

Backend command from repository root:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=backend:backend/tests python -m pytest --noconftest -q \
  backend/tests/test_assembly_pending_stage.py \
  backend/tests/test_assembly_source_pending_message.py \
  backend/tests/test_preparation_piece_operations.py --tb=short
```

Mobile command from `frontend`: `node scripts/verify-assembly-pending-stage.cjs`.

These local checks use synthetic data/host components. The user-visible image is an illustrative mockup of the implemented styling, not native-device UAT. No new device, native printer or production latency result is claimed.

### Follow-up source integrity

The source checkpoints contain exactly four backend and five mobile changed files relative to their prior task handoffs. Each remote Git blob was checked against the frozen local manifest. Unchanged local baseline overlays were excluded. The later handoff commit in each repository changes only this continuation record.

Backend follow-up files:

| Path | Git blob SHA |
|---|---|
| `.github/workflows/prod-preparation-piece-operations.yml` | `d9559c18bf0e14a8bcef99d90dc883013ef3df1e` |
| `backend/assembly_pending_stage.py` | `ffc6c13ab1b8e27d0ca0cef487a640c59431b20c` |
| `backend/preparation_piece_operations.py` | `01f010f0fdec3b8bf86b400fc3206ee1593935c3` |
| `backend/tests/test_assembly_pending_stage.py` | `59f577e2fca9acb160bae27fcf1fcaf26d247783` |

Mobile follow-up files:

| Path | Git blob SHA |
|---|---|
| `frontend/src/features/assemblyShipping/AssemblyShippingStage.tsx` | `4742749dc24576210b87d10a448cedfaa0db4508` |
| `frontend/src/features/assemblyShipping/repository.ts` | `27254a73714e10e5d24793c9b13b27d6269b4f3a` |
| `frontend/src/features/assemblyShipping/assemblyStatusDisplay.ts` | `39c162b1b34f7f104c4d98ad4b447ec69fa56f26` |
| `frontend/scripts/verify-assembly-pending-stage.cjs` | `98b3ef8426e2c9b545be547a6b0e616fab9dbd28` |
| `.github/workflows/mobile-ci.yml` | `f4dfd2b56bf05bef6f30f9082d47733cff07f218` |

### Follow-up CI snapshot

On backend source `0a6b4baaf5a545a592bb5321e3bcb59ebd490743`, Production Preparation Piece Operations, Page and operational read performance, Assembly Mobile Settings and Assembly Direct Preparation Receipt succeeded. The backend preparation job ran 224 passing tests including the new stage suite; the page/read job ran 69 passing tests. These were PR-merge checks into reviewed base `88af38f01cf979acbca59b95a95e661828f2306f`, not production deployment. Shipping Print Boundary was still running at this snapshot; no claim that all repository CI is green is made.

- [Preparation CI](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44953239745)
- [Page/read CI](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44953239804)
- [Assembly settings CI](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44953239908)
- [Direct receipt CI](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44953239889)
- [Shipping boundary CI](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44953239740)

The prior mobile CI job-start limitation remains unresolved; final follow-up mobile source CI must be checked before any release. Local passes do not establish GitHub CI success. No workflow was restarted during this follow-up.

## Next safe action

1. Fetch both remote task branches and verify the source checkpoints and documentation-only successors. Read this status record and the PR checks; do not resume from the older local checkpoint.
2. Resolve the mobile CI job-start failure and run the required checks. No test gate was weakened to make it pass.
3. Complete paired review. Deploy the backend counts/read and pending-stage display contracts before distributing the mobile update, only after the user requests publishing.
4. For backend release, follow the repository `AGENTS.md` protocol v5 from the then-current reviewed production source, prepare a new governed source/intent pair and perform the required release verification. This task created no lease, no release intent, no merge and no deployment.

Production changed by this task: **no**. No production financial, order, supplier, notification, email or message writes were performed. The repository-wide issue ledger was not posted to; this record and the draft PRs are the durable handoff.
