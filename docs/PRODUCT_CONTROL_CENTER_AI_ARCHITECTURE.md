# Mezan OS Product Control Center — AI-ready architecture

## Authority boundaries

- Salla remains the commerce publishing channel.
- Mezan OS owns analysis, drafts, approvals, audit, local taxonomy, cost profiles, product-knowledge quality, shopper-intent mapping and AI governance.
- Existing cost collections remain independent and authoritative for Mezan cost calculations:
  - `mezan_product_cost_profiles_v2`
  - `mezan_cost_resources_v2`
  - `mezan_product_option_cost_bindings_v2`
  - `mezan_order_item_cost_snapshots_v2`

This architecture also follows `docs/adr/ADR-003-ai-commerce-discovery.md`: a core Mezan AI objective is to make Amasi products increasingly understandable, discoverable and recommendable through natural-language AI commerce while preserving factual accuracy and provenance.

## Change lifecycle

1. Read current product from Salla.
2. Build a Mezan draft containing only allowed fields.
3. Show before/after diff.
4. Approve manually or through a bounded policy.
5. Write approved fields to Salla.
6. Re-read the product from Salla.
7. Verify every intended field.
8. Store immutable audit records and the verification result.
9. Measure downstream product, discovery, conversion and profit outcomes when the change is part of an experiment.

## Product domains

- Core commerce: name, price, sale price, status, SKU, barcode.
- Content: description, short description, SEO title/description/keywords/slug.
- Classification: Salla categories, brand, Google category, Mezan local taxonomy.
- Media: images, ordering, primary image, ALT text.
- Options: choices, custom text fields, variants, inventory.
- Costs: preserved in the existing Mezan cost engine and never overwritten by Salla publishing.
- Performance: visits, add-to-cart, checkout, orders, conversion, returns, ad cost, profit.
- Product knowledge: verified semantic attributes needed to understand and compare the product in natural language.
- Discovery intelligence: shopper intents, query/intent coverage, recommendation evidence and AI/search-assisted outcome metrics where legitimately available.

## Product Knowledge Layer

The Product Control Center must progressively expose and govern a structured Product Knowledge Layer rather than treating a product as only a title, description and price.

Useful knowledge fields may include, when known and legitimately sourced:

- Product/variant identity.
- Product type and category.
- Material.
- Finish/plating.
- Color.
- Stone/gem type.
- Length, size, dimensions and weight.
- Style vocabulary.
- Care instructions.
- Availability/sellability.
- Shipping, return and warranty facts.
- Gift/occasion/use-case attributes.
- Product questions and answers.
- Customer-language phrases linked to the product.
- Media evidence.
- Conversion, return and profitability signals.

Authoritative product facts and AI-generated semantic inferences must never be mixed silently. Any inferred attribute must preserve:

- inference type;
- model/version;
- created time;
- evidence/source references;
- confidence;
- approval state when it may be published.

## Natural-language shopper intent

The future Product Agent must be able to consume requests such as:

> I want a gold-colored necklace for a gift under 250 SAR with a delicate style.

and preserve both the original language and structured constraints such as:

- budget;
- product type;
- material/color/finish;
- style;
- occasion;
- size/length;
- delivery requirement;
- must-have/must-not-have conditions.

The recommendation path is:

`Shopper request → structured intent → candidate products → evidence checks → ranked products → product/session/order → profit outcome`

A language model preference by itself is not sufficient evidence for product suitability.

## AI-commerce-discovery quality checks

The Product Control Center should eventually flag:

- Important product facts missing.
- Conflicting facts between product sources.
- Unsupported claims in title/description/FAQ.
- Weak category or semantic coverage.
- Common customer questions not answered by product knowledge.
- Natural-language intents with no good product match.
- Products repeatedly recommended but not converting.
- Products with demand signals but weak media/content representation.
- Published structured data/feed fields that disagree with authoritative facts.

## Shared use of product knowledge

The same Product Knowledge Layer should support:

- Product pages.
- Onsite search and filters.
- Future conversational shopping inside Amasi/Mezan.
- External AI/search/shopping discovery when an approved integration exists.
- Customer-service answers.
- Product comparisons.
- Creative briefs and advertising copy.
- Product-page FAQs.
- Demand-gap and assortment analysis.
- Product development and photography priorities.

Do not create separate competing taxonomies for SEO, ads, customer service and AI shopping when one governed knowledge model can serve them.

## Measurement

When discovery metadata is legitimately available, preserve evidence that can support:

`AI/Search Source → Intent/Query → Product → Session → Order → Order Item → Profit`

The system must allow incomplete or confidence-based attribution. Never invent an AI source or exact query when the source does not provide it.

Future product/discovery metrics may include:

- Product knowledge completeness.
- Product knowledge confidence.
- Intent coverage.
- Recommendation acceptance/click-through.
- Add-to-cart and conversion from AI-assisted journeys.
- Net profit from AI/search-assisted journeys.
- Common unmatched shopper intents.
- Content/attribute changes that improved discovery or conversion.

## AI execution levels

- `suggest_only`: AI creates a draft only.
- `approval_required`: AI draft requires human approval.
- `bounded_auto`: automatic publishing only inside explicit field and risk limits.

Price, sale price, visibility, inventory, and destructive media actions must default to `approval_required`.

Any AI-initiated production mutation must pass through the Mezan Action Gateway under ADR-002. AI Commerce Discovery does not authorize direct model-to-Salla publishing.

## Truth and safety rules

- Never fabricate material, stone, dimensions, warranty, certification, availability or delivery facts.
- Never publish inferred suitability as an authoritative fact without labeling/review rules.
- Never expose private customer information as public product knowledge.
- Preserve provenance for important product facts and semantic attributes.
- Prefer unknown/missing over invented values.
- Any discovery optimization must remain useful and truthful for the human shopper, not only an AI crawler or assistant.
