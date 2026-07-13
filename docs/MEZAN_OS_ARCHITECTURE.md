# Mezan OS Architecture

Status: Approved
Version: 1.0
Architecture gate: Mandatory

## 1. Purpose

Mezan OS is an ecommerce operating system centered around the order and the
individual order item.

The system must remain simple for users even when internal workflows are
complex.

## 2. Sources of Truth

### Salla — Order Source

Salla owns external commerce data:

- Orders
- Customers
- Ordered products and variants
- Payment information
- Shipping information
- Order statuses
- Order creation date

Salla inventory is not a source of truth for Mezan.

### Mezan — Operational Source

Mezan owns operational data:

- Operational inventory
- Availability checks
- Inventory reservations
- Returned-item inspection
- Preparation workflows
- Purchase batches
- Suppliers
- Employee assignments
- Quality control
- Internal receiving
- Shipping readiness

### Qoyod — Accounting Source

Qoyod owns accounting records:

- Invoices
- Invoice payments
- Accounting entries
- Accounting reports

Products sent from Mezan to Qoyod are service items. Qoyod inventory is not
used by Mezan.

## 3. Single Source of Truth

Every business fact has one owner.

Rule:

    One Data
    One Owner
    Unlimited Readers

Pages must not calculate or persist their own version of business data.

Business logic belongs in an engine or service. Frontend pages are presentation
and workflow layers only.

## 4. Core Entity Model

The primary hierarchy is:

    Order
      └── Order Item

Operational workflows attach to `order_item_id`, not only to product_id or SKU.

The same product can appear in multiple orders with different:

- Color
- Size
- Variant
- Personalization
- Customer text
- Supplier
- Assigned employee
- Preparation status
- Inventory source

Each order item therefore requires a stable identity.

## 5. Engine Ownership

### Order Engine

Owns the normalized order read model and order-item identities.

### Availability Engine

Checks Mezan operational inventory before an item may be sent to a supplier.

It must not read inventory from Salla or Qoyod.

### Operational Inventory Engine

Owns physical operational availability, reservations, returns, inspection and
issue transactions.

### Preparation Engine

Owns preparation assignment, readiness, internal receiving and employee
responsibility.

### Purchase Engine

Owns purchase batches, suppliers and supplier-bound order items.

### Shipping Engine

Owns internal shipping readiness and handoff states.

### Accounting Engine

Owns Mezan's accounting view and integrations with Qoyod.

### Marketing Intelligence Engine

Future engine that connects:

    Campaign
      └── Order
            └── Order Item

It will calculate product-level advertising cost, attributed sales, ROAS, CPA
and profit.

## 6. Availability Before Supplier

No order item may be added to a supplier purchase batch before an availability
check.

Matching must consider the actual specification:

- Product or parent product identity
- Variant ID
- SKU
- Color
- Size
- Material
- Personalization that affects interchangeability
- Quality status

When matching stock exists, the user sees a simple choice:

- Use available stock
- Override and send to supplier

An override requires permission and a recorded reason.

## 7. Workflow Simplicity

Internal engines must not force users to navigate between many pages.

A task should be completed from one workspace using:

- Drawers
- Dialogs
- Tabs
- Side panels
- Inline actions

If a user must move between multiple pages to complete one operational task,
the workflow requires redesign.

## 8. Workspace Strategy

The long-term frontend should converge toward a small number of workspaces:

- Dashboard
- Orders
- Products
- Operations
- Accounting
- Reports
- Settings

Capabilities should be embedded in these workspaces instead of creating a new
page for every task.

## 9. Progressive Replacement

Legacy code is classified per capability:

- REUSE
- REFACTOR
- REPLACE
- DELETE

Infrastructure may be reused when correct:

- Authentication
- Users
- Permissions
- Database connection
- Layout
- Theme
- Shared UI components

Legacy business logic must not be reused when it:

- Duplicates data
- Reads a conflicting source
- Recalculates the same metric separately
- Couples pages directly to old collections
- Prevents future legacy removal

Legacy removal sequence:

    Replacement
      → Validation
      → Migration
      → Usage confirmation
      → Deletion

## 10. Date Rule

Order lists display and sort by the order creation date from Salla.

Order updates must not move an old order to the top of the list.

Updated timestamps belong in timelines and diagnostics only.

## 11. Frontend Rule

React must not contain authoritative business calculations.

Frontend code may:

- Format values
- Render states
- Collect user intent
- Submit commands
- Display engine results

Frontend code must not become a second business-data source.

## 12. Page Reduction Rule

The number of production pages should decrease over time.

Temporary pages such as diagnostics, repair tools, dry-run tools and migration
screens must be hidden or deleted after their purpose ends.

## 13. Development Gate

Every capability must pass:

    Decision
      → Architecture
      → Acceptance criteria
      → Implementation
      → Tests
      → Production validation
      → Adoption
      → Legacy removal

A later phase must not begin before the current phase is accepted.
