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
