# PR2: fulfillment component reservation reconciliation

Draft #1273 depends on unmerged PR1 #1272, canonical HEAD
`25461ddb94002785b026c4bef6115466691f4b2d`, tree
`0e08b71fd6a4e8dc5d7ac7e5207f89eca289dc93`. No existing PR1 file is changed.

## Contract and scope

`preview_reconciliation` and `reconcile_components` are internal service functions,
not registered HTTP routes. Both lifecycle and component-reconciliation flags must
be explicitly true for a new write; both default off. Add, edit options, cancel/
delete and replace remain unavailable. Existing commercial plan lines and customer
selection context are frozen. Only existing units whose resource recipe changed
can receive replacement reservations. This does not implement commercial changes.

Preview binds workflow revision, order generation digest, plan revision, selected
unit generations and a digest of recipes/dependencies. Apply requires the complete
preview, manager authority, reason, idempotency key and an active order hold.
Identity is order_item_id + unit_index + generation; legacy units start at zero.
The active legacy unit slot remains stable, with previous generations and all old
allocations retained in immutable before/after audit. No row or history is deleted.

## Component state machine and full-cycle comparison

| Before | Reconciliation | After and execution |
| --- | --- | --- |
| Reserved, unchanged recipe | Preserve byte-for-byte | Same generation/allocation |
| Reserved, changed recipe | Release reservation pressure inside transaction; allocate new demands | Reserved generation + 1; old allocation retained in audit |
| Consumed, changed recipe | Preserve consumption and stock deduction | Explicit consumed_component_preserved requirement |
| Some units consumed, others reserved | Preserve consumed units; replan selected reserved units only | Mixed result, explicit reconciliation_required |
| Ambiguous/intra-unit partial consumption | Do not infer remaining stock or release anything | Explicit ambiguous_or_partial_consumption requirement |
| Prebuilt unit | Preserve receipt/claim provenance | Explicit prebuilt_provenance_requires_review |
| Assigned/advanced preparation dependency | Preserve pieces and preparation assignments | Explicit preparation_assignment_requires_review |
| Unselected units at any stage | No write | Original lifecycle/allocations/history remain identical |
| Ready-to-ship/completed/delivery workflow | Reject | No reconciliation writes |

Plan outcome is `reconciled_held` or `reconciliation_required`; unresolved actions
persist across later subset requests. Component lifecycle is marked
`reconciliation_required`, accepted=false. No activation occurs in PR2.

A durable authority-scoped version-3 order hold is installed atomically. Existing
PR1 guards enforce it, while PR1 resume only releases version-2 holds. A webhook
may legitimately refresh the source lifecycle to reserved; it cannot clear this
independent activation barrier. A future approved activation contract is required.
No release endpoint for this barrier is added here.

## Allocation and transaction behavior

This PR reconciles **component stock reservations**. Preparation assignment registry
changes are deliberately deferred and represented as explicit requirements; this
PR does not bypass its ownership contract. Warehouse physical quantities, consumed
allocations, prebuilt claims, physical pieces and preparation assignments are not
written. Consumed stock is never returned automatically.

The existing owner-serialized Mongo transaction contains release/reallocation,
generation changes, plan/workflow revision, activation barrier, immutable audit and
idempotency result. Insufficient stock or audit failure rolls all of them back.
Concurrent writers serialize; stale previews fail. Same-key retry returns the
committed result even after flags are disabled; differing payload reuse conflicts.
Active/uncertain execution claims block reconciliation. Duplicate stored unit
identity fails closed, rather than choosing an arbitrary record.

## Verification

The new 26-test suite runs on a real isolated MongoDB 8.0.12 replica set, including
independent clients and actual source capture/reconciliation functions. It covers
unconsumed, consumed, partial and mixed units; every fence; stale recipes; duplicate
identities/keys; concurrent writers; concurrent same-key retry; response loss;
webhook races/replay; failed audit; insufficient-stock rollback; authorization;
flags; execution claims; barrier compatibility; prebuilt provenance; customer
options; preservation of unselected pieces and financial/preparation sentinels.

Full before/after comparison includes stock units, physical pieces, preparation
allocations, warehouse stock, prebuilt claims, invoice/financial sentinels and audit.
Existing component, lifecycle, preparation, mobile, assembly, shipping and webhook
regression suites run alongside it. CI records exact source HEAD/TREE, enforces at
least 350 executed cases with zero failures/errors/skips, and uploads JUnit XML.

No Salla, Accounting, invoice or Review Completion writer is introduced or changed.
No provider mutation is invoked by the new service. No destructive migration.
Independent code review found no remaining material blockers after durable-hold
and retained-requirement fixes; actual execution evidence is in STATUS and CI.

## Rollback

Keep `ORDER_FULFILLMENT_COMPONENT_RECONCILIATION_ENABLED` and
`ORDER_FULFILLMENT_LIFECYCLE_CONTROLS_ENABLED` off. No configuration enables them.
Before any activation, reverting this additive PR removes unused service/test/docs
without altering data. After a controlled internal reconciliation, disabling the
flag prevents new work but keeps committed retry responses and existing holds.
Do not delete audits, restore consumed stock, clear the version-3 hold, or roll back
the PR1 guard. Data remains explicitly held pending a separately approved resolution
and activation contract. Transaction failures require no manual data restoration.

No merge. No deploy. Rollout OFF. Production writes = 0.
