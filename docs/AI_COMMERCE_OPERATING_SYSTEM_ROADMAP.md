# Mezan OS — AI Commerce Operating System Roadmap

Status: Approved
Architecture gate: Binding future roadmap
Approved objective: Build a long-lived commerce intelligence and execution system that can improve net profit, customer experience and operational quality with limited human intervention.

## 1. North Star

Mezan OS must evolve from an order and accounting platform into an AI-assisted commerce operating system.

The long-term system must be able to:

- Observe store, customer, advertising, financial and operational activity.
- Detect anomalies, conversion problems and commercial opportunities.
- Explain findings using traceable evidence.
- Request missing inputs from the owner or team.
- Recommend actions.
- Execute approved actions through connected tools.
- Measure the result.
- Learn from outcomes and prior decisions.

The primary optimization target is sustainable net profit, not revenue or ROAS alone.

## 2. Ten-Year Data Principle

Mezan must preserve data that may remain useful for analysis and model training for at least the next ten years, subject to privacy, legal and retention requirements.

Every integration must preserve:

1. Raw provider payloads.
2. Canonical normalized facts.
3. Source identity and timestamps.
4. Versioned transformation metadata.
5. Data-quality and confidence indicators.
6. Relationships between customer, session, campaign, creative, order and order item.

Derived metrics must never replace the raw facts required to recompute them later.

## 3. Source Ownership

### Salla

Owns commerce facts:

- Orders and order items.
- Customer and checkout facts supplied by Salla.
- Order status, payment and shipping facts.
- Product-page attribution and UTM values attached to the order.

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

### Mezan

Owns:

- Canonical cross-source identity resolution.
- True order profitability.
- Operational and accounting outcomes.
- Decision history.
- Experiment history.
- AI recommendations, approvals, executions and measured outcomes.

### Customer conversations

WhatsApp, email, live chat and approved social channels provide the voice-of-customer record:

- Questions and objections.
- Purchase intent.
- Reasons for not purchasing.
- Product, delivery, payment and trust problems.
- Complaint themes and sentiment.
- Support response and conversion outcome.

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

## 5. Future Engines

### Marketing Attribution Engine

Connects:

Campaign → Ad group/ad set → Ad → Creative → Session → Order → Order Item → Profit

It must support multiple attribution views rather than pretending one model is universally correct.

### Customer Journey Engine

Reconstructs customer sessions, product views, cart events, checkout progress and abandonment.

### Voice of Customer Engine

Analyzes customer conversations and links extracted issues to products, sessions, campaigns, orders and outcomes.

### Conversion Optimization Engine

Finds store and product-page problems, proposes experiments and measures conversion impact.

### Creative Intelligence Engine

Tracks creative concepts, assets, copy, hooks, formats, audiences and measured results.

### Content Generation Engine

Creates draft:

- Product images.
- Advertising images.
- Product and campaign videos.
- Product titles and descriptions.
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

### Commerce Executive Agent

Combines marketing, customer, store, product, operational and financial evidence to prioritize actions by expected net-profit impact.

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

Gate: Spend and delivery facts reconcile to each platform and link to orders with documented confidence.

### Phase 5 — Customer Journey and Voice of Customer

- Session and funnel event ingestion.
- Behavioral analytics integration.
- WhatsApp and support-channel ingestion.
- Intent, objection, issue and sentiment extraction.
- Customer/order/product/campaign linking.

Gate: Privacy controls, reliable identity resolution and measurable extraction quality.

### Phase 6 — Intelligence and Experimentation

- Attribution models.
- Profit-aware campaign analysis.
- Conversion diagnostics.
- Product-page recommendations.
- Creative and audience analysis.
- Experiment registry and outcome measurement.

Gate: Recommendations must include evidence, confidence and measurable acceptance criteria.

### Phase 7 — Content Copilot

- Draft product copy, images and videos.
- Creative variants and campaign briefs.
- Human review, versioning and asset-performance tracking.

Gate: Brand, legal, platform and factual-quality checks.

### Phase 8 — Campaign Copilot

- Draft campaign structures and changes.
- Budget recommendations.
- Pause, scale and targeting proposals.
- Owner/team approval workflow.

Gate: Simulation, limits, auditability and rollback.

### Phase 9 — Bounded Autonomous Growth

- Automatic low-risk experiments.
- Budget adjustment within approved envelopes.
- Automatic pause on validated loss/anomaly conditions.
- Continuous learning from decision outcomes.

Gate: Proven reliability over an agreed observation period, with no bypass of financial and safety controls.

## 8. Non-Negotiable Engineering Rules

- Do not discard raw provider fields merely because the current UI does not use them.
- Do not let frontend pages become data owners.
- Do not mix provider facts with AI inference without labeling confidence and provenance.
- Do not infer sensitive customer attributes without an explicit, legitimate source and approved purpose.
- Do not train or evaluate models on silently changed schemas.
- Version canonical schemas and transformations.
- Preserve event time, ingestion time and source time separately.
- Every action-capable agent requires permissions, limits, audit logs and rollback.
- Customer data access must follow least privilege, retention and privacy controls.

## 9. Current Scope Control

This roadmap is binding but does not authorize premature implementation.

The current implementation gate remains Order Engine and Order Item Engine completion. Marketing, customer-conversation, content-generation and autonomous campaign work must wait for their defined phases and acceptance gates.


## 10. Explicit Growth Intelligence Objectives

The following objectives refine existing engines without bypassing the phase gates above. Their detailed integration contract is documented in [MEZAN_GROWTH_INTELLIGENCE_EXTENSION.md](MEZAN_GROWTH_INTELLIGENCE_EXTENSION.md).

### Lifecycle and Retention Intelligence

Mezan must progress from calculating repeat-purchase probability and customer lifetime value to operating measurable lifecycle programs. It must:

- Detect useful, consented lifecycle moments such as first-purchase follow-up, replenishment, win-back and high-value-customer recovery.
- Recommend the next best product, offer, channel and contact time using profit, stock, delivery capacity and customer history.
- Measure incremental repeat orders, contribution profit, opt-outs and complaint impact against control groups.
- Apply frequency caps, consent rules and channel-specific policy before any message is prepared or sent.

This extends the Customer Journey, Voice of Customer and Commerce Executive engines; it is not a separate source of customer truth.

### Service-to-Revenue Intelligence

Mezan must treat pre-sale and post-sale conversations as both service evidence and a controlled growth signal. It must:

- Detect purchase intent, objections and unresolved questions.
- Link conversations to customer, session, product, order and campaign when identity evidence permits.
- Recommend factual replies, relevant products or recovery actions without inventing availability, delivery promises, discounts or policy.
- Route sensitive, low-confidence, complaint, refund and exception cases to a human.
- Measure response time, resolution quality, assisted conversion, repeat contact, complaints and net profit.

This expands the existing Voice of Customer Engine from analysis into a governed support copilot.

### AI Discovery and Answer-Engine Visibility

Mezan must measure and improve whether products and store facts can be discovered and accurately represented by search engines and AI answer systems. It must:

- Audit product, category, policy and merchant information for crawlability, structured data, factual consistency, source authority and answerability.
- Track a versioned set of commercial queries and the observable citations, recommendations and errors returned by approved search or answer systems.
- Propose content or structured-data experiments and measure qualified traffic, conversion and contribution profit.
- Keep observed external answers separate from provider facts and Mezan recommendations.

AEO/GEO is an acquisition experiment surface, not a replacement for SEO, paid acquisition or verified store facts.

### External Commerce Knowledge Scout

Mezan may ingest approved public research sources such as podcasts, platform documentation, industry reports and market observations into a quarantined evidence layer. It must:

- Preserve source URL, publisher, publication date, retrieval time, excerpt or summary provenance and confidence.
- Extract claims as unverified external hypotheses, never as operational facts.
- Deduplicate ideas against the roadmap, experiment registry and prior decisions.
- Score relevance to Amasi by expected profit impact, evidence strength, implementation cost, risk and dependency readiness.
- Convert accepted ideas into draft experiments with owner approval, success metrics, rollback rules and an expiry/review date.
- Record rejected and superseded ideas to prevent repeated recommendations.

External content must never directly trigger campaign, pricing, customer-message or production changes.

### Cross-Channel Portfolio Optimization Clarification

The existing Campaign Control Engine must optimize the advertising portfolio across Snapchat, TikTok, Meta and Google Ads rather than optimizing each platform in isolation. Portfolio recommendations must use reconciled spend, attributable orders, contribution profit, marginal performance, inventory, fulfilment capacity and uncertainty. Cross-platform budget movement remains draft-only until the Phase 8 and Phase 9 controls are proven.

## 11. Phase Placement

- **Phase 4:** preserve search/answer visibility observations, external-research provenance and cross-channel identities as versioned facts.
- **Phase 5:** ingest consent, conversation, lifecycle and assisted-conversion signals.
- **Phase 6:** deliver retention scoring, service-to-revenue recommendations, AEO/GEO diagnostics, knowledge-scouter deduplication and portfolio simulations.
- **Phase 7:** prepare reviewed lifecycle messages, support replies and answer-engine content drafts.
- **Phase 8:** prepare approval-bound lifecycle actions, support actions and cross-channel budget proposals.
- **Phase 9:** allow only bounded, reversible actions after incrementality, safety, privacy and reliability gates pass.

None of these objectives changes the current implementation gate: Order Engine, Order Item Engine and financial truth remain prerequisites.
