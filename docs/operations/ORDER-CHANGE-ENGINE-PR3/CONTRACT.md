# PR3: offline Salla source reconciliation

Stacked Draft PR1274 depends on PR1273 at
`fd00eae5c2bd109be0215f89970f2794a0f55ba2`, which depends on PR1272.
No existing source file or accepted dependency is modified.

## Boundary

`replay_snapshot` is a trusted internal **offline normalized replay adapter**.
It is not registered as an HTTP endpoint, Salla webhook handler, UI action or
production worker. It receives source evidence; it is not an order editor.
The first complete, versioned fixture is explicitly seeded with `baseline=True`.
A later live adapter must prove authorization, completeness, item identity and
source ordering before it can supply this contract. A successful mock/replay does
not prove live Salla ingestion or mutation safe. No live provider client is used.

Both `ORDER_FULFILLMENT_LIFECYCLE_CONTROLS_ENABLED` and
`ORDER_SALLA_CHANGE_RECONCILIATION_ENABLED` must be exactly `true` for new writes.
Neither is enabled by this PR. Committed transport retries work when flags are off.

## Input and evidence

Arguments: tenant, order number, transport idempotency key, normalized snapshot,
and explicit baseline flag. Snapshot has timezone-aware `version`, boolean
`complete`, and items identified by `order_item_id`, `product_id`, positive integer
quantity and full options dictionary. Variant, custom fields and financial facts
are preserved. Optional source actor/reason and explicit replacement pairs are
evidence only. Actor is null with `actor_evidence=not_supplied` when absent.

The snapshot version is a normalized source timestamp for this offline contract;
it does not assume Salla supplies an authoritative monotonically increasing
integer. Tenant ownership is supplied by the trusted caller, not a public payload.
Malformed identity/duplicate item IDs/invalid quantities are rejected. Partial or
unversioned evidence is retained with a guard and awaiting-authoritative-refresh.
It cannot be interpreted as deleting omitted items.

## Change detection and state

Comparison uses source item identity, never SKU. Add, remove, variant/options,
quantity and other commercial differences are recorded. An explicit source pair
can link a removed and added line as replacement; otherwise they remain separate.
A changed product on a stable source line is a replacement candidate. Simultaneous
differences remain visible in old/new data and `changed_fields`; no handler runs.
Reopening a cancelled source creates explicit exception evidence.

Outcomes: baseline_recorded, no_change, stale_ignored, source_conflict,
awaiting_authoritative_refresh, pending_application, awaiting_execution_resolution,
exception_required. A material event always has application_state=pending_application.
There is no applied transition in PR3 and no new preparation unit is created for ADD.

Source timestamp, order workflow revision and existing unit generation are separate.
Only material intake increments workflow revision. No piece revision/generation,
assignment, component reservation, canonical commercial snapshot, invoice, totals,
shipment, stock or consumed proof is modified. Old and unselected pieces stay intact.

Older snapshots do not move the accepted source backward. Equal-version different
facts create a conflict. A newer identical snapshot advances only source evidence.
Incomplete-to-complete and pre-baseline-to-evaluable input have separate immutable
evaluation identities. Original transport retries retain their original outcome.
Refresh replay uses the same adapter; no provider refresh request is performed.

## Immutable event and notification outbox

All records use the existing append-only `mezan_fulfillment_control_events_v1`
collection, distinguished by `event_type`:

* `salla_change_intake`: source snapshot, fingerprint/version, before/after evidence,
  decision, timestamp and durable result.
* `salla_order_change`: schema_version, event_id/change_id/idempotency_key, tenant/order,
  change_type, source version/fingerprint/previous version, revision/generation,
  affected piece+item+unit+generation+revision identities, available old/new data,
  fulfillment stage, actor evidence/reason, source/received/recorded timestamps,
  required_action=reconciliation_review, application/intake state,
  financial_impact=pending_contract and salla_mutation_enabled=false.
* `salla_change_notification_outbox`: event reference, recipients (known assigned
  employees), stage fallback, affected units, actor/time, required action and initial
  pending/read_at=null/acknowledged_at=null evidence.

Outbox is a distinct durable record in the same transaction, not an external send.
No notification delivery/read/ack writer is introduced. PR8 must store delivery and
acknowledgement receipts separately; it must not mutate this immutable payload.
No event contract asserts that ADD already created new_units. Existing PR1 control
events and their schema remain unchanged; this source envelope includes validation
and financial-only events that are not commercial application commands.

## Atomicity and hard guard

The unchanged restricted `operational_owner` transaction serializes on the tenant's
existing owner row with snapshot reads and majority commit. Event, intake audit,
outbox, transport result, guard and revision commit together. Audit/outbox failure
rolls everything back. Same-key mismatched payload conflicts; repeated identical
source evidence with another key does not emit duplicate events/outbox records.

Material or ambiguous changes install a durable authority-scoped v4 **order hold**.
This deliberately pauses the whole order, including ADD and financial-only changes,
until later approved handlers resolve the pending change. It is stronger than a
piece-only hold and does not change existing piece states. PR1 already checks all
active holds; its resume endpoint cannot release v4. A sticky owner marker keeps
enforcement active after flags are disabled. Reading an alert does not unlock work.

PR1 receive/prepare/ready/assembly/addressing/shipping execution seams retain their
current/current-generation/cancellation/replacement/obsolete checks and reject the
new hold. An already acquired execution claim causes awaiting_execution_resolution.
The new guard prevents subsequent acquisitions; it cannot retract provider I/O
already issued by an earlier claim. PR3 neither clears uncertain claims nor performs
recovery, activation, shipment cancellation or any commercial lifecycle operation.
PR2's v3 barriers are preserved alongside v4.

## Isolation and rollback

The service imports no Salla client, canonical intake writer, invoice or accounting
writer. In particular it does not call persist_component_source_snapshot, whose
existing post-commit accounting observer is outside this PR's permitted boundary.
It reads source fixtures and operational records only; writes are limited to the
existing operational history/request/hold/owner-marker/workflow-revision stores.
No migration, index replacement, history deletion or stock reversal is introduced.

Disable the new flag to stop new replay work. Preserve committed audit/outbox,
idempotency results and holds. Do not remove a v4 or PR2 v3 hold through rollback,
re-enable obsolete work, clear an uncertain execution, or auto-restock consumption.
No-release semantics are intentional until an approved future application/resolution
contract exists. Before any usage, reverting these additive files removes unused
functionality; after usage retain PR1 guard enforcement and durable evidence.

## Verification boundary

Real isolated MongoDB8.0.12 replica-set tests cover source deltas, exact versions,
duplicates, conflicts, missing/partial source, refresh replay, independent clients,
lost-response retry, active worker claims, immutable events/outbox, audit failure,
rollback, flag-off guards, virtual units, untouched physical/financial sentinels and
PR1 non-resume. Affected PR1 preparation/mobile/assembly/shipping/component suites
run alongside them. Live Salla behavior is deliberately not claimed or exercised.

Rollout OFF. Draft only. No merge, deploy or recovery. Production writes = 0.
