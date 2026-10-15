# Review acceptance configuration fence

This change is stacked on the frozen review completion implementation in #1256
(`0614b7cc7cfa8a285dd5350525a532dd5a92cbe9`). It does not recover historical orders.

The approved semantic snapshot now includes a monotonic merchant configuration
version. Supported application configuration mutations and completion validation
write the same merchant fence document in their Mongo transactions, before
reading configuration. A configuration change committed after a completion
transaction starts forces a write conflict and fresh transaction retry. The
original approval is then rejected with `409 component_acceptance_changed`.
Workflow, completion event and completion result still commit together.

If completion already owns the fence, a concurrent configuration writer waits
or retries until completion commits. That configuration change is ordered
**after** completion; a successful completion in this ordering is valid.
Provider calls remain outside Mongo transactions. Verified provider success is
retained after a configuration conflict. Explicit reapproval requires a new
operation identity; reconciliation cannot replace the original snapshot.

## Participating application writers

The server injects `AcceptanceConfigDatabase` into its existing route factories.
Only acceptance fields in settings, product identities, operation profiles,
resource bindings, option bindings, component resources and preparation defaults
participate. The adapter joins an existing session transaction when supplied.
Unrelated cost, display and synchronization metadata updates delegate unchanged.
No-op semantic updates do not increment the version. Inserts, deletes, upserts
and changes subsequently reverted to their old values are covered.

Writer audit: product_v2_routes, product_v2_sync_hotfix,
product_v2_recent_sync_routes, product_creation_routes,
product_v2_details_routes, product_control_center_routes,
product_v2_workspace_routes, product_fulfillment_routes,
product_group_link_routes, product_option_cost_routes,
component_edit_routes, component_category_required_routes,
order_review_export_controls and supplier_receiving_routes.
These modules are not modified. Their injected database supplies coordination.

The version is deliberately merchant-wide. A semantic change to another product
of the same merchant can invalidate an in-flight approval conservatively.
No financial collection is added to the operational write capability; only the
new acceptance fence collection is added.

## Boundaries

This is an application protocol, not a Mongo authorization boundary. Privileged
raw database clients and manual Mongo writes bypass it and must not be used to
mutate acceptance settings concurrently with completion. New writers must use
the injected database. Unscoped/mixed-owner writes, update pipelines and bulk
configuration writes fail closed; no such form was found in the writer audit.
Replica-set transactions are required. Existing operations without the new
snapshot version fail closed and are not silently migrated.

## Verification

`tests/test_review_acceptance_config_guard.py` exercises the real replica set,
the wrapped application database and the review ASGI route. It covers a config
commit after transaction snapshot creation, the opposite serialization order,
ABA changes, phantom creation/deletion, transaction rollback, no-op and other
owner writes, owner boundaries, keyword/positional CRUD and find-and-modify
upserts. Provider transport is synthetic; no live Salla mutation occurs.

The review CI runs the original 100 cases, the 11 existing snapshot regressions
and the new fence suite at the exact PR HEAD. G47 also runs on the stacked PR
base. Both retain JUnit evidence and reject skips/failures.
