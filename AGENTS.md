# Mezan OS — Persistent Agent Context

This file is the first-read project contract for any AI coding agent, assistant, or new development conversation working in this repository.

## Project Goal

Mezan OS is one long-lived **AI-native commerce operating system**. It is not a separate AI project and it is not a rewrite of the current product.

The product must evolve so that trusted commerce, operational, accounting, advertising, customer and product data can be observed by specialized AI agents, converted into evidence-backed decisions, and later executed through bounded, governed actions.

The primary business optimization target is **sustainable net profit**, while preserving accounting correctness, customer experience, operational quality, privacy, security and owner-defined constraints.

A core growth objective is also to make Amasi products understandable, discoverable and recommendable through natural-language AI commerce. Mezan must be designed for a future in which a shopper can express a need to an AI assistant in ordinary language and the system can represent that intent, match it to accurate Amasi product knowledge, measure the resulting journey and use the outcome to improve Amasi.

## Parallel Development Agreement

Development is intentionally **parallel**:

1. Continue completing and stabilizing the current deterministic Mezan workstreams: Salla, orders, order items, products, components/services, suppliers, receiving, preparation, costs, accounting, Qoyod, dashboards, Snapchat, Meta, TikTok, Google/GA4, customer service and integrations.
2. At the same time, shape those workstreams so they become trusted inputs, deterministic tools and execution connectors for future AI agents.
3. Preserve rich product/variant facts, customer-language signals, provenance and attribution evidence needed for future AI shopping discovery.
4. Do not stop current delivery to perform a wholesale AI rewrite.
5. Do not create a second duplicated "AI Mezan" stack beside the real system.
6. Do not prematurely add autonomous writes merely because a model can perform them.

A workstream may advance independently when it is safe, but its data contracts, provenance, permissions, auditability and source-of-truth rules must remain compatible with the AI-native target architecture.

## Binding Architecture

Read these before architecture or cross-cutting changes:

1. `docs/adr/ADR-001-architecture-principles.md`
2. `docs/adr/ADR-002-ai-native-operating-model.md`
3. `docs/adr/ADR-003-ai-commerce-discovery.md`
4. `docs/PROJECT_DECISIONS.md`
5. `docs/AI_COMMERCE_OPERATING_SYSTEM_ROADMAP.md`

These documents are binding project decisions, not optional background notes.

For product, search, content, recommendation or AI-shopping work, also read:

- `docs/PRODUCT_CONTROL_CENTER_AI_ARCHITECTURE.md`

## Core Rule: AI-Native, Not AI-Dependent

Correctness-critical business behavior remains deterministic and authoritative, including:

- Orders and order-item identity/state.
- Product cost, tax, accounting and reconciliation calculations.
- Inventory, supplier and receiving state.
- Permissions and tenant isolation.
- Idempotency and duplicate prevention.
- Provider normalization and canonical models.
- Hard budget/loss/risk constraints.

AI reasons **above** trusted facts. It may analyze, explain, prioritize, predict, recommend and request actions. It does not become the source of truth for deterministic business facts.

If AI is unavailable or below confidence, Mezan must continue operating safely through deterministic code and explicit human controls.

## Target Flow

`External Sources → Raw Facts → Canonical Domain → Deterministic Business/Financial Rules → Intelligence Features → Specialized AI Agents → Mezan Action Gateway → Approved Connectors/Workflows → Outcome Measurement`

## AI Commerce Discovery Core Loop

Mezan must preserve the ability to support this long-term loop:

`Natural-language customer need → structured intent/constraints → trusted product knowledge → evidence-ranked Amasi products → session/order/order item → net-profit outcome → experiment/business memory → improved product knowledge`

AI-shopping readiness is broader than SEO. Product facts, taxonomy, attributes, natural-language questions/answers, media, availability, pricing, product performance and legitimate customer-language signals must remain usable by future conversational discovery systems.

Never improve AI discoverability by fabricating product claims, hiding provenance or mixing inferred attributes with authoritative facts.

## Target Agent Topology

The long-term system may include:

- Mezan Supervisor.
- Profit Agent.
- Ads Agent.
- Customer Agent.
- Product Agent.
- Orders Agent.
- Accounting Agent.
- Growth Agent.
- Risk Agent.

Agents do not own business truth. They consume canonical facts and produce traceable analyses, recommendations or bounded action requests.

For AI commerce discovery:

- Product Agent monitors product-knowledge completeness and semantic readiness.
- Customer Agent contributes legitimate Voice-of-Customer language and intent signals.
- Growth Agent identifies discovery gaps and profitable demand opportunities.
- Supervisor coordinates cross-domain priorities without bypassing source-of-truth or Action Gateway controls.

## Mandatory Action Gateway

No AI model or agent may write directly to production databases or external providers such as Snapchat, Meta, TikTok, Google Ads, Salla, Qoyod or customer channels.

Every AI-initiated write must eventually pass through the **Mezan Action Gateway**, enforcing at minimum:

- Actor identity and authentication.
- Tenant isolation and permissions.
- Action allowlists.
- Risk and confidence checks.
- Approval level.
- Budget/loss/cooldown guards where applicable.
- Idempotency and duplicate prevention.
- Deterministic connector/workflow execution.
- Immutable audit logs.
- Outcome measurement.
- Rollback state and emergency stop where applicable.

## Progressive Autonomy

Autonomy advances only through explicit gates:

1. Observe.
2. Analyze and explain.
3. Recommend.
4. Prepare an action for approval.
5. Execute approved low-risk actions within limits.
6. Run bounded autonomous experiments after measured reliability.

Never skip evidence, permission, approval, audit or rollback gates.

## Business Memory

Durable Mezan business memory must be structured, versioned and traceable system data. Do not rely on chat memory as the business system of record.

Examples include decision history, experiment results, campaign/creative performance, product profitability, supplier/operational patterns, legitimate customer-intent signals, AI/search discovery outcomes, owner approvals/rejections and measured outcomes.

## How to Treat Existing Code

Existing validated work is valuable infrastructure for the AI-native architecture. Classify capabilities as:

- `REUSE`
- `REFACTOR`
- `REPLACE`
- `DELETE`

Do not rewrite a working deterministic module merely to make it look AI-native. Reuse it as a trusted service or connector when correct.

## New Conversation / New Agent Startup Checklist

Before proposing major work:

1. Read this `AGENTS.md`.
2. Read ADR-001, ADR-002 and ADR-003.
3. Read relevant entries in `docs/PROJECT_DECISIONS.md`.
4. Check `docs/AI_COMMERCE_OPERATING_SYSTEM_ROADMAP.md` for the current phase/gates.
5. Inspect the current implementation and recent PRs for the workstream being changed.
6. Preserve ongoing parallel work and avoid collisions with other branches/workstreams.
7. Prefer additive changes, feature flags and backward-compatible contracts.
8. Keep AI-readiness and AI-commerce-discovery readiness in scope, but do not let future AI work block urgent deterministic fixes.
9. For product-facing work, preserve the facts and provenance required to answer natural-language shopper needs without inventing attributes.

## Non-Negotiable Interpretation

When a new conversation enters this repository, the default interpretation must be:

> We are continuing the same Mezan OS project. Current operational/product/accounting/advertising work and AI development proceed in parallel toward one AI-native system. The current system is the foundation for the AI; it is not throwaway work and it is not a separate path. A core AI goal is to make Amasi increasingly understandable, discoverable and recommendable to natural-language shopping assistants while measuring the resulting business impact.
