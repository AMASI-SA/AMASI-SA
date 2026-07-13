# Order Capability Audit

Status: Approved for Sprint 001
Audit date: 2026-07-13

## Executive Finding

The current order domain is fragmented across many pages, APIs and writers.

`unified_orders` is widely consumed, but it is not a clean Order Engine:

- Multiple integrations write different fields into it.
- Salla, Make, Excel, BNPL, settlements, costs and repair processes mutate it.
- Multiple pages expose overlapping order workflows.
- Some pages calculate or interpret the same business facts independently.

Sprint 001 must establish one authoritative read contract before adding any
operational features.

## REUSE

Reuse the following infrastructure:

- Authentication
- Users and permissions
- MongoDB connection
- Main FastAPI router
- Salla OAuth and token storage
- Salla API client
- Salla webhook signature verification
- Preservation of full Salla raw payload
- Shared frontend layout, theme and generic UI components
- Existing Qoyod integration during the transition, isolated from the new
  Order Engine read contract

## REFACTOR

Move useful capabilities behind the new Order Engine contract:

- Salla order fetching
- Order pagination
- Search
- Filters
- Export
- Order status policy
- Product and variant normalization
- Product images
- Customer details
- Payment and receiving-bank details
- Shipping details
- Order event timeline
- Product cost and settlement references as external engine projections

Refactored capabilities must not write independent competing versions of the
order.

## REPLACE

Replace these patterns:

- Orders Workspace reading from the legacy `/api/orders` endpoint
- Frontend reading Mongo-shaped documents
- Order details implemented by searching a paginated list
- Legacy source precedence controlling authoritative Salla order facts
- Independent page-level calculations
- Multiple pages representing the same order queue
- Direct `unified_orders` queries from future workspaces

## DELETE AFTER REPLACEMENT

Retire after their replacement is accepted and usage is confirmed:

- Legacy Orders page
- Orders diagnostics pages
- Separate eligible, pending and unsent order pages
- Temporary Qoyod migration, fresh-start, dry-run and repair pages
- Duplicate webhook and reconciliation screens
- Temporary developer-only production routes
- Any sidebar entry whose capability is embedded in a workspace

Deletion requires:

1. Replacement exists.
2. Production validation passes.
3. No remaining route or workflow dependency exists.
4. Usage report confirms retirement is safe.
5. Git history preserves recovery.

## Authoritative Ownership

### Salla owns

- Order identity
- Order creation date
- Customer snapshot
- Ordered items and variants
- Payment details
- Shipping details
- Order status and events

### Mezan engines own

- Operational inventory
- Availability
- Reservations
- Preparation
- Purchase batches
- Suppliers
- Employee responsibility
- Internal receiving
- Shipping readiness
- Profit and marketing projections

### Qoyod owns

- Accounting invoice
- Invoice payment
- Accounting records

## Sprint 001 Source Policy

During Sprint 001:

- Salla raw order data is the authoritative input.
- `unified_orders` may be used only as a temporary discovery/index bridge.
- The new API must expose a normalized DTO, never a raw Mongo document.
- The new API is read-only.
- It performs no Qoyod calls.
- It performs no operational inventory writes.
- It performs no preparation or supplier actions.

## New API Contract

The target endpoints are:

- `GET /api/orders-v2`
- `GET /api/orders-v2/{order_number}`

The list endpoint returns summary DTOs.

The detail endpoint returns one complete order DTO with:

- Order
- Customer
- Payment
- Shipping
- Items
- Images
- Variants
- Options
- Personalization
- Timeline placeholders
- Future engine projection placeholders

## Gate Before Implementation

Do not implement Availability, Preparation, Purchase, PDF or Marketing until:

- The list endpoint is accepted.
- The detail endpoint is accepted.
- Orders Workspace consumes only the new contract.
- Creation-date ordering is verified.
- No duplicate orders appear.
- Full Salla details are preserved.
