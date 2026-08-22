# Operations Monitoring Contract

This document is intentionally additive and defines the deterministic contract for the Amasi mobile operations monitoring experience.

## Scope

- Monitoring has two views: preparation employees and couriers.
- The default rolling period is the last 30 days; callers may select another explicit range.
- Monitoring does not own workflow state. It reads canonical preparation/shipment facts.
- Operational actions are read-only from monitoring. Product cost/service maintenance remains available only through the existing governed product-cost/service routes.

## Preparation employees

For each employee and selected period, expose:

- pending review count
- in-progress count
- delivered/completed count
- currently held pieces
- ready but not handed-off pieces
- `average_preparation_seconds`
- `measured_count`

The average is the arithmetic mean of every completed physical `piece_id` whose completion timestamp is inside the selected period and which has a valid actual `started_at` and `completed_at`. Duration is `completed_at - started_at`. Missing, zero, or negative durations are excluded. All products are included.

Employee drill-down must preserve the same information architecture as “My Products”: status buckets, preparation file/invoice context, product image, options/specifications, required services, and current preparation facts. Monitoring must not expose invoice deletion, item deletion, supplier dispatch/upload, supplier reassignment, workflow-state mutation, custody handoff/receipt, quantity mutation, or preparation-file recreation.

Clicking a product image may navigate to the existing governed product page. From there an authorised user may update product cost and product/service cost configuration using the existing deterministic routes. Those edits must not mutate preparation timestamps or custody/workflow state; invoice views should reflect the authoritative cost/service change on refresh/revalidation.

## Couriers

For each courier and selected period, expose shipment counts and `average_delivery_seconds` plus `measured_count`.

The delivery average is the arithmetic mean for delivered shipments whose `delivered_at` is inside the selected period and which have valid `out_for_delivery_at` and `delivered_at`. Duration is `delivered_at - out_for_delivery_at`. Missing, zero, or negative durations are excluded. `assigned_at -> delivered_at` may be exposed separately as an administrative assignment-cycle metric and must never be mixed into actual delivery time.

## Date-range semantics

Default: rolling last 30 days from now. UI presets may include 7 days, 30 days, current month, previous month, and a custom range. All cards and drill-down counters use the same selected range.

## Architecture and safety

This feature follows ADR-001 additive/backward-compatible/SSOT/multi-tenant rules and ADR-002 deterministic-core rules. Monitoring is a view over canonical operational facts and does not introduce a competing source of truth.
