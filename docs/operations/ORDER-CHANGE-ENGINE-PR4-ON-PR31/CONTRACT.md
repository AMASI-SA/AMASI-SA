# PR4: atomic ADD application on PR3.1

Base: PR #1277 at `5b346ee42785c20c2a878c27dd5decafb4385f13`.
This is a new independent PR4. Existing PR #1275 and PR #1272/#1273/#1274/#1277
branches remain unchanged. PR3.1 hold producer and PR1/PR2 services are unchanged.

## Source and UI

The trusted offline webhook replay adapter is the source of immutable ADD evidence.
No live webhook rollout or Salla mutation is added. The operational API accepts only
an existing event ID, employee ID, reason, idempotency key and current revision/
source-generation fences. Commercial fields cannot be supplied by the client.
The new capability `ORDER_SALLA_ADD_APPLICATION_ENABLED` defaults OFF and also
requires the lifecycle and source-reconciliation flags.

The order-details panel shows pending source products, image, quantity, variant,
options and custom fields. The button appears only for an explicit add_product,
pending_application, backend-allowed entry. The operator selects an eligible
preparation employee. Stale conflicts reload eligibility; uncertain transport retries
reuse the same request and idempotency key.

## State and identity

Parent order remains in_progress. Existing ready/received pieces keep their status,
generation, assignment, components and allocations. The new workflow item records
reviewed; its own new preparation batch, registry and pieces are assigned to the
selected employee. Existing preparation/start/receipt/assembly paths operate on
those new pieces, under existing execution guards.

New physical/component units and preparation allocations retain change_id,
order_item_id (component schema: order_line_id), unit_index, generation=0 and the
source event revision. application_revision separately records the commit revision.
Full original options/custom fields remain frozen; inventory selection hashes use
the stock contract, with dictionary custom fields normalized only for that contract.

## Hold and release boundary

PR3.1's per-unit item-scoped ADD_CHANGE_HOLD v5 is required. Exact identity is:
change_id + order_item_id + unit_index + generation + revision. Tenant, order,
event, authority, scope, version and source-generation must also match.

| Active barrier | Apply behavior | Release |
| --- | --- | --- |
| This event's exact ADD holds | Eligible after all other guards | Only these exact rows |
| Another well-formed ADD event's item holds | Independent ADD permitted | Preserved |
| Manual (order or item), PR2, historical order barrier | Reject without mutation | Never |
| Cancellation/replacement/obsolete/unknown hold | Reject without mutation | Never |
| DELETE/EDIT/REPLACE evidence | Conservative rejection | Never |

Current revision and generation are checked within the serialized transaction.
A same-key successful replay returns its original receipt even after fences move.
A new key for an already applied event returns the existing application, never
reassigns or creates pieces. Different payload under the same key is rejected.
Historical non-ADD evidence remains conservative; resolving it is outside PR4.

## Atomic application

One existing operational_owner Mongo snapshot/majority transaction:

1. Revalidates authorization, flags, source evidence, lifecycle, exact holds and fences.
2. Resolves the component recipe using frozen customer selections and calculates
   new reservations through PR2's unchanged _allocate function.
3. Appends only the new plan line; inserts new component units and allocations.
4. Creates the new batch/registry/assigned physical pieces, options and service plan.
5. Appends the reviewed workflow item, immutable applied receipt/audit and employee
   notification outbox linked to the source OrderChangeEvent.
6. Saves the idempotent result and releases only exact ADD holds as its final writes.

No stock deduction occurs during ADD. Inventory allocations are required for every
tracked demand; products with no tracked demands retain an explicit component unit
with an empty demand/allocation set under the existing recipe contract.
Notification outbox failure is transactional. Notification delivery is not the guard:
active holds and existing execution guards enforce eligibility independently.

A restricted salla_add write profile permits inserts for new artifacts only. Existing
pieces, component units and preparation allocations cannot be updated/deleted by
this profile. Workflow/plan mutations only append the new item/line. Hold writes
require exact v5 item identity and cannot target a general order hold. Financial,
invoice, stock deduction and provider mutations are excluded.

## Verification and rollback

Tests run on isolated loopback MongoDB 8.0.12 replica set, with generated test-only
databases, no application DB fallback and no production credentials. They cover
quantity/options/variants, duplicates/retries, independent clients, ADD/DELETE and
ADD/ADD races, holds, stale fences, employee visibility, failure injection using
Mongo validators, old-unit preservation and financial isolation. Frontend tests
exercise display, strict gating, errors, timeout retries and duplicate submission.

Any local failure rolls back new artifacts, revision, event/outbox/idempotency and
hold release together. Disable the existing feature flag to stop new application;
keep committed receipts and exact hold history intact. Do not re-hold or undo a
completed application via rollback. No Recovery operation is introduced.

No merge, deploy, production write, accounting writer, Salla mutation or edits to
PR1/PR2/PR3/PR3.1 branches are part of this task.
