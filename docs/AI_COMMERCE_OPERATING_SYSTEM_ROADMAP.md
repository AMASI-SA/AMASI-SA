# Mezan OS — AI Commerce Operating System Roadmap

Status: Approved
Architecture gate: Binding future roadmap
Approved objective: Build one long-lived AI-native commerce intelligence and execution system that can improve sustainable net profit, customer experience and operational quality with progressively less human intervention, while preserving deterministic business correctness.

## 1. North Star

Mezan OS must evolve from an order and accounting platform into an **AI-native commerce operating system**.

AI-native does not mean AI-dependent. Orders, accounting, cost, tax, inventory,
permissions, idempotency and other correctness-critical rules remain deterministic
and authoritative. AI consumes trusted facts, reasons above them and requests
bounded actions through governed execution interfaces.

The long-term system must be able to:

- Observe store, customer, advertising, financial and operational activity.
- Detect anomalies, conversion problems and commercial opportunities.
- Explain findings using traceable evidence.
- Request missing inputs from the owner or team.
- Recommend actions.
- Execute approved actions through connected tools.
- Measure the result.
- Learn from outcomes and prior decisions.
- Understand natural-language shopping intent and connect it to accurate product knowledge.
- Improve Amasi so its products are increasingly understandable, discoverable and recommendable through AI-assisted commerce channels.

The primary optimization target is sustainable net profit, not revenue or ROAS alone.

A core growth target is **AI Commerce Discovery**: Mezan must prepare Amasi for a commerce environment in which shoppers increasingly express product needs to AI assistants in natural language. This is not an SEO side project. Product knowledge, intent understanding, recommendation evidence, attribution and learning from AI-assisted journeys are part of the core intelligence architecture.

The binding decision for this target is:

`docs/adr/ADR-003-ai-commerce-discovery.md`

### 1.1 Binding AI-Native Operating Model

The target architecture is:

`External Sources → Raw Facts → Canonical Domain → Deterministic Business/Financial Rules → Intelligence Features → Specialized AI Agents → Mezan Action Gateway → Approved Connectors/Workflows → Outcome Measurement`

The project remains one product. Existing validated work is not discarded or
duplicated for AI; it becomes the factual, operational and execution foundation
that the intelligence layer can use.

The intended agent topology may include:

- Mezan Supervisor.
- Profit Agent.
- Ads Agent.
- Customer Agent.
- Product Agent.
- Orders Agent.
- Accounting Agent.
- Growth Agent.
- Risk Agent.

Agents do not own business truth. All AI-initiated writes must pass through the
Mezan Action Gateway with permission, risk, approval, idempotency, audit,
execution and rollback controls.

The binding architectural decision is:

`docs/adr/ADR-002-ai-native-operating-model.md`

### 1.2 AI Commerce Discovery Loop

The long-term discovery loop is:

`Natural-language need → structured shopper intent → trusted product knowledge → evidence-ranked products → product/session/order → order-item profit → experiment/business memory → improved product knowledge`

The same Product Knowledge Layer should support onsite discovery, product-page improvement, content creation, customer service, advertising, future external AI/search/shopping channels and demand-gap analysis.

## 2. Ten-Year Data Principle

Mezan must preserve data that may remain useful for analysis and model training for at least the next ten years, subject to privacy, legal and retention requirements.

Every integration must preserve:

1. Raw provider payloads.
2. Canonical normalized facts.
3. Source identity and timestamps.
4. Versioned transformation metadata.
5. Data-quality and confidence indicators.
6. Relationships between customer, session, campaign, creative, order and order item.
7. Product knowledge provenance and, where legitimately available, AI/search discovery source and intent/query evidence.

Derived metrics must never replace the raw facts required to recompute them later.

## 3. Source Ownership

### Salla

Owns commerce facts:

- Orders and order items.
- Customer and checkout facts supplied by Salla.
- Order status, payment and shipping facts.
- Product-page attribution and UTM values attached to the order.
- Published product facts exposed through the approved Salla integration.

### Advertising platforms

Snapchat, TikTok, Meta and Google Ads own:

- Campaign, ad set/ad group and ad identities.
- Creative identities.
- Spend, impressions, clicks and platform conversions.
- Budgets, bidding, targeting and delivery state.

### Analytics and behavior tools

GA4 and approved session-behavior tools own:

- Sessions and traffic acquisition.
- Page and product interactions.
- Funnel events.
- Abandonment paths.
- Site-search behavior.
- Performance and behavioral signals.

### AI/search/shopping discovery channels

When supported and legitimately available, external AI assistants, answer engines, search engines and shopping discovery channels may provide:

- Referral/source identity.
- Query or intent evidence.
- Product/citation/referral identifiers.
- Click/session metadata.
- Channel-specific visibility or performance signals.

Mezan must preserve uncertainty and must not invent attribution when a channel does not expose enough evidence.

### Mezan

Owns:

- Canonical cross-source identity resolution.
- True order profitability.
- Operational and accounting outcomes.
- Decision history.
- Experiment history.
- AI recommendations, approvals, executions and measured outcomes.
- Structured Product Knowledge Layer built from authoritative facts plus explicitly labeled inferences.
- Structured shopper-intent objects and intent-to-product matching logic.
- Cross-source AI/search discovery measurement and confidence.

### Customer conversations

WhatsApp, email, live chat and approved social channels provide the voice-of-customer record:

- Questions and objections.
- Purchase intent.
- Reasons for not purchasing.
- Product, delivery, payment and trust problems.
- Complaint themes and sentiment.
- Support response and conversion outcome.
- Natural product language that may reveal taxonomy, FAQ and discovery gaps when used under privacy controls.

## 4. Required Data Layers

### Layer A — Immutable Raw Facts

Store provider payloads without destructive normalization:

- Salla raw.
- Advertising-platform raw.
- GA4 raw.
- Session-behavior raw references and extracted events.
- Qoyod raw.
- Payment-provider raw.
- Shipping-provider raw.
- Customer-conversation raw messages and metadata.
- AI/search/shopping referral or query evidence where a source provides it.

Raw data must be append-only or version-preserved wherever practical.

### Layer B — Canonical Commerce Model

Canonical entities include:

- Customer.
- Session.
- Visit and funnel event.
- Campaign.
- Ad group/ad set.
- Ad.
- Creative.
- Product and variant.
- Product knowledge attribute with provenance.
- Shopper intent/query object.
- Intent constraint.
- Product recommendation/match evidence.
- Order and order item.
- Payment.
- Shipment.
- Conversation.
- Complaint and customer-intent signal.
- Experiment.
- Decision and execution.

### Layer C — Intelligence Features

Examples:

- Net-profit contribution by campaign, creative, product and customer cohort.
- Conversion rate and funnel loss.
- Return, cancellation and support-contact probability.
- Repeat-purchase probability and customer lifetime value.
- Creative fatigue.
- Audience saturation.
- Product-page friction.
- Checkout friction.
- Voice-of-customer themes.
- Product-knowledge completeness and confidence.
- Intent-to-product match coverage.
- AI/search discovery gaps.
- Products repeatedly requested but poorly represented.
- Products frequently recommended but not converting.
- Anomaly and incident signals.

### Layer D — Decisions and Actions

Every recommendation or automated action must record:

- Observation.
- Evidence and source data.
- Confidence.
- Expected benefit and risk.
- Proposed action.
- Required approval level.
- User approval, rejection or modification.
- Execution details.
- Outcome window.
- Actual result.
- Rollback status when applicable.

Durable business memory must be stored as structured, versioned and traceable
system data. Conversational memory alone must never be treated as the business
system of record.

## 5. Future Engines

### Marketing Attribution Engine

Connects:

Campaign → Ad group/ad set → Ad → Creative → Session → Order → Order Item → Profit

It must support multiple attribution views rather than pretending one model is universally correct.

It must also be able to preserve AI/search-assisted journey evidence when available without falsely forcing every order into a single-source model.

### Customer Journey Engine

Reconstructs customer sessions, product views, cart events, checkout progress and abandonment.

### Voice of Customer Engine

Analyzes customer conversations and links extracted issues to products, sessions, campaigns, orders and outcomes.

### Conversion Optimization Engine

Finds store and product-page problems, proposes experiments and measures conversion impact.

### AI Commerce Discovery & Product Knowledge Engine

Builds and governs the shared product-intelligence layer used to improve Amasi and support natural-language product discovery.

It must progressively support:

- Verified product and variant knowledge with provenance.
- Semantic attributes separated from authoritative facts.
- Natural-language shopper intent extraction.
- Constraint-aware candidate generation and ranking.
- Evidence-backed product recommendations.
- Product FAQ and comparison knowledge.
- Product-knowledge completeness diagnostics.
- AI/search/shopping discovery source measurement where available.
- Intent/query coverage gaps and unmet-demand signals.
- Links from discovery source/intent → product → session → order item → profit.

The engine must not fabricate product attributes or treat external AI-platform output as authoritative product truth.

### Creative Intelligence Engine

Tracks creative concepts, assets, copy, hooks, formats, audiences and measured results.

### Content Generation Engine

Creates draft:

- Product images.
- Advertising images.
- Product and campaign videos.
- Product titles and descriptions.
- Product FAQs and machine-readable discovery content based on verified facts.
- Advertising copy.
- WhatsApp, email and SMS content.

Generated content must remain reviewable and versioned before autonomous publishing is permitted.

### Campaign Control Engine

Can propose and later execute:

- Budget increases or decreases.
- Campaign, ad-set or ad pauses.
- Targeting changes.
- New experiments.
- New campaign creation.

### Commerce Executive Agent / Mezan Supervisor

Combines marketing, customer, store, product, operational and financial evidence
to prioritize actions by expected sustainable net-profit impact. It coordinates
specialized agents but does not bypass their domain controls, the canonical data
layer or the Action Gateway.

## 6. Autonomy Levels

Automation must progress through explicit levels:

1. Observe only.
2. Explain and recommend.
3. Prepare a draft action for approval.
4. Execute low-risk approved actions within limits.
5. Execute bounded autonomous experiments.
6. Broader autonomy only after measured reliability and rollback controls.

No engine may jump directly to uncontrolled autonomous spending or production publishing.

Mandatory controls include:

- Per-platform and total budget limits.
- Daily loss and spend guards.
- Approval thresholds.
- Action allowlists.
- Cooldown periods.
- Rollback procedures.
- Complete audit logs.
- Emergency stop.
- Deterministic safe-degradation when AI is unavailable or below confidence.

## 7. Project Phases and Gates

### Phase 1 — Commerce Foundation (Current)

- Order Engine.
- Order Item Engine.
- Reliable Salla Direct synchronization.
- Correct order list and details.
- Qoyod integration stability.

Gate: No later operational or intelligence engine starts until order facts are reliable and production-validated.

### Phase 2 — Operational Commerce

- Product Definition.
- Components and manufactured-product definitions.
- Availability and operational inventory.
- Preparation, purchase, supplier and receiving workflows.
- Shipping readiness.
- Preserve rich product/variant facts and provenance required by the future Product Knowledge Layer.

Gate: Stable order-item identity and non-duplicating workflows.

### Phase 3 — Financial Truth

- Unified profitability per order and order item.
- Payment, BNPL, shipping, refund and product-cost reconciliation.
- Accounting and cash-flow reliability.

Gate: Financial metrics are explainable and reconcile to sources.

### Phase 4 — Marketing Data Foundation

- Raw connectors for Snapchat, TikTok, Meta and Google Ads.
- GA4 and Search Console.
- Campaign, ad and creative canonical models.
- Identity and UTM preservation from Salla.
- Data-quality monitoring.
- Reserve compatible source/referral fields for future AI/search/shopping discovery evidence.

Gate: Spend and delivery facts reconcile to each platform and link to orders with documented confidence.

### Phase 5 — Customer Journey and Voice of Customer

- Session and funnel event ingestion.
- Behavioral analytics integration.
- WhatsApp and support-channel ingestion.
- Intent, objection, issue and sentiment extraction.
- Customer/order/product/campaign linking.
- Preserve legitimate natural-language product requests as structured intent candidates with privacy controls.

Gate: Privacy controls, reliable identity resolution and measurable extraction quality.

### Phase 6 — Intelligence and Experimentation

- Attribution models.
- Profit-aware campaign analysis.
- Conversion diagnostics.
- Product-page recommendations.
- Creative and audience analysis.
- Product-knowledge completeness scoring.
- Natural-language intent-to-product matching experiments.
- AI/search discovery measurement where technically available.
- Experiment registry and outcome measurement.

Gate: Recommendations must include evidence, confidence and measurable acceptance criteria.

### Phase 7 — Content Copilot

- Draft product copy, images and videos.
- Draft product FAQs, semantic attributes and structured discovery content from verified facts.
- Creative variants and campaign briefs.
- Human review, versioning and asset-performance tracking.

Gate: Brand, legal, platform and factual-quality checks.

### Phase 8 — Campaign Copilot

- Draft campaign structures and changes.
- Budget recommendations.
- Pause, scale and targeting proposals.
- Owner/team approval workflow.
- Route every executable change through the Action Gateway.

Gate: Simulation, limits, auditability and rollback.

### Phase 9 — Bounded Autonomous Growth

- Automatic low-risk experiments.
- Budget adjustment within approved envelopes.
- Automatic pause on validated loss/anomaly conditions.
- Continuous learning from decision outcomes.
- Bounded discovery/content experiments only after product-fact and publishing controls are proven.

Gate: Proven reliability over an agreed observation period, with no bypass of financial and safety controls.

## 8. Non-Negotiable Engineering Rules

- Do not discard raw provider fields merely because the current UI does not use them.
- Do not let frontend pages become data owners.
- Do not mix provider facts with AI inference without labeling confidence and provenance.
- Do not infer sensitive customer attributes without an explicit, legitimate source and approved purpose.
- Do not fabricate product facts, materials, dimensions, stones, warranties, availability or suitability to improve AI/search visibility.
- Product knowledge must preserve source, version and confidence where applicable.
- Preserve original shopper-language evidence separately from extracted intent when legitimately collected.
- Do not train or evaluate models on silently changed schemas.
- Version canonical schemas and transformations.
- Preserve event time, ingestion time and source time separately.
- Every action-capable agent requires permissions, limits, audit logs and rollback.
- No AI model or agent may write directly to providers or production data; executable AI actions must use the Action Gateway.
- Correct deterministic modules are reused as the business foundation rather than duplicated in a parallel AI stack.
- AI unavailability must degrade to deterministic operation and explicit human control.
- Customer data access must follow least privilege, retention and privacy controls.

## 9. Current Scope Control

This roadmap is binding but does not authorize premature implementation.

The AI-native operating model and AI Commerce Discovery target are project-wide architectural goals, not requirements to pause current delivery or rewrite existing validated work.
Current modules continue to be completed and production-validated in their active workstreams, while new work preserves the interfaces, product facts, provenance and evidence needed for future intelligence, discovery and action layers.

The current implementation gate remains Order Engine and Order Item Engine completion. Marketing, customer-conversation, content-generation, AI-commerce-discovery execution and autonomous campaign work must wait for their defined phases and acceptance gates unless a later approved project decision explicitly changes the active gate.
