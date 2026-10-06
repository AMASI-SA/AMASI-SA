# PR4: apply source-proven ADD only

Draft PR1275 is stacked on PR1274 at
`20abc3136b7b793e5dc009f269d0387efc44ec59`. PR1272/1273/1274 and their branches
are not changed. The PR4 diff includes the explicitly authorized narrow change
to ADD hold creation, a restricted atomic profile and route registration.
Review Completion, the excluded issue work and production are untouched.

## Source and operational action

Salla remains the only commercial authority. The backend accepts an existing
immutable PR3 add_product event ID, assignee, reason, idempotency key and fences.
It does not accept product/quantity/options/price/discount/tax input from Mezan.
PR3 still uses normalized offline webhook replay; no live intake or Salla mutation
is wired. New capability `ORDER_SALLA_ADD_APPLICATION_ENABLED` defaults OFF and
requires both existing lifecycle and source-reconciliation flags as well.

The small order-details panel shows source facts read-only. Its only mutation is
«إرسال إلى تمت المراجعة», using backend capability, employee selection, reason
and confirmation. Stale conflict reloads the list; an unknown transport outcome
retries the same payload/key. Changing the displayed order ignores old responses.
Successful local application under another hold explicitly says preparation is
still stopped. No commercial editor or edit/delete/replace action is added.

## State and atomicity

The order must remain `in_progress`. Only the new line gains a reviewed milestone
and new assigned physical units. The parent workflow stage never returns to reviewed.
For the required example: A ready and B received keep their original rows; C gains
reviewed -> assigned -> preparation through the existing employee start operation.
The same ready/receiving/assembly/component execution guards remain authoritative.

One snapshot/majority owner transaction performs:

1. Verify actor authority, source event/current source facts, current revision and
   generation, event-specific barrier identity and native employee eligibility.
2. Refuse cancellation, unresolved non-ADD evidence, source refresh/retry, locked
   workflow, experiments, active/uncertain execution or shipping/invoice blockers.
3. Append only the new normalized operational component-plan line and reserve its
   units using PR2 allocation logic. Preserve all prior unit rows and allocations.
4. Build a new batch/registry, real in-memory PDF and line-specific service plan;
   insert new committed preparation allocations and physical pieces only.
5. Append a reviewed workflow item, before/after application audit and notification
   outbox; persist an immutable applied receipt and idempotency result.
6. Release only this fully completed ADD change's barrier. Any error rolls all
   local effects back, including the release and applied receipt.

The unique unit identity is source order_item_id + unit_index + generation, with
change_id on new records. New generation starts at zero; no old generation changes.
SKU is not an identity. The complete source line, options and custom fields remain
snapshotted, while existing display/service/component projections are reused.
Stock quantities are not deducted on Apply. Actual existing consumption happens
later through the guarded component authority; consumed old units are untouched.

Preparation uses a new deterministic file/batch, not rematerialization of old work.
There is no fabricated PDF readiness: actual bytes are rendered and their size/hash
recorded. Assignment requires active native app access, using the existing helper.
Employee work discovery sees a real ready registry and assigned pieces, with file
title/source label «منتج مضاف إلى الطلب» and customer options.

## Exact hold contract

With PR4 OFF, PR3 default behavior is unchanged. With PR4 ON, an intake containing
only ADD events creates ADD_CHANGE_HOLD identified by tenant + change_id, recording
source generation, creation revision and all new unit identities. Non-ADD intake
retains its original barrier contract. Legacy order-wide ADD holds are rejected;
there is no migration, recovery or automatic reinterpretation of those holds.

Apply verifies the exact change_id/generation/revision/unit set and uses the same
fields in release CAS. If one source change contains multiple new items, its hold
remains active until every sibling ADD has a complete applied receipt. A second
change's ADD hold is never released by this transaction.

Manual, PR2 reconciliation, cancellation, replacement, obsolete and other change
holds are never released. Local materialization may finish with unrelated holds;
the result then reports eligible_for_execution=false and existing hard guards
still prevent work. Actual PR2 reconciliation_required is accepted only with its
matching active v3 authority hold; its lifecycle and consumed proof stay unchanged.
Unresolved DELETE/EDIT/REPLACE source evidence rejects ADD application entirely.
No blanket ignore-holds path or generic resume endpoint exists.

## Event, outbox and application receipt

PR3 OrderChangeEvent is immutable. `salla_add_applied` references its event_id and
change_id and records actor/reason, timestamp, complete before/after local evidence,
result and released hold ID. Effective application status is derived from this
receipt; the original pending event is not rewritten or deleted.

`salla_add_notification_outbox` is inserted in the same transaction, with event,
employee recipient, piece IDs, source message, required action and initial unread/
unacknowledged fields. This PR queues the event and exposes the addition label in
the existing employee work payload/file title. It does not claim push delivery or
add a background notification dispatcher; delivery/read UI remains separate work.

## Isolation and rollback

The new `salla_add` transaction capability cannot write canonical orders, warehouse
stock, product catalog, invoices, journals or financial controls. Pieces/component
units/preparation records/history are insert-only in this profile. Workflow updates
can only append items and increment revision. Hold updates only permit release of
an exact ADD_CHANGE_HOLD with identity/fence predicates. No provider I/O occurs.

Rollback means disabling PR4 for new applications, not deleting applied products.
Committed same-key retries retain the original result. Preserve applied units,
reservations, assignment, audit, outbox and all remaining holds; never reconstruct
old pieces, refund/return consumed stock or reopen a released change to replay it.
Existing PR1 guards stay enabled for enrolled owners after feature flags turn off.

## Verification

Isolated MongoDB8.0.12 tests exercise real reservations, consumption, PDF-backed
assignment, employee visibility/start, mixed stages, variants/options and identity,
duplicate intake/apply, independent clients, ADD/DELETE race, lost response/retry,
stale fences, manual/actual PR2/second ADD holds, sibling ADDs, permission denial,
source cancellation, execution/financial blockers and immutable-source preservation.
Mongo validator code121 forces audit/outbox rollback after materialization; insufficient
components and invalid assignment leave no partial units/records or hold release.
ASGI tests reject commercial payload fields and enforce authority/default-off behavior.
Web tests cover confirmation, capability visibility, stale conflicts, retry and
order-switch safety. Exact-head CI also reruns the unchanged PR3 default contract.

Draft only. Rollout OFF. No Salla mutation or financial write. No merge/deploy/recovery.
Production writes = 0.
