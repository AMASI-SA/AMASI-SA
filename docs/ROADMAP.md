# Mezan OS Roadmap

## Status Legend

- DONE
- IN PROGRESS
- BLOCKED
- PLANNED
- FUTURE
- RETIREMENT

## Foundation

### Architecture documentation
Status: IN PROGRESS

### Orders Workspace initial owner-only shell
Status: DONE
Commit: 95a9d00

### Legacy capability audit
Status: PLANNED

Classify current order-related code and pages:

- REUSE
- REFACTOR
- REPLACE
- DELETE

## Sprint 1 — Order Engine Foundation

Status: IN PROGRESS

Scope:

- Order Engine API contract
- Order list endpoint
- Order detail endpoint
- Salla order creation date
- Full order normalization contract
- Stable order-item identity contract
- Orders Workspace consumes one API source
- Owner-only access initially
- Read-only implementation
- No Qoyod writes
- No operational inventory writes

Not included:

- Inventory allocation
- Supplier batches
- Preparation actions
- PDF generation
- Marketing attribution
- Accounting calculations

## Sprint 2 — Order Item Foundation

Status: PLANNED

Scope:

- Stable `order_item_id`
- Product and variant identity
- SKU
- Images
- Color
- Size
- Options
- Personalization
- Customer-entered fields
- Quantity and pricing snapshot

## Sprint 3 — Operational Inventory and Availability

Status: PLANNED

Scope:

- Mezan-only operational inventory
- Returned-item inspection
- Quality status
- Availability matching
- Reservations
- Issue confirmation
- Override permissions and reasons
- Duplicate allocation prevention

## Sprint 4 — Preparation and Purchase Workflow

Status: PLANNED

Scope:

- Assigned preparation employee
- Supplier
- Purchase batch
- Multiple orders per batch
- Mixed or identical products
- Supplier-ready confirmation
- Internal receiving confirmation
- Shipping readiness
- Full audit timeline

## Employee OS Foundation — Employee, Payroll and Fulfillment Access

Status: EMPLOYEE MANAGEMENT COMPLETE; PAYROLL CUTOVER PENDING

Phase 1:

- Canonical employee identity in Mezan V2
- Read-only migration preview from `operating_salaries`
- Explicit login-account and operational-role linkage
- Ledger-backed salary payable, advance and custody snapshot
- Idempotent `shadow_read_only` employee and salary-contract migration
- Owner-only migration and audit controls

Phase 2 employee management:

- Owner-managed creation and editing for native and migrated employees
- Active/inactive employee lifecycle with immediate login revocation
- V2-only link, unlink and password reset for non-owner team accounts
- Canonical operational role and permission assignment, including the
  assigned-work-only preparation role
- Search and filters for status, account and role across responsive employee cards
- Append-only before/after employee activity timeline
- Zero writes to legacy payroll, liabilities, advances, custody or ledger
- Existing migrated salary, advance and custody data remains read-only until
  payroll cutover

Required before payroll cutover:

- Resolve suggested or conflicting login links manually
- Validate employee and salary totals at 100%
- Route new salary changes through effective-dated V2 contracts
- Run one complete payroll cycle in parallel
- Verify fulfillment permissions and task assignment by canonical employee ID
- Preserve a rollback checkpoint, then retire the legacy employee page

## Sprint 5 — Unified PDF and Preparation Output

Status: PLANNED

Scope:

- Single-order preparation PDF
- Bulk preparation PDF
- Shared template and API
- Source order and order-item traceability
- Reprint audit

## Sprint 6 — Workspace Consolidation

Status: PLANNED

Scope:

- Merge useful legacy order tools into Orders Workspace
- Move diagnostics behind owner/developer controls
- Retire duplicate pages
- Reduce sidebar entries

## Sprint 7 — Accounting Integration

Status: PLANNED

Scope:

- Qoyod invoice visibility
- Payment visibility
- Service-product policy
- Accounting and profit permissions
- No inventory coupling to Qoyod

## Sprint 8 — Marketing Intelligence

Status: FUTURE

Scope:

- Campaign to product mapping
- Campaign to order-item attribution
- Product-level advertising cost
- Product sales from advertising
- CPA
- ROAS
- Profit after advertising
- Inventory and supplier recommendations

## Sprint 9 — AI Decision Layer

Status: FUTURE

Scope:

- Supplier delay detection
- Employee productivity analysis
- Variant return analysis
- Stock and campaign recommendations
- Preparation bottleneck detection
