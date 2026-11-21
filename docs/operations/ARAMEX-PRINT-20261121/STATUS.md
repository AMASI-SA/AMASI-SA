## Release checkpoint — 2026-11-21

The owner authorized normal merge and publication. Release branch `codex/aramex-current-label-rootfix-20261121`
is frozen at intent-only B `bc267e64a2305a8b47528960e34684d9ce95949a`, source A `a1d2ca761f0956a4214aa773e22e829bf74b8c09`,
reviewed base J `5019e957cd1bb578f7b7375099547a470e51cb05`.
Runtime: `rg5-eb507d72b5149c6df4de7c5769b467ae4f7daf70cd179d82ef51bd5ec354c097`.
PR: https://github.com/AMASI-SA/AMASI-SA/pull/2040.

Exact-A checks:25 successes,6 conditional skips,0 failures. Official Linux
source readiness run44912011737 passed. Candidate artifact14710023632 matches
all1343 Frontend,1282 Backend and322 release-control Git records, and the
deterministic identity recomputes exactly. Full local Windows validation refused
NTFS file modes; no waiver was applied. Exact-B Host20/24 rehearsals and all
applicable CI must pass before merge/publication.

Continuation updates use this evidence branch `codex/aramex-current-label-evidence-20261121`;
DO NOT DEPLOY it. The source/intent release pair must remain immutable. The
exact evidence commit is recorded in the PR and canonical ledger task block.
Issue1006 task block: https://github.com/AMASI-SA/AMASI-SA/issues/1006#issuecomment-6238987123.
Its append preserves all preceding owner comment content. An unsupported
conditional request header was removed after rejected requests; no foreign
content was deleted or replaced.

The shared original guard reported active:false. Shared tracked source was
clean on another task branch; all its untracked files were preserved. Only a
separate temporary read-only Python environment was created outside `/app`.
The preview database has no matching target record, so it is not evidence
about the original production shipment. Salla's authenticated print page needs
its own login; no authentication barrier or substitute label was used.
No merge, own lease, shared source mutation or deployment yet.

Next: finish exact-B checks and isolated Linux adapter rehearsal; refetch base
and shared guard status, merge normally, update clean `/app` preserving foreign
files, run the configured adapter, prepare/prepublish, publish once, verify all
three original probes and closed lease, then retest the original order by ADB.
Production changed by this task:no. Original shipment acceptance:unverified.

---

# Current carrier shipment read recovery

Task branch: `codex/aramex-current-label-rootfix-20261121`.
Verified release source base: `5019e957cd1bb578f7b7375099547a470e51cb05`.
Initial local verification base: `c4a83ac18b24f0367b3fbe8052a026c2dbdbac98`.
The owner subsequently authorized commit, upload, merge and publication.
The task branch fast-forwarded to the new reviewed production base without
changing the candidate source patch. State: source A checkpoint preparation;
local implementation verified, live failure mechanism and original shipment
acceptance remain unverified. No release intent, lease, shared-host change or
deployment has been performed. Exact source/intent SHAs and CI are recorded
in the task PR as they become available.
Production changed by this task: **no**.

## Evidence and bounded change

The native reproduction stopped before Android printing: the application
reported no outbound shipment. An independently connected Salla reader returned
an existing international-carrier shipment with tracking but a null label.
The owner confirmed printing through Salla's authenticated print page. These
observations do not identify the application's exact provider failures or its
captured canonical shipment metadata. They do not prove that this candidate
alone fixes the reported shipment.

`backend/order_engine/amasi_shipping_compat.py` now uses the already-known
current shipment ID to read details when lists cannot identify it. This hint
cannot select a superseded shipment, substitute a new shipment, or establish
ownership by itself. Fresh details must identify the exact shipment and order;
present reference conflicts, cancellation and return shipments block the new
path. Existing current-shipment, carrier, freshness and request fences remain.
No shipment creation, completion-status write, alternate label, invoice PDF,
dashboard-page fallback, mobile source or credential change is introduced.

Failed reads are distinguished from an authoritative empty list. A denied
shipping read returns `shipping_scope_required`; unavailable reads return
`salla_shipping_unavailable`, rather than a successful "no shipment" response.
An independently obtained valid label still succeeds.

## Fresh local verification

The initial regression failed before the production edit at the public carrier
reader's `ready` result. The new regression module now passes 24 cases,
including both native HTTP routes, read-only provider calls, canonical ID
proof, missing/foreign/conflicting identity, cancellation/returns, supersession,
and retaining tracking without printing a commercial invoice.

Twelve affected modules run in isolated processes passed **390 tests and 64
subtests**. One in-memory transaction rollback case is deliberately skipped;
its real-Mongo counterpart passed on an isolated loopback replica set with
unique test databases. Use `PYTHONPATH=backend;backend/tests` on Windows and
`--asyncio-mode=auto`. Do not combine these modules in one process: an existing
unittest transaction-adapter import leaks into later real-Mongo fixtures.
The initial runtime also lacked the pinned email validator; the affected
handover module passed all five cases in an existing complete test runtime.
No production source was changed to accommodate those test-environment issues.
The dedicated Shipping Print Boundary workflow now runs the new regression
module in its own process on the exact PR head.
AST parsing and `git diff --check` pass.

## Continuation

The local patch is retained outside ephemeral directories with its SHA256 and
per-module verification summaries. GitHub writes, normal merge and publication
are authorized. Preserve source A before the governed build, then freeze an
intent-only B and require its exact-head checks. Issue #1006 is at its comment
limit; preserve the task-specific PR handoff if the canonical ledger cannot
accept the entry, and report that limitation.

Inspect a fresh production source and obtain the failing request's provider
read results and canonical shipment identity through an authorized connection.
Verify how the provider exposes the official label when the list returns null.
Do not present the authenticated dashboard print URL as a downloadable PDF.
Complete the actual shipment/device acceptance before claiming resolution.
Any later publication requires separate authorization and the unchanged v5
source/intent, rehearsal, lease, prepublish and completion-proof gates.
