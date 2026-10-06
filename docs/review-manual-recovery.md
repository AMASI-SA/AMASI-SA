# Explicit current-state review after an unverifiable historical approval

This Draft is stacked on #1266, not a production recovery execution. Only the
nine incident order numbers in `RECOVERY_ORDERS` can enter this flow. Historical
operations, approval hashes, confirmation timestamps and `requires_review`
states remain unchanged. No status POST is sent to Salla.

## UI and API

The reviewed-products page lists eligible missing orders through
`GET /order-reviews-v1/manual-recovery/candidates`. Eligibility requires an
allowlisted, owner-scoped old operation blocked by
`review_completion_source_changed`, original provider confirmation, incomplete
original business evidence, current Salla reviewed status and no completed
local review workflow. A known different last conflict is rejected.

`POST /{number}/manual-recovery/prepare` forces the existing central Salla
details/items refresh, with automatic fulfillment disabled and all item pages
read. GET/catalog enrichment happens outside transactions. The restricted owner
transaction checks current acceptance/config, eligibility, component acceptance,
source and review revisions, then saves a fifteen-minute approval preview.
There is no new completion operation, reviewed workflow or event at this step.
An existing frozen component plan must be compatible with the original available
recipe acceptance evidence; missing or changed recipe evidence blocks with
`component_plan_reapproval_required`. This feature does not silently replan.

The UI displays current products, quantities, SKU, options, customer, shipping,
payment, component and eligibility facts. Only a separate click on
«تأكيد الاستعادة للمراجعة» submits the actor-bound session and approval hash to
`POST /{number}/manual-recovery/confirm`. False/missing confirmation, expired new
preview, ownership mismatch or changed facts cannot approve.

## Durable execution and audit

Confirmation uses the existing Review Completion engine. The new approval has a
distinct deterministic operation identity, an immutable versioned business
snapshot copied from the displayed preview and a GET-only provider snapshot.
Every validation point rechecks the preview basis, config fence, source/review
revision, component source/generation and cancellation. Provider readback uses
all current items and the same receiving-bank enrichment as refresh. Unknown
business changes fail closed. There is no old-approval substitution.

The operation and completion event carry `manual_review_recovery` metadata:
reason, old/new operation IDs, any prior failed manual attempt, session ID,
confirming actor/time, snapshot hash, schema/normalization versions and source.
Workflow, completion event and final result commit in the existing transaction.
Repeated clicks and workers share its lease. Resume remains GET-only and retains
the new operation's first provider confirmation timestamp.

A conflict requires a fresh preview and explicit approval. A successor may be
created only when the previous manual attempt is terminal `requires_review`;
all prior documents remain immutable. Concurrent previews cannot create two
active approvals for the same predecessor. Network/5xx uncertainty retains the
UI's original session so retry cannot silently start another approval.

## Evidence and restrictions

Tests use synthetic incident identifiers, ASGI, mocked GET-only provider
transport, and a real local Mongo replica set. They are not a live Salla replay
or execution on the nine production orders. No production API, deployment,
backfill, migration or financial writer is part of this implementation.
