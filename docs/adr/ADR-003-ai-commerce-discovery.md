# ADR-003 — AI Commerce Discovery and Amasi Growth

**Status:** Accepted (2026-08-08)
**Scope:** Mezan OS product knowledge, product intelligence, search/discovery, customer intent, content, attribution, experimentation and future AI-shopping integrations.
**Depends on:** ADR-001 and ADR-002.

---

## Context

Commerce discovery is shifting from keyword-only search and platform browsing toward natural-language product discovery through AI assistants, answer engines, conversational search and shopping agents.

A customer may increasingly express a need such as:

> I want a gold-colored necklace for a gift under 250 SAR with a delicate style.

The strategic problem is no longer only whether Amasi ranks for a keyword. Amasi products must be represented with enough accurate, structured and traceable knowledge that Mezan can understand the request, match it to suitable products, explain the recommendation, improve the product/catalog data, publish approved facts to commerce channels and measure whether AI-assisted discovery produces profitable orders.

This capability must be built into the core AI-native objective of Mezan OS rather than treated as a later SEO add-on.

---

## Decision

### 1. AI Commerce Discovery Is a Core Mezan Objective

A core long-term objective of Mezan AI is to make Amasi understandable, discoverable and recommendable through natural-language commerce interfaces.

Mezan must support the full loop:

`Customer intent → structured constraints → candidate products → evidence-ranked recommendation → product/session/order → profit outcome → learning`

This objective serves Amasi growth first, while remaining general enough for future stores operated by Mezan OS.

### 2. Product Knowledge Is a First-Class Data Layer

Mezan must maintain a structured **Product Knowledge Layer** above authoritative product facts and below AI recommendation logic.

The layer may include verified facts such as:

- Product and variant identity.
- Name and short/long description.
- Price, sale price and currency.
- Availability and sellability state.
- Category and product type.
- Material and finish/plating.
- Color.
- Stone/gem type when known.
- Dimensions, length, size and weight when known.
- Care instructions.
- Warranty/return/shipping facts where legitimately sourced.
- Images and media references.
- Gift/occasion/use-case attributes when explicitly provided or safely derived as labeled inference.
- Style and design vocabulary.
- Customer questions and objections linked to the product.
- Performance and profitability signals.

Authoritative facts and AI-derived semantic attributes must remain distinguishable. Inferred attributes require provenance, model/version and confidence.

### 3. Natural-Language Intent Becomes a Canonical Intelligence Object

Mezan must be able to represent a shopper request as structured intent without discarding the original request.

Examples of intent dimensions include:

- Budget.
- Recipient or relationship when voluntarily supplied.
- Occasion.
- Product type.
- Color/material/finish.
- Style.
- Size/length.
- Delivery constraint.
- Must-have and must-not-have conditions.

The original natural-language request, extracted constraints, confidence and any later purchase outcome must remain traceable.

### 4. Recommendation Must Be Evidence-Backed

Product recommendations must rank candidates using explicit facts, constraints and business rules rather than unsupported model preference.

A recommendation should be able to explain, for example:

- Matches the requested budget.
- Matches requested color/material/style.
- Is currently sellable.
- Has suitable delivery availability.
- Has stronger conversion or return performance for similar legitimate intents.
- Produces acceptable sustainable contribution after costs.

Commercial optimization may influence ranking, but Mezan must not misrepresent product suitability or fabricate product attributes.

### 5. AI Shopping Readiness Is Broader Than SEO

SEO remains useful, but Mezan must also optimize for machine-readable and conversational discovery.

The system should progressively support:

- Clean canonical product facts.
- Structured taxonomy and attributes.
- Natural-language product questions and answers.
- Product comparison facts.
- Intent-to-product matching.
- Approved structured data and feed quality.
- AI-readable product descriptions that remain useful to humans.
- External AI/search/shopping discovery channels when technically and commercially appropriate.

Do not create keyword stuffing, synthetic claims or misleading metadata for AI crawlers or assistants.

### 6. Mezan Must Measure AI-Origin and AI-Assisted Commerce

Where a channel exposes legitimate referral, campaign, query, session or assistant metadata, Mezan should preserve it and link the journey to:

`AI/Search Source → Intent/Query → Product → Session → Order → Order Item → Profit`

Because external AI systems may not expose complete attribution, Mezan must retain multiple evidence sources and confidence instead of claiming false certainty.

Useful future measures include:

- AI-assisted sessions.
- Product recommendation click-through.
- Product match acceptance.
- Add-to-cart and checkout rate from AI-assisted journeys.
- Orders and net profit from AI/search discovery sources.
- Query/intent coverage gaps.
- Products repeatedly requested but poorly represented.
- Products recommended but not converting.
- Customer language that should improve taxonomy, descriptions or merchandising.

### 7. Product Agent and Growth Agent Share This Responsibility

The Product Agent owns analysis of product knowledge completeness, product semantics and product-level discovery readiness.

The Growth Agent analyzes market/discovery opportunities, query/intent gaps and profitable growth opportunities.

The Customer Agent contributes legitimate customer-language and Voice-of-Customer signals.

The Mezan Supervisor may coordinate these agents, but no agent becomes the source of truth for product facts.

### 8. Product Knowledge Must Feed Amasi Improvement

The purpose is not only to answer external AI assistants. Mezan must use the same knowledge to improve Amasi itself, including:

- Better product titles and descriptions.
- Better category and attribute completeness.
- Better onsite search and filters.
- Better product comparison and recommendation.
- Better product-page FAQs.
- Better creative briefs and ad copy.
- Better understanding of unmet demand.
- Better selection of products to photograph, stock, promote or develop.

This creates one shared product-intelligence foundation instead of separate SEO, advertising and customer-service taxonomies.

### 9. Learning Must Run Through the Experiment and Business Memory Layers

Changes made for AI/search discovery should be measured as experiments when practical.

The loop is:

`Observation → hypothesis → approved change/experiment → exposure → behavior/order/profit measurement → result → Business Memory`

Mezan should learn which product facts, content structures, recommendation patterns and discovery channels improve sustainable net profit and customer experience.

### 10. Privacy, Truth and Safety Are Mandatory

- Do not infer or store sensitive customer attributes from weak proxies.
- Do not fabricate materials, stones, dimensions, certifications, warranties or availability.
- Do not expose private customer information in public product knowledge.
- Respect channel terms, privacy controls and approved data-retention policies.
- Any product mutation initiated by AI must follow ADR-002 and the Mezan Action Gateway.

---

## Consequences

- Product Definition work must preserve rich product and variant attributes that may be needed by future conversational discovery.
- Product Control Center is not only a publishing UI; it becomes the governed editor for product knowledge quality.
- Customer conversations become a useful source for discovering real product language and unmet intent, subject to privacy controls.
- Attribution must reserve space for AI/search-assisted journeys and confidence-based evidence.
- Experiment history must measure discovery/content changes rather than treating them as untracked SEO edits.
- Current production work continues in parallel; this ADR does not authorize premature autonomous publishing or external AI-channel integrations.

---

## Non-Goals

This ADR does not authorize:

- Fabricated product claims.
- Autonomous changes to product price, inventory or visibility.
- Scraping or publishing customer-private information.
- Treating one AI platform as the universal commerce source of truth.
- Replacing Salla as the current commerce publishing channel without a separate approved decision.

---

## Required Reading for Future Product/AI Work

Future work affecting products, search, content, recommendations, customer intent, attribution or AI discovery must read this ADR together with:

- `docs/adr/ADR-001-architecture-principles.md`
- `docs/adr/ADR-002-ai-native-operating-model.md`
- `docs/AI_COMMERCE_OPERATING_SYSTEM_ROADMAP.md`
- `docs/PRODUCT_CONTROL_CENTER_AI_ARCHITECTURE.md`
