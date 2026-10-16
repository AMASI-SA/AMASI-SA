# MZ2 smart bundles: architecture audit and acceptance contract

Status: design and source audit; acceptance below is pending. This document does
not certify implementation, production readiness, deployment, or live provider
behavior. Audit performed on 2026-10-16. Production was not changed.

## Existing authorities and extension seams

Products use `mezan_products_v2`; components use `mezan_cost_resources_v2`.
Product recipes and selected-option recipes use
`mezan_product_resource_bindings_v2` and
`mezan_product_option_cost_bindings_v2`. Product IDs have local and Salla aliases;
references must resolve under the merchant before expansion.

`stock_component_consumption_service._recipe` builds tracked resource demands.
`reserve_component_stock` freezes those demands per sale unit, allocates proven
lots, and serializes through `operational_owner`. Existing plans are replayed,
not recalculated. `consume_component_stock` and `release_component_stock` already
own deduction, duplicate handling, prebuilt provenance, and cancellation.
Bundle expansion should adapt this engine rather than introduce a second stock
writer. `_lines` currently strips unknown fields, so any snapshot payload needs
an explicit trusted normalization contract.

`fulfillment_v2_routes._component_order_lines` creates engine inputs.
`reconcile_component_order_lifecycle` enforces generation, authoritative source
watermarks, cutover, durable blocked intents, retries, and cancellation.
`_consume_batch_components` closes packing and handoff paths. Preserve these
guards. `build_order_fulfillment_decision` currently classifies parent lines;
mixed-source bundles require leaf requirements and aggregate parent readiness.

`inventory_receipt_service.place_inventory_receipt` provides receipt-ID
idempotency and capacity enforcement. Purchase receiving validates independent
product and component identities and tracked active components.
`operational_physical_receipts` separates physical stock from financial adoption.
Bundle composition must not create supplier invoices or financial postings.

## Gaps and proposed migration

1. Add a tenant-scoped, versioned composition definition on top of Products V2.
   Permit arbitrary practical member counts, including 4 and 10+, under explicit
   safe limits. Preserve product, variant, option, and component identities.
2. Expand nested bundles with recursion-stack cycle detection. Shared children
   are valid reuse, not cycles. Bound depth, expanded occurrences and multiplied
   quantities; preserve member paths for repeated occurrences.
3. Freeze the complete composition at sale acceptance: version, paths,
   quantities, selected options, costs and operational sources. Existing engine
   snapshots only cover tracked component demands. Existing option-cost
   snapshots use mutable `$set` updates and do not provide this guarantee.
4. Feed frozen demands into existing reservation/consumption. Historical plans
   must remain stable after definition edits, component edits or archival.
   Existing legacy/cutover rules must remain in force; no historical backfill.
5. Resolve service, stocked, supplier and ordinary product leaves independently,
   then aggregate readiness. Purchased finished goods currently reserve their
   recipe; only proven manufactured receipts exempt repeat consumption. Preserve
   that distinction and prevent parent-plus-child duplicate deductions.
6. Define one cost authority. Existing cost is base plus product resources plus
   selected-option costs, so naively adding child costs double counts. Missing
   costs must remain explicit rather than silently becoming zero.
7. Resolve fresh actor and merchant through
   `security_sensitive_routes.require_product_permission`; enforce authorization
   on both read and write routes. Do not copy older direct `user["id"]` routes.
8. Keep new writes inside the restricted operational transaction boundary.
   `operational_atomic` explicitly allowlists writable collections; add narrowly
   scoped permissions if new snapshot collections are necessary. Avoid financial
   capability expansion. Product operation choices are globally frozen in
   `product_fulfillment_rules`; bundle support must not silently unfreeze them.
9. Introduce additive data/schema changes first, with empty/no-bundle fallback
   preserving ordinary products. Keep migration dry-run and explicit; do not
   mutate live definitions, orders, suppliers, inventory or Salla during tests.

## Conditional customer options

Existing implementation, not a fresh provider specification:
`product_creation_routes.ProductCreationCondition` supports one parent option
index, `=` or `!=`, and a parent choice value. Normalization validates references
and rejects dependency cycles. `_condition_payload` maps resolved provider IDs
to `visibility=on_condition`, `visibility_condition_type`,
`visibility_condition_option` and `visibility_condition_value`.

Creation remains hidden at zero stock until real option/value IDs exist.
`_apply_salla_creation_conditions` applies conditions, reads back saved values,
and verifies them before publication. Existing tests cover ignored conditions,
missing IDs, ignored hiding, and timeout retries without duplicate creation.
Reuse these helpers for publish adaptation. This audit made no Salla calls and
does not establish current provider support beyond the repository contract.

Local bundle selection must evaluate the dependency chain and exclude inactive
conditional selections from demands and costs. Validate missing/invalid parent
values, stale hidden answers, chained dependencies and cycles. Snapshot the
effective selections so later condition edits cannot change accepted orders.

## Strict acceptance matrix

All rows are **pending execution and evidence**. Source inspection and runtime
availability are not passing acceptance evidence.

| Case | Required proof |
| --- | --- |
| Ordinary product | Existing create/read/cost/fulfillment behavior unchanged |
| Four mixed-source members | Supplier, stock, service and ordinary-product requirements remain individually traceable; parent readiness waits correctly |
| Ten or more members | Correct expansion, rendering, multiplied quantities and cost without fixed two-member assumptions |
| Nested reuse | Multi-level expansion and shared children work; member paths remain distinct |
| Cycles and limits | Direct/indirect cycles, excess depth/leaves and overflow fail before writes |
| Tenant and role boundaries | Cross-merchant references denied; employee permissions resolved against owner |
| Sale snapshot | Definition/cost/condition edits after acceptance do not alter prior sale composition |
| Stock lifecycle | Duplicate acceptance/consumption does not double deduct; shortage rolls back all stock mutations |
| Cancellation | Unconsumed reservations released; consumed units require reconciliation, not automatic restock |
| Provenance | Manufactured receipts avoid duplicate consumption; purchased goods do not falsely claim provenance |
| Receipt and accounting | Receipt idempotency/capacity retained; no accidental invoice/ledger writes |
| Conditional options | Active branches only contribute; hidden stale answers excluded; invalid references/cycles rejected |
| Retry and source order | Stale provider events cannot supersede current sale snapshots or cancellation |
| UI and Arabic | RTL creation/edit/detail/preview works for nested and 10+ member lists and validation errors |
| Regression | Component lifecycle, stock consumption, fulfillment, purchase receipt and write-gate suites pass |

## Local validation runtime discovery

Read-only discovery found a running Mongo process listening on loopback port
27516. Its `hello` response reported a writable primary, replica set
`salaryreview`, and only `127.0.0.1:27516` as its member. No existing database
contents were read and no database was created by this audit.

Use `MZ2_TEST_MONGO_URI=mongodb://127.0.0.1:27516/?replicaSet=salaryreview`
only with isolated UUID fixture databases. Existing component integration suites
enforce loopback and create `g47_integration_*` / `g47_component_test_*`
databases. They own cleanup of those fixture databases only.

The cached `mz2-track-b-python` runtime imports pytest, pytest-asyncio, FastAPI,
Pydantic, PyMongo, Motor and HTTPX successfully. Default system Python lacks
several required packages. Frontend dependencies exist in the original workspace
and the `mz2-frontend-baseline-0a9f1ac` checkout; the latter includes Vite but not
Vitest or `@playwright/test`. Dependency compatibility and application builds
remain to be verified separately.

## Supplier preparation boundary

Bundle parent sale lines are excluded from the legacy supplier PDF/preparation
catalogue using frozen plan/intake evidence. A stale batch containing a bundle
parent is rejected before physical-piece materialization. Those legacy files
would otherwise show sale quantity rather than the expanded quantities and
could create duplicate parent preparation work.

Bundle supplier requirements are available through the bundle requirements
report with original order, line, unit, leaf and supplier references. They are
satisfied by matching existing purchase receipts; this feature does not create
or send supplier files, create purchase invoices automatically, or post journals.
Packing consumes the existing component plan only after required leaves are
ready. These boundaries must remain visible in release acceptance; local stock
and report tests do not prove provider delivery or end-to-end supplier PDF work.
