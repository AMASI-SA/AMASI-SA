# Independent CI correction — PR #1321

The original head `b99f9fe30a1310dff1dcb807b4c4eab807a87a2f` was **not accepted**:
six GitHub workflows failed. Earlier local results did not cover their full
execution environments. This follow-up retains Draft status and requires fresh
CI on the final correction head. No merge, release intent, deploy, OTA or
Production mutation is authorized or performed.

## Failure evidence and correction

| Failed workflow/run | Evidence | Correction |
|---|---|---|
| [Supplier Invoice Service Policy](https://github.com/AMASI-SA/AMASI-SA/actions/runs/38082299555/job/114301539668) | 135 passed, 1 failed: dict test DB has no `unified_orders` attribute | Use Motor's equivalent indexed collection access; extend the explicit test collection fixture with canonical source and assert persisted outbox evidence. No runtime bypass for fake databases. |
| [Production Preparation Piece Operations](https://github.com/AMASI-SA/AMASI-SA/actions/runs/38082299495/job/114301539496) | 158 passed, same 1 failure | Same source/fixture correction; all existing assertions retained. |
| [Fulfillment V2](https://github.com/AMASI-SA/AMASI-SA/actions/runs/38082299420/job/114301539335) | 339 passed, 2 failed: same dict failure and absent `mongomock_motor` | Same correction plus pin the already-used test dependency in this workflow. No skip/import suppression. |
| [G47 Focused Integration](https://github.com/AMASI-SA/AMASI-SA/actions/runs/38082299532/job/114301539689) | 137 passed, 4 failed: historical accepted component plans with invalid source dates cannot build a DTO; the new early DTO-only status gate rejected them | Use explicit tenant/order/accepted-plan canonical evidence for this pre-existing historical path. Both canonical status evidence and any available DTO must positively satisfy the strict Ready status; component/unit/transaction checks remain. Unknown, conflicting, under-review and terminal statuses still reject. |
| [Shipping Print Boundary](https://github.com/AMASI-SA/AMASI-SA/actions/runs/38082299540/job/114301576799) | Four operational-print fixtures omit current Salla status | Supply explicit `completed` prerequisite in positive fixtures. Keep strict production gate and add prior-printed store-courier negative cases. |
| [MZ2 Track F native shipping](https://github.com/AMASI-SA/AMASI-SA/actions/runs/38082299522/job/114301539381) | Shares the current-label fixture with missing status | Same focused fixture correction; no shipping runtime relaxation. |

The historical G47 case is an implementation defect, not merely a fixture
change: historical component authority intentionally survives invalid date
presentation data. Positive current status evidence remains mandatory. A
negative fixture that previously wrote `in_progress` into an already
`in_progress` row now writes conflicting `under_review`; additional uniform
noneligible statuses are tested without document side effects.

## Historical reprint policy and related PRs

Pre-change refresh permitted some historical reprints after shipped/delivered.
[PR #1305](https://github.com/AMASI-SA/AMASI-SA/pull/1305), head
`d187dfbb729923f491f6ce3a106dde043e2b890c`, explicitly records the newer owner
rule superseding that behavior: every carrier's print endpoint requires current
Salla `completed`, even after previous print/handoff. This correction preserves
that stricter rule, tests it for external carriers and store courier, and adds
no separate historical-print exception. Local completed/delivering/delivered
stage alone is not live Salla proof. This is a visible policy boundary, not a
silent relabeling of historical tests. The failing fixtures above lacked status;
they did not prove a requirement to allow delivered-provider printing.

#1321 independently implements #1305's positive Ready/print gates while replacing
post-commit synchronous completion with durable delivery. The branches overlap
in preparation and shipping paths; they must not be blindly combined. #1305's
changes were not merged or rewritten.

[PR #1300](https://github.com/AMASI-SA/AMASI-SA/pull/1300), head
`617858e5d327d7c1cd898e73ef6aa12e726e567f`, remains Draft/BLOCKED. Its documented
real-Mongo counterexample is still relevant: status-only canonical writers can
bypass `operational_owner`, so an already-open snapshot may see stale
`in_progress`. This correction does not modify those writers and does not claim
to solve that race. Green CI here cannot supply independent deployment approval
or end-to-end safety proof for that separate known blocker.

## Search performance

`assembly/search` repeated the identical order-stage instruction query once per
eligible piece. It now reads once per request, then applies the same piece/item/
order targeting and actor acknowledgement policy to each piece. Save still
performs a fresh database enforcement read; no cross-request cache was added.

Independent coordinator measurement on integrated code, real disposable local
Mongo, seven interleaved warm samples against pinned original PR head `b99f9fe3`:

| Pieces | Before median ms | After median ms | Instruction reads |
|---:|---:|---:|---:|
| 1 | 6.181 | 5.310 | 1 → 1 |
| 10 | 29.230 | 7.879 | 10 → 1 |
| 50 | 112.894 | 10.248 | 50 → 1 |

The benchmark executes the complete `_assembly_search` function with synthetic
data and real Mongo queries; source/eligibility collaborators are held constant.
It excludes HTTP/auth overhead and is not a Production latency percentile. The
script compares responses for equality. Run with local `MZ2_TEST_MONGO_URI`,
`PYTHONPATH=backend;backend/tests`, and the pinned Git object available:

```
python backend/tests/benchmark_assembly_search_instructions.py
```

New independent-oracle tests cover all three piece counts, per-piece/item/order
instructions, actor-specific acknowledgement, edits between requests, and zero
instruction reads when all pieces are blocked. These tests are added to the
real-Mongo Review Completion Resumable workflow and need no Git history.

## Workflow revision concurrency

A controlled real-Mongo test reproduced an actual defect: harmless `revision`
and `updated_at` changes during provider waits made the old outbox fence reject
valid completion and continue pending. New operations freeze material workflow
evidence (identity, stage, approval mode/operation, items/routes/operational work,
fulfillment decision, completion coverage, batch and experiment shipping mode).
Generic revision is used only for compare-and-set inside the current owner
transaction. Source and component evidence are still checked before provider
effects **and** before publishing a provider readback.

Tests change revision before provider resolution, during status POST and during
label GET; completion confirms and the status POST remains single-attempt.
Changing material item quantity during label GET rejects the stale result even
without a revision increment. Claims, lease expiry and persisted status/AWB
attempt markers remain independent of this fingerprint.

The original PR has never been deployed by this task. A hypothetical older
outbox lacking a material fingerprint retains the conservative exact-revision
fallback; no migration invents proof after a mismatch. Instruction validation
remains at search/save; this change does not introduce a new policy for mandatory
instructions added after an already committed local completion.

## Companion PR #1320: final review only

No image implementation or financial source was rewritten. HEAD remains
`f810ae8f65080afc99d56d751d7b9eb164c29ee0`, TREE
`3c2656d2e138a25d80c169eea27443281e35463c`. All 12 GitHub workflows at that head
completed successfully, including [CodeQL](https://github.com/AMASI-SA/AMASI-SA/actions/runs/38082016859),
[Native Invoice](https://github.com/AMASI-SA/AMASI-SA/actions/runs/38082016925),
[Financial Integrity](https://github.com/AMASI-SA/AMASI-SA/actions/runs/38082016895)
and [Display](https://github.com/AMASI-SA/AMASI-SA/actions/runs/38082016922).

Fresh Production `094d0ef7fa001d3b3794f2600c77795190ddb1a8` differs from the
supplier branch's `d55b1b86` base only in seven TikTok-related paths and its
pre-existing intent file, disjoint from #1320's files. This is source overlap
inspection, not a merge/deploy operation.

Invoice ownership/employee authorization precedes PDF image preparation.
Canonical image selection comes from tenant + invoice + session + piece events.
Cache storage itself is tenant + exact snapshot URL, intentionally reusable
across authorized invoices of that tenant, not one physical cache per invoice.
No standalone cache-reading endpoint exists. Each JPEG is bounded to 256 KiB
(about 349,528 bytes Base64 plus metadata), but there is no TTL, eviction or
total quota. Total storage grows with distinct tenant/URL pairs, and concurrency
six is per request rather than a global bound.

The owner clarified that `MEZAN_SUPPLIER_RECEIVING_SCAN_LOCK_FIX` is only a
proposed future task: no branch or PR exists. There is currently nothing to
integrate or conflict-check against it. `scan_piece` is byte-identical between
the supplier base, current Production and #1320 (SHA256
`2cb70950c18a1076a44e6bd635a888f311d4172fbd4ae3edacc98516c5023f24`). A future
barcode PR must recheck shared-file overlap. Total invoice-save speedup remains
unproven and is not claimed.

## Current verification state

Local correction evidence: 68 preparation/carrier tests; 27 G47 tests with 67
subtests; 12 durable-delivery tests; four batched-search tests. The focused
metadata/material tests were rerun after the final fingerprint refinement.
The broader Shipping Print Boundary corpus passed 459 tests with one existing
memory-only rollback skip; the real-Mongo rollback counterpart executed.

Fresh final-head GitHub Actions remains the acceptance gate. Its final links
and statuses will be recorded in the PR report after the correction is pushed.
No earlier local pass or original-head workflow is called a final pass.
