# Automatic continuation of new review approvals

Stacked on #1257 (`a1a0f78c787c280d1aef415d50434465fd21c5e1`). #1256 and
#1257 branches remain unchanged. This is not historical recovery or backfill.

Only the completion engine creating a **new** durable operation sets
`auto_resume_version=1`. The worker never sets this marker, scans orders,
infers approval from Salla, or creates a workflow/event itself. Older operations
without the marker and orders without operations are outside its query.

Each process starts the worker in `_local_startup` after required initialization;
shutdown cancels it before closing the DB. Every replica can run it safely.
The 15-second loop selects at most 10 due prepared/syncing/provider_confirmed
operations. A Mongo atomic claim expires after 180 seconds; the existing
completion engine additionally acquires its own fenced 120-second lease shared
with manual retry. Each worker attempt is bounded to 90 seconds. Provider
deadlines/readback and all source, acceptance, revision, cancellation and
component guards remain in that engine.

The worker loads current facts but supplies the stored operation ID, revision,
items and approved acceptance snapshot. An explicit resume cannot create an
operation if it disappeared. Original fingerprints are compared, never replaced.
The original actor must still exist, be enabled, authorized and bound to the
same merchant. A config conflict cannot mint a new approval. Provider-confirmed
evidence is retained when a later verification read fails.

Eight background attempts maximum; delays are 30, 60, 120, 240, 480, 960,
1800 seconds (cap 1800). Contention with a valid completion lease defers without
consuming the background budget. A process killed on its last attempt is marked
terminal on the next scan after leases expire; it is not sent to Salla again.
HTTP evidence/eligibility conflicts block immediately; transient and unclassified
errors exhaust the finite budget. Cancellation of the process leaves persisted
claims to expire. No provider error text or payload is logged/persisted.

Terminal failures become `state=requires_review`, retaining the original
approval and prior phase, with `resume_block_reason`. The authorized read-only
`GET /order-reviews-v1/{order_number}/completion-operation` endpoint exposes a
small status projection. This adds no
Mobile/UI behavior and does not mislabel a blocked approval as reviewed.
Explicit reapproval remains the existing separate approval identity protocol.

Successful resumption commits workflow, completion event and result through the
same engine transaction. `/reviewed` and the current catalog still require
`workflow.stage=reviewed`; instant ready-to-ship routing remains separate.

## Evidence

`test_review_completion_auto_resume.py` uses disposable real Mongo replica-set
databases and actual ASGI review/list/catalog endpoints. Mongo validators inject
workflow/event persistence failures. Tests cover retries, two workers, manual
competition, lost provider response/readback, restart in separate processes,
product/config/revision/cancellation/authorization conflicts, bounded failures,
and old-operation exclusion. Salla transport is synthetic, never live.

The focused Review Completion CI includes these tests and all prior regressions
at the exact PR HEAD. No other workflow, stage, shipping, Mobile or financial
implementation is modified. No deployment or production mutation is authorized.
