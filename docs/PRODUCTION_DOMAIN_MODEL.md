# Mezan OS Production Domain Model

Status: Draft for operational validation
Version: 1.0
Date: 2026-07-14

## 1. Purpose

This document defines how Mezan represents products that may:

- Contain several components.
- Use stock components purchased in bulk.
- Pass through multiple suppliers or workshops.
- Require several sequential or parallel production operations.
- Be prepared by different employees.
- Be received only after the relevant item or operation becomes ready.

Cost calculation and pricing rules are intentionally deferred until the
Product Definition phase.

## 2. Core Principle

The smallest operational unit in Mezan OS is:

`Order Item`

One order may contain several Order Items.

Each Order Item may have:

- Different components.
- Different suppliers.
- Different preparation employees.
- Different manufacturing operations.
- Different readiness and receiving events.

Operational facts that apply to only one Order Item must not be stored on
the parent Order.

## 3. Domain Layers

### 3.1 Order Item

Represents exactly what the customer purchased.

Examples:

- Abaya, size 60.
- Name necklace with flower.
- Bracelet with engraving.

The Order Item preserves the customer-selected snapshot:

- Product identity.
- Variant.
- Size.
- Color.
- Material.
- Personalization.
- Uploaded references.
- Quantity.

The Order Item does not directly own a single supplier or a single
preparation employee.

### 3.2 Component

A Component is a physical input consumed or assembled into the final item.

Examples:

- Chain.
- Name plate.
- Flower.
- Jump ring.
- Stone.
- Box.
- Bag.
- Ribbon.

A Component may be:

- Purchased in bulk and stored in Mezan operational inventory.
- Purchased for one specific Order Item.
- Produced through another workflow.
- Optional according to customer selections.

A Component is not required to be a sellable Salla product.

### 3.3 Production Operation

A Production Operation is one manufacturing or preparation step performed
on an Order Item or one of its Components.

Examples:

- Calligraphy preparation.
- Laser cutting.
- Engraving.
- Polishing.
- Plating.
- Assembly.
- Quality inspection.
- Packaging.

Each operation may have:

- Its own supplier or workshop.
- Its own assigned employee.
- A required predecessor operation.
- A readiness state.
- A handoff or receiving event.
- Photos and notes.
- Start and completion timestamps.

### 3.4 Supplier Assignment

Supplier assignment belongs to a Component or Production Operation.

It does not belong directly to the whole Order.

One Order Item may therefore involve:

- No supplier, when fulfilled fully from inventory.
- One supplier.
- Several suppliers in sequence.
- Several suppliers working on different components.

### 3.5 Preparation Responsibility

The preparation employee is assigned at the Order Item or operation level.

One order may have several preparation employees because each item may be
prepared independently.

There is no single authoritative `preparation_employee` field on the Order.

### 3.6 Receiving

The receiving employee is not assigned when the item is initially uploaded
or sent to a supplier.

Receiving becomes available only after:

- The relevant Order Item is marked ready, or
- The relevant Production Operation is marked ready for handoff.

The employee who performs the actual receiving action is recorded at that
time.

Receiving is an event, not an initial static assignment.

## 4. Example: Custom Name Necklace

Order Item:

`Name necklace with flower`

Components:

1. Name plate.
2. Chain.
3. Jump rings.
4. Flower.
5. Packaging materials.

Possible workflow:

1. Calligrapher prepares the name design.
2. Cutting workshop cuts the name plate.
3. Engraving workshop finishes engraving.
4. Plating workshop plates the name plate.
5. Inventory supplies one chain.
6. Inventory supplies jump rings and flower.
7. Assembly operation connects all components.
8. Quality inspection.
9. Item marked ready.
10. Receiving employee records receipt.
11. Item becomes available for final order preparation and shipping.

## 5. Example: Abaya

Order Item:

`Abaya, size 60`

The identity contract preserves size 60.

The future Product Definition may determine:

- Standard manufacturing operation.
- Size-dependent manufacturing variation.
- Packaging components from inventory.

Cost rules are not defined in this document.

## 6. Derived Order Summary

The Order page may display summaries such as:

- Number of suppliers.
- Number of preparation employees.
- Ready items count.
- Items waiting for suppliers.
- Items waiting for receiving.

These values must be derived from Order Items, Components and Operations.

They must not become separate competing sources of truth on the Order.

## 7. State Ownership

### Order Item owns

- Customer-selected identity snapshot.
- Overall operational readiness derived from its workflow.
- Links to Components and Production Operations.

### Component owns

- Component identity.
- Required quantity.
- Fulfillment source.
- Reservation or consumption reference.
- Supplier link when externally sourced.

### Production Operation owns

- Operation type.
- Sequence and dependencies.
- Supplier or workshop.
- Responsible preparation employee.
- Operational state.
- Ready timestamp.
- Handoff and receiving events.

### Receiving Event owns

- Receiver employee.
- Received quantity.
- Received timestamp.
- Inspection outcome.
- Notes and evidence.

## 8. Deferred Topics

The following are intentionally deferred:

- Product costing.
- Supplier liability generation.
- Inventory valuation.
- Size-based price or cost rules.
- Component cost allocation.
- Manufacturing labor cost.
- Profit calculation.
- Marketing attribution.

The current architecture must preserve the information required for those
future engines without implementing their calculations now.

## 9. Non-Negotiable Rules

1. One Order Item may use multiple suppliers.
2. One Order may have multiple preparation employees.
3. Supplier data must not be stored as one field on the parent Order.
4. Receiving employee is recorded only when receiving actually occurs.
5. Salla inventory is not a source of truth.
6. Mezan owns operational inventory and reservations.
7. Qoyod continues to treat sale products independently as service items.
8. Cost logic must not enter the Order Item identity contract.
9. UI summaries are derived; operational entities remain the source of truth.
10. Every future UI must be responsive across mobile, tablet and desktop.

## 10. Next Contract

After this document is operationally approved, the next contracts will be:

1. `ComponentIdentityDTO`
2. `ProductionOperationDTO`
3. `OperationDependencyDTO`
4. `ReceivingEventDTO`

No persistence, APIs or UI should be added before these contracts are
reviewed together.
