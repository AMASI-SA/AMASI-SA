# Operational refresh completion — 2026-11-26

## Authority and current verified state

The merchant authorized resolving the underlying accounting refresh problem, merging, publishing directly to Production, and continuing testing until resolved. Preserve historical movements, original dates, bank evidence, financial formulas and idempotency. Keep Mezan light: no extra recurring jobs, query fan-out, blind refresh retries, invented transactions, paid Emergent agent chat or Preview. The unchanged original Release Guard v5 remains mandatory.

Repository: AMASI-SA/AMASI-SA. Follow-up branch: `codex/operational-refresh-completion-20261126`.
Verified current production/base D: `317dc183485566ba9e05c5afca5d4ea1b84e291e`, tree `a8aedd1be45cf7dcb26b31078a6c5e580709e30f`.
PR2133 source A: `50cbe07da4e29fa41c7a184addffdeb8d2169f1a`; intent B: `4067edbfbfd0739382a1c390784bc238a7f00b7f`.
Runtime release: `rg5-5064eab0115d74d39ba29eeed04f13511f5a27156260c0cc5a3a4bc2045e1fb5`.

PR2133 was merged, passed exact A/B/D CI, clean-clone rehearsals and the original-host adapter/package checks, and was published exactly once as provider version `bad38ff` at 05:17 Asia/Riyadh. The original verifier ran once, passed all three exact identity/frontend probes, and closed its lease at 02:34 UTC. Raw proof SHA256: `108220774d22e0074127aa10d63b1dbd5e35afbaaf87998ac14c6bed37da6c4b`.

The task-stopped local Backend was restored at 02:41:22.858577 UTC. A separately reviewed continuation retained the original failed preservation receipt: all 432 untracked paths remain, 431 records are identical, and one platform bookkeeping file retained its original prefix with 378 appended bytes. One credential-file inode replacement is recorded using metadata only. Its contents were not read/hashed/copied/modified, and no actor attribution is established. Original baseline and guard were not changed.

Durable prior-release evidence: `codex/operational-refresh-source-publication-evidence-20261126` at `7ddf136d875cf325189b47f2887de9892405edd5`, path `docs/operations/OPERATIONAL-REFRESH-SOURCE-ROOT-FIX-20261126/verification/production-proof.json`. That documentation-only branch must never be merged or deployed. Do not repeat PR2133's publication or verifier.

## Live acceptance failure

The sole manual refresh on PR2133 started at 02:41:38.999 UTC and returned `operational_refresh_timeout` at 02:43:06.228 UTC. The previous saved snapshot remains in use; no new successful financial calculation or recovery report was accepted.

| Stage | Observed milliseconds | Outcome |
| --- | ---: | --- |
| Native evidence | 11142.523 | complete |
| Raw aggregate read | 119.896 | complete |
| Aggregate unpack | 16287.066 | complete |
| Redundant copy preparation | 0.012 | complete |
| Primary orders cursor | 18058.509 | complete |
| Primary source reads | 18124.681 | complete |
| Bundles | 14857.234 | complete |
| Option cost catalog | 2616.166 | complete |
| Order indexes | 1512.510 | complete |
| Source order loop | 17848.574 | cancelled |
| Total source collection | 57312.655 | cancelled |
| Aggregate | 73830.229 | cancelled |
| Profit snapshot | 25845.285 | complete with product authority cancelled |

These stages overlap and must not be summed indiscriminately. All 19,151 primary orders completed under the explicit 20-second allowance; first row was 11.132 ms, application queue 0.033 ms, cursor close 51.534 ms. The unchanged 85-second outer budget expired before source conversion/reconciliation/save completed. Thus source read admission improved, but the live root-fix goal remains open.

## Initial diagnosis and preserved decisions

Read-only independent work is tracing the remaining order loop and bundle normalization, archive/native snapshot materialization, and the request's dependency graph. Native proof acquisition intentionally precedes primary order reads because adapters write version-bound source evidence. Profit already overlaps collection after primary reads. Do not substitute profit and operational cohorts or authority results without exact projection/freshness/correction parity.

A concrete candidate for investigation is duplicate full validated financial/issue packet decoding: native gap preparation decodes packets, then aggregate unpack decodes the same contents again. Any request-local reuse must bind exact packet/owner/metadata, retain validation and authorization, own cancellation, and avoid mutable aliasing or retained stale data. No implementation decision or production speed claim is established yet.

At the initial diagnosis checkpoint, no follow-up source change had been made.
The verified implementations and current next action are recorded below. No new
lease, publication or manual retry has occurred in this follow-up.

Issue #1006 is at its comment cap; update this task document, its PR, and the evidence branch. Keep private financial reports outside GitHub.

## Verified audit-boundary checkpoint and measured decisions

The first scoped implementation removes only the immediately repeated admission
checkpoint at each internal eight-page audit boundary. Both serial and four-page
prefetch readers retain fresh first/every-eight/final authorization and lease
renewal, all per-page validation and cooperative cancellation, exact queries,
complete history and joined cleanup. The preceding completion checkpoint is
also the next group's admission; no read, await or processing intervenes.

The new 32-case regression first produced 16 expected duplicate-boundary
failures and 16 passes on the old source. After the two predicate changes,
119 focused cases passed in 4.845 seconds with 11 real-Mongo/socket cases
explicitly deselected. Independent source/test/evidence review found no blocker.
The 64-page synthetic count falls from 16 to 9 audit checkpoints (whole packed
unpack 22 to 15), with exactly the same 64 page reads, full-state equality,
unchanged stored input, zero nonlease writes and no orphan tasks. These are
operation counts, not measured production latency. Evidence is in
`evidence/audit_boundary_findings.md` and its linked scripts/JSON/JUnit.

The proposed native-to-aggregate decoded-packet handoff is **rejected/deferred**.
A dense 49.02 MiB financial packet plus 0.96 MiB issues, with 19,151 synthetic
orders, produced an optimistic A/B/B/A median saving of 935.645 ms for avoiding
the second decode. Native-stage retained allocations increased by 37.69 MiB;
traced peak remained approximately 319.36 MiB. Exact native/full-state BSON
parity and source/input hashes passed. This saving does not justify additional
retention and alias ownership complexity. No request-local snapshot cache is
implemented. See `evidence/native_decode_reuse_measure_result.json`.

Separate full-source synthetic profiling found repeated per-line option-catalog
construction and full order copying during pure payment normalization. An
experimental combined run fell from 29.992 to 24.731 seconds with identical
full outputs, input/source hashes and query sequence. It also included a
rejected blanket small-item copier substitution whose isolated copy/GC cost
increased, so that combined percentage is not attributed to the selected fixes
or claimed as a production gain. A scoped per-attempt option authority and
read-only payment view are under implementation/review; the general public
payment snapshot must retain its independent-copy behavior.

A separate copier-lifetime prototype identified an avoidable private recursive
closure cycle retaining the original graph through its memo until cyclic GC.
Breaking only that private closure reference on every exit preserved values,
aliases/cycles, BSON/custom-hook behavior and escaped-memo contents. It released
the original immediately under GC-disabled weakref probes. A narrow lifetime
fix is under implementation; no blanket bundle-copy replacement is authorized
by these measurements.

This checkpoint is **not final source A and not ready to merge or publish**.
Production remains the verified PR2133 runtime, whose sole post-publication
manual refresh failed. Continue scoped implementation, independent review and
complete affected verification before freezing a new A/B release. Do not retry
the existing live refresh blindly or repeat the prior release.

## Completed source implementation and candidate A

PR: https://github.com/AMASI-SA/AMASI-SA/pull/2134.
Previous remote checkpoint: `7328d8e745eb0eff1109024995f1968a5a6c5964`.
The next source checkpoint containing this section is the candidate source A;
its exact commit/tree must be taken from Git metadata and retained in the
separate release evidence. It is not permission to merge before its gates pass.

Four narrowly scoped changes are complete:

1. The adjacent audit checkpoint reduction described above.
2. A successful option-catalog authority reused only during one source attempt.
   All per-line prices, quantities, selected bindings, identities, currency and
   failure paths remain outside the cache. The exact native catalog/binding
   graph is observed and checked again before observations leave collection;
   changes reject the whole attempt. The scope is cleared on every exit.
3. An internal read-only payment-normalization view borrows only four unused
   nonfinancial root branches. The public snapshot retains complete independent
   copying; financial aliases/cycles and all admission rules are preserved.
   Unsupported objects retain the old path.
4. The report copier clears only its private recursive closure reference in a
   `finally` block. This releases the keepalive memo promptly after success,
   errors and cancellation while preserving any memo deliberately retained by a
   custom deepcopy hook. No bundle-copy callsite changes were made.

Option regression testing reproduced and fixed a real effectiveness issue:
ordinary unprojected Mongo binding rows contain ObjectId/datetime metadata.
Exact native metadata now participates correctly; ObjectId's replaceable byte
slot is observed, and custom time zones/subclasses retain fallback. The author
ran 176 cases (63 new and 113 existing), all passing in 3.75 seconds. Independent
review compared original catalog/admission/token blocks by AST and reviewed all
new cases without rerunning them; no blocker remained.

Payment tests: 45 new cases passed in 1.08 seconds. The wider local run passed
430 cases plus 184 subtests but could not execute four real-Mongo scenarios
because the local Mongo listener was absent. The unchanged JUnit is retained as
`evidence/payment_contracts_local_mongo_unavailable.xml`, not presented as green.
Both strict Operational Balance CI environments provision real replica sets and
must execute those cases with no skips, failures or errors. The new payment test
was renamed to `test_operational_balance_payment_read_view.py` with identical
content so those existing CI discovery lists actually execute it.

Copier lifetime controls first produced 5 intended failures and 2 passes on the
old source; the complete targeted set then passed 74 cases in 3.810 seconds with
no skips/errors/failures. The root reviewer read the final tail-only source diff
and all seven weakref/hook cases. Audit-boundary evidence remains 119 passes,
with its 11 explicitly deselected local integration cases still required in CI.

### Actual-runtime paired source measurement

The final synthetic pair uses the actual option and payment implementations,
native BSON-shaped binding metadata, 19,151 orders, 76,604 items and 128 products.
The same frozen runtime is used for both modes; the baseline explicitly disables
the two opt-ins. Complete output equality, serialized output, inputs, query
sequence and runtime source hashes all match. No database query was added.

| Measured work | Opt-ins disabled | Actual candidate |
| --- | ---: | ---: |
| Source-order stage | 14,216.590 ms | 10,576.560 ms |
| Option token validation | 3,202.092 ms | 1,133.941 ms |
| Native catalog constructions | 19,152 | 128 |
| Payment payload copying | 1,938.521 ms | 649.026 ms |
| Whole source collection | 30,566.549 ms | 25,070.153 ms |

The unchanged bundle stage also varied by about 1.816 seconds, so the entire
5.496-second collector difference is not attributed to the opt-ins. This is one
bounded synthetic pair, not a production latency claim. The authority build
took 19.770 ms and final validation 8.976 ms. Its measured auxiliary object
representation is 1,424,776 bytes before caching and 2,310,654 bytes including
128 catalog results, excluding borrowed source graphs; this is not RSS. The
sequential process high-water figures are not comparable isolated memory peaks.
The copier lifetime fix is identical in both modes. Evidence is retained in
`evidence/measure_option_payment_candidate.py` and its result/findings.

### Remaining required gates and live acceptance

No follow-up Production change has occurred yet. Freeze exact candidate A,
require its complete applicable CI and reviewed intent artifact, create only
intent commit B, require B CI and clean-clone rehearsals, merge ordinarily to a
tree-equivalent D, and require the applicable Production push gates. Preserve
the unchanged original Release Guard v5 and fresh original-host preservation
baseline. Execute one authorized direct publication only after those checks.

After official three-probe verification, require an actually advanced saved
calculation and a fresh recovery report. Compare retained historical movements,
original dates, bank receipt approval state, delivery/courier obligations,
advertising and collections against the private baseline. Existing warnings
about source completeness are not silently converted into verified sources.
If the live refresh still fails, preserve its trace and keep this task open.

## Subsequent natural attempt and final source preparation

The first candidate A was `7b4da05fba49d7b2e923ae3ac304af7e15dbcaa6`, tree
`f764cc59cf9eb2ad378656b5cbf0ded462481b2f`. All 12 applicable workflows passed.
The Stock backend executed 6,746 cases plus 97 subtests; Web backend executed
8,073 cases plus 151 subtests, and demand-read executed 106 cases. Both backend
XMLs contain all 147 newly added cases and all four previously unavailable
local Mongo cases, without failures, errors or skips. Job logs prove Mongo
8.0.12 replica sets and exact-A checkout. These results belong to that exact
candidate; any additional implementation requires fresh A gates and intent.
No intent-only B, merge, lease or publication followed that first A.

A read-only schedule-status observation at 03:37:02.190 UTC found that the
existing PR2133 Production scheduler's 06:34 Asia/Riyadh attempt also failed.
It read 19,152 orders and completed source collection (56,615.505 ms), but
reconciliation was cancelled at 6,585.907 ms when the same outer deadline
expired. Bundle normalization took 11,321.974 ms. The saved calculation remains
at `2026-11-25T22:51:55.742037+00:00` (01:51 Asia/Riyadh). This observation used
only the existing status GET; no extra refresh or schedule change was made.

A single, reviewed offline baseline/private-view pair on the first candidate
measured a conservative native-item view at the existing bundle normalization
copy point. Both modes used separate fresh processes and the unchanged actual
option/payment implementations. The 19,151-order/76,604-item synthetic cohort
contained no selected physical/financial bundles or supplier history, so
populated financial and lifecycle tests are required before adoption.

| Work | Original item copies | Private native-item view |
| --- | ---: | ---: |
| Bundle normalization | 12,352.540 ms | 8,041.561 ms |
| Whole source collector | 27,274.038 ms | 24,006.110 ms |
| Process CPU | 27,712.823 ms | 24,587.306 ms |
| Full item deep copies | 76,604 | 0 |
| Process high-water RSS | 942,020 KiB | 719,188 KiB |

Full financial output and serialized-output hashes, query sequence, input
digests and all runtime hashes matched. Both children were joined. The view
avoided 1,551,228 nested container clones in this fixture. Inclusive stage
timings overlap, unchanged stages varied, and process RSS includes imports and
fixture setup. These measurements do not establish live latency or memory use.
No timing sweep or additional production refresh was performed.

The fifth scoped improvement is authorized for implementation: only the
collector's freshly materialized, private cohort may opt into a read-only item
view during bundle authority preparation. Public/default normalization retains
its independent-copy behavior. Exact JSON trees may borrow untouched nested
branches; repeated containers, cycles, custom/subclass values and unsupported
shapes retain the original copy at its original point. No query, cohort,
financial formula, supplier proof, receipt/fingerprint rule or deadline changes.
Meaningful populated financial, fallback, mutation and cancellation tests and
independent source review remain required before freezing the next A.

Full first-A CI and diagnostic evidence, including the exact raw bundle result
SHA256 `8f9321f986e49580ac9e45756a940b8c7ada426c3a207986f7478af016c492a6`,
is durable on the documentation-only evidence branch
`codex/operational-refresh-completion-evidence-20261126` at
`1d2ca5443885635f95f2b8d2efd3ae285db6c16a`. Never merge or deploy that branch.
The independently reviewed next-release operator drafts are disabled and
separately checkpointed there; final A/B/D/runtime values remain unfilled.

## Final private bundle-view implementation and review

The fifth change is complete and independently reviewed with no blocker. The
public normalizer and default loader keep full item copies; only
`collect_sources` enables the private view. AST comparison confirms the shared
normalization body is unchanged apart from its copier seam, and all remaining
authority/receipt/projection logic is identical. Normalization stays synchronous
at its existing location; no new thread or checkpoint is claimed.

The input boundary is the collector's fresh standard Motor/BSON cohort. The
server and isolated demand runner use no custom document class, codec or type
registry. The private opt-in is not a general API for arbitrary injected Python
objects, hooks mutating unrelated input rows, or module-global monkeypatching.
Unsupported item shapes still retain their original per-item copy behavior.
No native item container escapes into returned financial rows or a cache.

The meaningful red control ran one newly added copy-count regression against
the preceding runtime: financial output and input preservation passed, but the
expected eight unnecessary complete item copies failed the new requirement.
The final focused suite passed all 34 cases in 2.99 seconds without failures,
errors or skips. Valid populated physical/financial cases explicitly observe
borrowing at the real authority boundary and zero complete item copies; an
aliased case proves one independent fallback copy with preserved internal alias.
Complete financial results, queries and source/database immutability match the
default path. Output mutation is isolated. The collector/reducer/replay case
retains exact supplier exposures and zero fabricated movements/facts. Both
authority-await cancellation paths join work and leave no remaining tasks.

Independent source/test/JUnit review and `git diff --check` passed. The new
`test_operational_balance_bundle_item_view.py` is selected by both existing
strict CI globs. Local focused evidence is recorded in the accompanying
`evidence/bundle_item_view_*` files. The next source checkpoint is the final
candidate A and needs new complete CI, a new intent-only B and all unchanged
release/live acceptance gates. Do not reuse the first candidate's intent.
Production still runs PR2133; the live accounting-refresh goal remains open.
