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

Status: Approved, future phase

Marketing attribution must support:

    Campaign → Ad Group/Ad Set → Ad → Creative → Session → Order → Order Item → Profit

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

---

## Decision-016 — Preserve Raw Facts for Future Intelligence

Status: Approved

Every supported provider integration must preserve the raw payload and its
provenance before canonical transformation, subject to approved privacy and
retention controls.

A derived metric, summary or current UI requirement is not a valid reason to
discard source fields that may be needed later.

The system must retain separately:

- Provider event/source time
- Ingestion time
- Transformation version
- Raw payload or version-preserved equivalent
- Canonical entity identifiers
- Data-quality and confidence metadata

---

## Decision-017 — Mezan Is the Cross-Source Decision Layer

Status: Approved

No single commerce, advertising or analytics provider owns the complete
commercial truth.

- Salla owns order and checkout facts.
- Advertising platforms own spend, delivery, campaign and creative facts.
- GA4 and approved behavior tools own session and funnel observations.
- Qoyod owns accounting records.
- Mezan owns cross-source identity, profit, experiments, decisions and measured
  outcomes.

Mezan must optimize sustainable net profit rather than revenue or platform ROAS
alone.

---

## Decision-018 — Customer Conversations Are Strategic Data

Status: Approved, future phase

WhatsApp, email, live chat and approved social-support conversations are a
formal Voice of Customer source.

The future Voice of Customer Engine must preserve raw messages and extract
versioned, confidence-labeled signals such as:

- Purchase intent
- Questions and objections
- Reasons for not purchasing
- Product, shipping, payment and trust problems
- Complaint themes and sentiment
- Support outcome and later order outcome

Conversation insights must be linkable, when legitimately identifiable, to the
customer, order, product, session and marketing source.

---

## Decision-019 — AI Decisions Require an Audit Trail

Status: Approved, architecture mandatory

Every AI recommendation and executable action must create a Decision Log that
records:

- Observation
- Evidence and source references
- Confidence
- Expected impact and risk
- Proposed action
- Required approval
- Approval, rejection or modification
- Execution result
- Measured outcome
- Rollback state

The system must be able to explain why an action was proposed or executed.

---

## Decision-020 — Autonomy Must Be Progressive and Bounded

Status: Approved

AI autonomy advances only through explicit gates:

1. Observe.
2. Explain and recommend.
3. Prepare actions for approval.
4. Execute approved low-risk actions within limits.
5. Run bounded autonomous experiments.
6. Expand autonomy only after measured reliability.

Campaign or content automation requires:

- Budget and loss guards
- Approval thresholds
- Action allowlists
- Cooldown periods
- Audit logs
- Rollback procedures
- Emergency stop

No future agent may receive uncontrolled advertising-spend or publishing
authority.

---

## Decision-021 — Generated Content Is Versioned and Measured

Status: Approved, future phase

AI-generated product copy, images, videos, campaign copy and communication
assets must be stored as versioned creative assets.

Each asset must be traceable to:

- Prompt or creative brief
- Source product facts
- Human approval state
- Campaign and placement usage
- Performance outcome

Generated assets must pass brand, factual, legal and platform checks before
publishing permissions expand.

---

## Decision-022 — Intelligence Must Distinguish Fact from Inference

Status: Approved

Provider facts, Mezan calculations and AI inferences must remain distinguishable
in schemas and APIs.

AI-generated attributes require:

- Inference type
- Model/version
- Confidence
- Created time
- Evidence references

Sensitive customer attributes must not be guessed from names, email addresses or
other weak proxies and stored as facts.

---

## Decision-023 — Future AI Work Follows the Approved Phase Roadmap

Status: Approved

The binding future roadmap is:

`docs/AI_COMMERCE_OPERATING_SYSTEM_ROADMAP.md`

The roadmap reserves Marketing Attribution, Customer Journey, Voice of Customer,
Conversion Optimization, Creative Intelligence, Content Generation, Campaign
Control and Commerce Executive Agent capabilities.

Reservation does not authorize premature implementation. The active project
gate remains the current Order Engine/Order Item Engine phase until its
acceptance criteria pass.

---

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

## Decision-028 — Customer Intelligence Starts as an Isolated Preview

Status: Approved, Phase 1 implementation

The Customer Intelligence & Sales Center may establish its long-term contracts
and owner-only workspace before live customer-channel ingestion begins, but the
initial implementation must remain an isolated synthetic preview.

The Phase 1 boundary is mandatory:

- The server owns the preview contract; React does not carry a second business
  fixture or source of truth.
- The preview service receives no database handle, customer-channel client,
  commerce connector, payment client, advertising client or AI execution
  client.
- Only an authenticated owner may read the preview.
- No live customer data is used.
- WhatsApp sending, follow-up execution, order creation, discount creation,
  payment-link creation, product mutation, campaign mutation and AI execution
  are contractually fixed to `false`.
- Future live capabilities require separate ingestion, privacy, identity,
  approval, quality and rollback gates. They are not implied by this preview.

---

## Decision-029 — Employee Identity Is Separate from Login and Salary

Status: Approved, Phase 1 implementation

Mezan V2 owns one canonical employee identity. A login account, operational
role, salary contract and financial ledger entity are linked capabilities; none
of them is the employee's identity by itself.

The initial migration is a guarded `shadow_read_only` copy:

- `operating_salaries` remains payroll authority until a validated cutover.
- `general_ledger` remains the authority for salary payable, advances and
  custody; migration never recomputes or rewrites historical balances.
- Existing users and operational role assignments are linked only through an
  explicit identifier. A name match may be shown as a review suggestion but is
  never applied automatically.
- Migration is idempotent, audited and blocks duplicate legacy identities.
- Salary changes become effective-dated contracts after cutover; historical
  contracts are never overwritten.
- The legacy employee page is retired only after one complete payroll cycle,
  fulfillment permission checks and 100% count/value reconciliation pass.
- AI may recommend assignments or flag anomalies. Salary payment, termination,
  sensitive permission changes and payroll cutover require human approval.

---

## Decision-030 — Employee Management Opens Through a Single-Employee Pilot

Status: Superseded by Decision-032 after pilot acceptance

Write-capable Employees V2 management starts with one native pilot employee
before any of the 15 shadow-migrated employee records can be edited.

The pilot boundary is mandatory:

- Only the owner can create, edit, link or assign the pilot employee.
- At most one pilot employee exists per owner.
- Pilot status is limited to `draft` or `inactive`; it cannot become payroll
  active.
- Pilot salary stays in an `employees_v2_pilot_only` contract with payroll,
  legacy salary, liability and general-ledger writes disabled.
- The 15 shadow-migrated employees remain read-only.
- A login account must belong to the owner's team, must not be the owner, and
  must have no employee link or operational role already.
- Accounts suggested for migrated employees remain reserved for manual review;
  this includes the unresolved Arafat suggestion.
- Pilot account linkage is V2-only and does not write the legacy
  `users.linked_employee_id` reverse link.
- Role assignment uses the canonical `mezan_role_assignments_v2` catalogue and
  is limited to the linked pilot account.
- Create, edit, link, unlink and role actions append employee audit events.

Opening writes for the 15 migrated employees requires a separate acceptance
decision after the pilot passes create/edit/link/role/unlink/audit checks and
financial invariants remain unchanged.

---

## Decision-031 — Live Customer Memory Ingestion Is Encrypted and Read-Only

Status: Approved, Abandoned Carts V2 foundation

Mezan may ingest live Salla abandoned-cart and customer identity evidence into
a dedicated tenant-scoped memory before Customer Intelligence exits its
synthetic preview, provided that the ingestion boundary remains read-only and
does not activate any customer or advertising action.

The mandatory boundary is:

- Customer name, email, mobile and address fields are encrypted at rest and
  never copied into analytics snapshots, event audit records, logs or public
  status responses as plaintext.
- Customer lookup aliases are keyed HMAC digests; provider customer IDs are
  provider-scoped, while contact aliases may join future channels to the same
  identity inside the same tenant and merchant only.
- Cart recovery URLs and coupon codes are encrypted. Public cart records expose
  only presence and coverage facts.
- Existing abandoned-cart collections are upgraded in place to schema V2 so
  historical carts are not split or discarded.
- First-touch and last-touch attribution are both preserved. Platform,
  account, campaign, ad-group, ad, creative, click and UTM identifiers are
  stored only when supplied by verified cart/order evidence; missing evidence
  is not inferred.
- A converted cart may recover attribution from its linked authoritative order.
- Customer identity links added to `unified_orders` are additive metadata only.
  They never change order state, totals, payment, shipment, accounting or
  product facts, and an existing identity link is never overwritten silently.
- Historical imports remain idempotent, retry bounded and read-only toward
  Salla. Encryption configuration fails closed rather than storing plaintext.
- The Customer Intelligence UI remains synthetic under Decision-028. Live PII
  decryption, messaging, discounts, order creation, campaign mutation and AI
  execution require later owner-only APIs, approvals, audit and rollback gates.

---

## Decision-032 — Employee Management Is Open While Payroll Remains Read-Only

Status: Approved, Employee OS management closeout

The single-employee pilot in Decision-030 has passed its management and audit
checks. Owner-managed identity and access writes are now open for all canonical
Employee V2 records, including the previously shadow-migrated employees.

The approved boundary is:

- The owner may create or edit employees, activate or deactivate them, link or
  unlink a non-owner team login, reset its password and assign an operational
  role.
- Deactivation and unlinking revoke current and future authenticated access
  immediately. Reactivation restores only the account and role state that was
  explicitly preserved for that employee.
- The preparation employee role remains limited to assigned-work read and work
  permissions. It does not inherit owner, financial or broad operational access.
- Every employee, account, password and role action appends an actor-stamped
  event with before/after state. Password plaintext is never recorded.
- `operating_salaries`, liabilities, advances, custody and `general_ledger`
  remain authoritative and read-only from Employee OS. Employee management
  creates no salary contract and makes zero financial writes.
- Payroll cutover, salary editing and retirement of the legacy employee page
  still require the reconciliation and parallel-cycle gates in Decision-029.

---

## Decision-033 — Customer Conversations Use One Channel-Neutral Mezan Core

Status: Approved, persistence foundation only

Mezan owns the customer and conversation memory. WhatsApp, Instagram and
TikTok are transport adapters around that core; no channel platform or external
AI product becomes the customer source of truth.

The persistence foundation consists of five logical entities:

- `customers` is the non-PII profile and routing record.
- `customer_identities` is the existing encrypted
  `mezan_customer_identities_v1` vault. It is reused, not copied or replaced.
- `channels` identifies a tenant's provider account without storing provider
  credentials or customer contact details in plaintext.
- `conversations` links one provider thread to one canonical Mezan customer.
- `conversation_messages` stores immutable channel evidence with encrypted
  content and provider idempotency keys.

Customer, conversation and message identity and lookup indexes are scoped by
the authenticated Mezan tenant and merchant. Provider account, conversation
and message references are non-reversible keys rather than raw phone numbers,
handles or external IDs. A signed provider webhook is resolved through one
global, unique provider-account HMAC; that binding contains no raw provider
identifier and is used only to select the tenant before normal scoped access.

This decision creates models and Mongo indexes only. It adds no channel
webhook, provider client, GPT call, mutation endpoint or send worker. Outbound
messaging, automatic replies, order creation, discounts, payment links and
product mutations remain disabled. A later Channel Gateway decision must
preserve this core and introduce inbound adapters before any egress capability.

---

## Decision-034 — WhatsApp Is the First Receive-Only Channel Adapter

Status: Approved, disabled-by-default ingress implementation

WhatsApp Cloud API is the first real channel connected to the shared Channel
Gateway. The implementation accepts only Meta webhook verification and signed
inbound message notifications.

The boundary is mandatory:

- Meta's GET challenge must match a backend-only verify token.
- Every POST must carry a valid `X-Hub-Signature-256` computed over the exact
  raw request body with the backend-only Meta App Secret.
- A verified `phone_number_id` resolves through a non-reversible binding to
  exactly one tenant channel. It is never accepted as `user_id` from the
  webhook body.
- Text and supported media/interactive evidence is normalized through the
  shared Channel Gateway. Raw webhook bodies are not persisted.
- Message text, contact identity and media references are encrypted at rest;
  message IDs are idempotent HMAC keys.
- Status-only and unsupported events never become fabricated customer
  messages.
- The adapter has no WhatsApp access token or send client. The router exposes
  only GET/POST on `/channels/whatsapp/webhook`; no send operation exists.
- GPT execution, auto-reply, employee reply, orders, discounts, payment links
  and product mutations remain disabled and absent from this adapter.

Production ingress stays off until the owner-approved channel binding, App
Secret, verify token, HMAC key and PII encryption key are installed in the
backend deployment environment.
