# Local review completion implementation

Base: `128bfc1c2b4670d853a8bce33f15a667d2fdb120`. New operations use `completion_mode=mezan_local_v1`; approval snapshot contract remains versioned independently.

## Atomic boundary
New review requests validate durable source, acceptance, workflow revision, eligibility, component lifecycle and instructions, then commit reservations, workflow/items, one deterministic completion event and the completed operation in one existing owner transaction. No Salla call, provider confirmation or readback. Local completion begins at reviewed; assignment/start remains in_progress and completed preparation remains ready_to_ship.

## Shared policy
A local workflow is authoritative only with a matching completed operation for the same tenant/order/mode. Supplier dispatch, preparation workspace, allocation advancement and assembly membership use this proof plus existing business/component guards. External status stays external. Later shipping integration is unchanged.

Direct warehouse/operational assembly is visible at reviewed without promoting readiness. Its first actual assembly action advances in_progress. Completion still requires all source units, required preparation receipts, current component/source authority and current address readiness. An incomplete-address order retains assembled work without a shipping batch; an idempotent later readiness check does not consume stock again. Carrier and printing implementations remain unchanged.

## Compatibility
Existing provider-backed operations retain their immutable approval/dispatch evidence and implementation. The public new-local route will not reinterpret unresolved legacy operations. Automatic provider resumption is paused; no migration, backfill, historical retry or redispatch. Unknown modes fail closed. A safe rollback must retain understanding of completed local workflows; reverting to a provider-only reader after local completions is not safe.

## Verification
Use a loopback MongoDB 8.0.12 replica set, synthetic orders and provider transport doubles only. Test success/uniqueness, forbidden external calls including detail/images, business and component rejection, transaction rollback, timeout/restart, local eligibility despite Salla pending, queue pagination/count, legacy evidence immutability, downstream assignment/assembly, and unchanged shipping/financial boundaries. Retain legacy contracts in explicitly isolated test harnesses.

Measure baseline production source versus candidate on the same synthetic fixture and isolated replica set: Salla and Mongo command counts, transaction duration, latency p50/p95/p99, process CPU, event-loop lag, and reviewed-product visibility latency. Provider latency is simulated and must be labelled; no production performance claim.

## Scope and release
Application changes only to review completion, its local consumers and tests/CI. No shipping/printing implementation, accounting, Android, dependency, Release Guard or Intent edits. Draft PR only after tests. No merge/deploy/release or production access.
