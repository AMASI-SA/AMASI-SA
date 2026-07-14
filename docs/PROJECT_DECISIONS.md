# Mezan OS Project Decisions

This file records binding architectural decisions and their reasons.

---

## Decision-000 — One Data, One Owner

Status: Approved

Every business fact has one authoritative owner.

No page, report or integration may maintain a separate competing version of the
same fact.

Readers are unlimited. Writers are controlled.

---

## Decision-001 — Salla Owns Orders

Status: Approved

Salla is the external source of truth for:

- Orders
- Customers
- Ordered items
- Payment details
- Shipping details
- Order statuses
- Order creation date

Webhook events signal a change. Mezan may fetch full order details from Salla
before normalizing them.

---

## Decision-002 — Mezan Owns Operational Inventory

Status: Approved

Mezan operational inventory is the only inventory source used for operational
decisions.

Do not use:

- Salla inventory
- Qoyod inventory

Operational inventory is separate from accounting inventory.

---

## Decision-003 — Qoyod Products Are Service Items

Status: Approved

Mezan sends Qoyod service products for invoicing and accounting.

Qoyod does not own Mezan's physical inventory, preparation or supplier
workflow.

---

## Decision-004 — Order Item Is the Operational Unit

Status: Approved

Operational state attaches to `order_item_id`.

Do not use product_id alone as the identity of preparation, reservation,
supplier assignment or receiving.

---

## Decision-005 — Availability Before Supplier

Status: Approved

An order item cannot enter a supplier purchase batch before Mezan checks
operational inventory for matching stock.

A user may override only with permission and a recorded reason.

---

## Decision-006 — Prevent Duplicate Work

Status: Approved

An order item cannot simultaneously be:

- In two active purchase batches
- Reserved twice
- Assigned to conflicting active workflows
- Presented again as available for supplier upload after allocation

Backend and database constraints must enforce this. UI hiding alone is not
sufficient.

---

## Decision-007 — Workflows, Not Page Proliferation

Status: Approved

A complete employee task should be performed in one workspace.

Prefer drawers, tabs, dialogs and inline actions over separate pages.

---

## Decision-008 — Reduce Pages Over Time

Status: Approved

The final product should have fewer production pages than the current system.

Temporary diagnostics and repair screens must have a retirement plan.

---

## Decision-009 — Progressive Replacement

Status: Approved

Do not delete legacy features before the replacement is validated.

Do not preserve legacy logic merely because it already exists.

Each capability must be classified:

- REUSE
- REFACTOR
- REPLACE
- DELETE

---

## Decision-010 — Order Creation Date Controls Ordering

Status: Approved

Order lists sort and display by Salla order creation date.

Status updates, resync operations and webhook receipt times must not change list
position.

---

## Decision-011 — Marketing Attribution Reaches Order Item

Status: Future, architecture reserved

Marketing attribution must support:

    Campaign → Order → Order Item

The future engine must report per product and variant:

- Advertising spend
- Attributed sales
- CPA
- ROAS
- Gross profit
- Net profit

Order Engine must preserve identifiers needed for this future capability.

---

## Decision-012 — Simple Employee Experience

Status: Approved

Employees must not need to understand internal engines.

Example workflow:

1. Select order items.
2. Select employee and supplier.
3. Submit once.
4. Mezan checks availability automatically.
5. Mezan reserves matching stock or creates the supplier workflow.

When stock exists, show only:

- Use available stock
- Send to supplier anyway

---

## Decision-013 — Permissions Protect Details and Actions

Status: Approved

Permission checks must exist in Backend and Frontend.

Future permission groups include:

- Orders visibility
- Customer visibility
- Payment visibility
- Accounting visibility
- Profit visibility
- Advertising cost visibility
- Preparation assignment
- Supplier upload
- Availability override
- Inventory reservation
- Receiving confirmation

---

## Decision-014 — Selective Reuse

Status: Approved

Reuse correct infrastructure.

Refactor or replace conflicting business logic.

The goal is not rebuilding everything and not preserving everything. The goal
is the lowest long-term implementation and maintenance cost.

---

## Decision-015 — Documentation Is Part of the Codebase

Status: Approved

Architectural decisions, roadmap status and sprint acceptance criteria must be
updated with implementation changes.

Important future capabilities must not exist only in conversations or generic
TODO comments.

## Decision-024 — Manufactured Products Are Composite Workflows

Any manufactured or assembled product is represented through:

1. Order Item — what the customer purchased.
2. Components — physical inputs and stock parts.
3. Production Operations — manufacturing and assembly steps.

A manufactured Order Item must not be treated as one indivisible supplier
purchase.

## Decision-025 — Supplier Ownership Is Below Order Level

Supplier assignment belongs to a Component or Production Operation.

A parent Order may summarize suppliers but must not own one authoritative
supplier field.

## Decision-026 — Receiving Is an Event

The receiving employee is recorded only when an item or operation is ready
and the employee performs the receiving action.

Receiving must not be preassigned when the item is uploaded or initially
sent to a supplier.

## Decision-027 — Cost Engine Is Deferred

Detailed product cost rules, option-based cost, supplier liability,
inventory consumption valuation and production cost allocation are deferred
until the Product Definition phase.

Current contracts must preserve all required identity and option data
without calculating cost.
