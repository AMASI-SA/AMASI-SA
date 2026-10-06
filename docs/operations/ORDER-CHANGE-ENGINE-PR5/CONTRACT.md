# PR5: source EDIT_OPTIONS and transactional old-generation guards

Base PR #1278: e983dabe75ebf568129c2408f24a8ce10f7276e6.
Only this branch owns PR5 changes. No previous PR branch is changed.
PR2 is imported unchanged: _allocate and _ambiguous_consumption only. Its public
reconcile_components contract is not bypassed or invoked with changed commercial
options. PR5 owns its separate source-authorized transaction.

## Hard guard and regression

Before fix, real Mongo8.0.12 reproduced: execution claim acquired; receive's last
hold check passes; PR3 commits EDIT_OPTIONS event and active hold; the old receive
writes ready_for_assembly. The permanent regression fails against the base module.

Receive and preparation start now capture full physical identity before entering
an owner-serialized transaction and revalidate it INSIDE that transaction with
current/active/cancelled/replaced/obsolete checks, revision and generation, holds,
and unresolved EDIT events. Transition writes and their audit are in that same
snapshot/majority transaction. Assembly-ready retains its existing transaction and
now also honors the caller's expected fences there. No post-commit recheck is used.

PR3 source commits and local transitions share the owner serialization row. If EDIT
commits first, the transition rejects. If transition owns the transaction first,
EDIT cannot commit in its middle; it commits afterward and blocks later execution.
An already-started external dispatch cannot be retracted by a local transaction;
existing active/uncertain claims remain conservative. No external dispatch or Salla
mutation is added to PR5.

## Option classification

- representation_only: recursively equal source options/custom fields/variant;
  numeric scalars and identical textual numeric representation are equivalent.
  No whitespace trimming, case folding, list reordering or arbitrary customer text
  normalization. No re-preparation, allocation rewrite or generation increment.
- preparation_file: changed customer selections, unchanged tracked recipe signature.
- components: tracked resource/binding/quantity signature changes.
- reconciliation_required: consumed, ambiguous/partial or prebuilt component evidence
  on a substantive change. Partial/ambiguous evidence also blocks representation-only changes. No restock, old unit obsolescence or new path is created.
- exception_required/rejection: ambiguous units/source identity, stale source,
  missing acceptance, other holds, conflicting evidence or unsafe stage.

Unknown changes are never presumed harmless. Any independent active hold is a
conservative application blocker, including an unrelated item hold. Existing
Manual/PR2/general order/cancel/replace barriers are never released by PR5.

## Generation, component and assignment transaction

Only the affected item indices participate. Supplied unit refs bind order_item_id,
unit_index, generation, revision and prior change_id. Stored event evidence and
current workflow/source fences are also checked. Same SKU is not an identity.

Substantive safe edits create generation N+1 with exact new options, component
reservations and an eligible employee assignment. Existing generation N physical
rows become obsolete/current=false/active=false and retain their history. Every
old/current artifact snapshot is retained in the immutable applied audit.

The existing component and preparation-allocation schemas have one active slot per
item/index. PR5 selectively replaces those slots inside its transaction, preserving
full previous rows in immutable audit. Other item slots and all inventory quantities
remain unchanged. Reserved pressure is released only inside the same transaction
before recalculating allocations through PR2. No stock is returned for consumed or
ambiguous evidence. New physical rows use generation-specific UUID/QR identities;
generation zero retains its exact historical identity. PDF QR and materialized
piece use the same generation mapping.

Audit, notification outbox, idempotent receipt, obsolete transition, new generation,
components and assignment commit together. Exact source item hold release is last.
Failure at any step rolls back all business effects and preserves active hold.
No general order hold, PR2 hold, or another change's hold can be released.

The immutable PR3 event continues to represent original source evidence. A separate
salla_edit_applied receipt resolves it; a notification acknowledgement does not.
Reconciliation decisions use salla_edit_decision and preserve the source barrier.

## Web and events

No commercial editor or Android UI. The Web view shows original/new options,
image/variant, affected employees, stage, classification, action and notification
status. The action submits event ID, assignee, reason and fences only, not options.
Uncertain retry preserves the same payload/key; changed key for an applied event
returns the original receipt, not a duplicate generation.

PR3 detection outbox already targets affected employees. PR5 exposes it through
GET /order-change-edit-v1/my-notifications using authenticated tenant and recipient
identity, without reviewer permissions or client-selected recipient IDs. It shows
old/new options and stop-old-generation action before Apply. This is a read-only
client capability, not proof of external push delivery; Android UI and its route
allowlist remain unchanged. PR5 outcome outbox links
the same OrderChangeEvent; new-generation preparation, reconciliation and no-op
continuation have distinct required_action values. Notifications are not guards.
Any financial difference remains pending_contract.

## Validation and scope

Real loopback MongoDB8.0.12 replica set, generated isolated test databases, no
application database fallback. Fixtures are on D after Mongo correctly rejected
index creation on the low-free-space C volume. That environment failure is not a
product pass; final verification must rerun the full suite.

Protected PR2, PR3 and PR3.1 services and PR4 ADD implementation remain unchanged.
reviewed_preparation_batches.py changes only QR generation pass-through, not Review
Completion logic. No delete/replace/add application, invoice/journal/refund writer,
Salla mutation, migration, merge, deploy or Recovery. New EDIT flag defaults OFF.
Production writes = 0.

The broad regression initially found an expected-error contract change: the new
transactional assembly source guard returns fulfillment_source_reconciliation_required
before the component consumer. The G47 test now asserts this exact earlier rejection;
packing still must return component_execution_blocked, with zero stock consumption.
