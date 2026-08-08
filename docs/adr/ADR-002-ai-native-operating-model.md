# ADR-002 — Mezan AI-Native Operating Model

**Status:** Accepted (2026-08-08)
**Scope:** All future Mezan OS architecture, including intelligence, agents, automation, integrations, dashboards, operational workflows and cross-cutting refactors.
**Depends on:** ADR-001 — Mezan Architecture Principles.

---

## Context

Mezan already contains valuable deterministic commerce, accounting, operational,
product, supplier, customer and advertising integrations. The project must become
progressively more AI-driven without discarding that work, creating a parallel
product, or making business correctness depend on a language model.

The target is one long-lived Mezan OS that can observe the business, reason over
trusted facts, recommend actions, execute bounded approved actions, measure
outcomes and improve future decisions.

---

## Decision

### 1. One Product, Additive Evolution

Mezan AI is not a separate project and does not trigger a full rewrite.

Existing validated capabilities remain in place and are progressively classified
as `REUSE`, `REFACTOR`, `REPLACE` or `DELETE` under the existing project rules.
New intelligence and action layers are added around the current system without
breaking production behavior.

### 2. Deterministic Core Remains Authoritative

Business facts and correctness-critical operations remain deterministic and
code-governed. Examples include:

- Order and order-item identity and state transitions.
- Product cost, tax, accounting and reconciliation calculations.
- Inventory, supplier and receiving state.
- Idempotency, permissions and access control.
- Provider payload normalization and canonical models.
- Budget limits, hard safety rules and rollback contracts.

AI may explain, prioritize, predict and propose decisions using these facts, but
must not become the source of truth for them.

### 3. AI Sits Above the Canonical and Financial Truth Layers

The intended flow is:

`External Sources → Raw Facts → Canonical Domain → Deterministic Business/Financial Rules → Intelligence Features → Specialized AI Agents → Action Gateway → Approved Connectors/Workflows → Outcome Measurement`

Dashboard and operational pages are views and workspaces over this architecture;
they are not competing data owners.

### 4. Specialized Agents with a Supervisory Layer

Mezan may use specialized agents with explicit domain boundaries. The target
roles are:

- **Mezan Supervisor** — coordinates priorities and cross-domain decisions.
- **Profit Agent** — evaluates true contribution and net-profit impact.
- **Ads Agent** — analyzes and later controls approved advertising actions.
- **Customer Agent** — supports customer service, follow-up and retention.
- **Product Agent** — analyzes product, cost, demand, content and inventory facts.
- **Orders Agent** — analyzes operational order flow and exceptions.
- **Accounting Agent** — explains accounting and reconciliation state while
  preserving deterministic accounting rules.
- **Growth Agent** — identifies experiments and commercial opportunities.
- **Risk Agent** — enforces risk, confidence, approval and loss boundaries.

An agent does not own the underlying business facts. It consumes canonical facts
and produces evidence-backed recommendations or action requests.

### 5. Action Gateway Is Mandatory for AI-Initiated Writes

No model or agent may write directly to Snapchat, Meta, TikTok, Google Ads,
Salla, Qoyod, production data, customer channels or another external system.

Every AI-initiated write must pass through a **Mezan Action Gateway** that applies:

1. Authentication and actor identity.
2. Permission and tenant checks.
3. Action allowlist validation.
4. Risk and confidence checks.
5. Approval-level enforcement.
6. Budget/loss/cooldown guards where applicable.
7. Idempotency and duplicate prevention.
8. Execution through an approved deterministic connector or workflow.
9. Immutable audit logging.
10. Outcome measurement and rollback state.

Direct model-to-provider or model-to-database mutation is prohibited.

### 6. Autonomy Is Progressive

AI capabilities progress through explicit stages:

1. Observe.
2. Analyze and explain.
3. Recommend.
4. Prepare an action for approval.
5. Execute approved low-risk actions within limits.
6. Run bounded autonomous experiments only after measured reliability.

No capability may skip the evidence, permission, audit and rollback gates merely
because an AI model is technically able to perform the action.

### 7. Business Memory Is Structured System Data

Mezan must retain durable business memory as structured, versioned and
traceable data rather than relying on conversational memory alone.

Examples include:

- Prior decisions and their measured outcomes.
- Experiment history.
- Campaign and creative performance history.
- Product profitability and operational behavior.
- Customer history and legitimate customer-intent signals.
- Repeated supplier or workflow issues.
- Accepted/rejected recommendations and owner overrides.

Business memory must preserve provenance, time, version and confidence where
applicable. It informs agents but never silently replaces authoritative facts.

### 8. Primary Optimization Objective

The primary commercial objective is **sustainable net profit**, while respecting
customer experience, operational quality, accounting correctness, privacy and
explicit owner-defined constraints.

Revenue, ROAS, conversion rate and platform-reported results are important
signals, not the final objective by themselves.

### 9. Existing Work Becomes the Foundation for AI

Current and future work on Salla, advertising platforms, GA4, products, costs,
orders, suppliers, receiving, customer service, accounting, dashboards and the
integration control center remains part of the same architecture.

The work is not duplicated for AI. Instead, validated modules become trusted
inputs, deterministic tools or execution connectors that agents can use through
approved interfaces.

---

## Consequences

- There is no separate "old Mezan" and "AI Mezan" product line.
- Existing production logic is not rewritten merely to make it look AI-native.
- Correct deterministic infrastructure has long-term value because it becomes
  the factual and execution substrate for agents.
- Any new action-capable AI feature must budget engineering work for permissions,
  audit, rollback and outcome measurement, not only prompting/model calls.
- AI failures must degrade safely to deterministic operation rather than making
  core commerce or accounting unavailable.

---

## Non-Goals

This ADR does **not** authorize:

- Uncontrolled autonomous advertising spend.
- Direct AI writes to production databases or providers.
- Replacing accounting, tax, cost or inventory truth with model inference.
- Rebuilding validated modules without a measured reason.
- Treating chat history as the business system of record.

---

## Compliance with ADR-001

This decision extends ADR-001 and specifically reinforces:

- Principle 1 — Additive Architecture.
- Principle 4 — Canonical Domain.
- Principle 6 — Layered Architecture.
- Principle 7 — Backward Compatibility.
- Principle 8 — Event Driven and Auditable.
- Principle 9 — Single Source of Truth.
- Principle 10 — Idempotency by Design.
- Principle 11 — Multi-Tenant Isolation.
- Principle 12 — Reversibility.
- Principle 14 — Secrets Discipline.

Any implementation that conflicts with ADR-001 or this ADR requires a new
explicit architectural decision before merge.
