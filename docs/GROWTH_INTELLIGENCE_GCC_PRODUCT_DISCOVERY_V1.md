# Growth Intelligence V1 — GCC Market, Season and Product Discovery

## Status

Separate workstream. This is **not** part of Campaign AI Decision Intelligence V3 and must not be inserted into the five-hour advertising decision loop.

## Why this is separate

Campaign AI answers: **What should we do with an active ad/campaign now, and why?**

Growth Intelligence answers a different class of questions:

- What should Amasi prepare before a coming salary/liquidity window or season?
- Which GCC country is the best next market for a proven product?
- Which products are becoming interesting enough to source, photograph, list, stock and test?
- Which trend is real enough to investigate, and what evidence could prove it wrong?
- What product/creative/merchandising work should be ready before the opportunity arrives?

Putting these into the same five-hour worker would mix real-time ad diagnosis with slow-moving market research, create unstable external dependencies, and make campaign decisions harder to audit.

## Operating model

### 1. Three different clocks

**Campaign AI:** established five-hour cadence. Uses Growth Intelligence only as contextual evidence.

**Operational Product Watch:** established fast product-health cadence for hidden/OOS/broken-page alerts.

**Growth Intelligence:** its own cadences:

- Daily: refresh official calendars, market signals and high-priority product opportunities.
- Weekly: product/category opportunity review and GCC market ranking.
- Seasonal horizon: continuously maintain 90/60/45/30/21/14/7-day preparation views where source dates support them.
- Event-triggered/manual: owner can request a focused study for a product, category, country or season.

No cadence is allowed to fabricate urgency merely because a date is near.

## Core source hierarchy

### Tier A — First-party Amasi evidence

Highest commercial relevance:

- Salla orders and order items.
- Product/variant sales velocity.
- Contribution profit and margin where exact cost is available.
- Geographic order/shipping country history.
- Search/customer-intent signals available legitimately inside Amasi/Mezan.
- Abandoned-cart/product interest as corroborating evidence.
- Campaign-product performance where attribution is valid.
- Inventory, lead time and prior sell-through when available.

### Tier B — Official market/calendar sources

Used for dates and structural market context:

- Official national calendars and public holidays.
- Official salary/support/pension schedules when published.
- Official statistics/ecommerce/population/income indicators where useful.
- Official customs/tax/payment/shipping rules when market-entry feasibility depends on them.

A private-sector payroll date must never be presented as one universal date unless an authoritative source actually supports that claim. When payroll varies, represent it as an uncertain window or learn it from Amasi's own demand history.

### Tier C — External product/trend discovery

Examples may include:

- Supplier marketplaces such as Alibaba or other approved sourcing sources.
- Retail marketplaces and large retailers used as **trend observations**, not automatically as suppliers.
- Comparable Saudi/GCC stores.
- Search/social trend sources where access and terms permit.
- Public product reviews/signals that are legitimate to use.

Every external signal requires source, observation time, country where relevant, reliability and limitations.

Do not claim "trending", "high demand" or "popular in Qatar/UAE" from a single weak source.

## Product discovery loop

`External signal + first-party audience fit → candidate → evidence review → unit economics → minimum viable test → owner approval → sample/test stock → product draft → photos/content → small market test → actual order/profit outcome → learning`

### Candidate analysis must include

- Product/category identity.
- Why it resembles things Amasi customers already buy or ask for.
- Which GCC markets appear most compatible and why.
- Source/supplier options and whether each source is a supplier or only a trend observation.
- MOQ, price, lead time and landed-cost inputs when legitimately known.
- Expected margin range only when enough cost data exists.
- Season/use case.
- Risks: fad decay, size/variant complexity, returns, shipping, quality, IP/trademark, long lead time, weak differentiation.
- What evidence would disprove the opportunity.

### Possible recommendations

- `WATCH`
- `SOURCE_SAMPLE`
- `ORDER_TEST_STOCK`
- `PREPARE_PRODUCT_DRAFT`
- `REJECT_FOR_NOW`

No supplier purchase is autonomous in V1.

## Product launch package

When a candidate reaches `PREPARE_PRODUCT_DRAFT`, Growth Intelligence should produce a governed launch package rather than only saying "add this product":

- Suggested product title based on verified attributes.
- Description outline: problem/use case, material/size/contents, objections, fulfillment, care where factual.
- Hero-image brief.
- Required gallery shots and angles.
- Short-video/UGC/Story creative briefs.
- Offer/price hypotheses, not fabricated competitor claims.
- Variant/size/color plan.
- Minimum test-stock logic based on known sales velocity, lead time and risk; no invented quantity.
- Target market test plan.
- Success/failure criteria and evaluation window.

Product facts remain authoritative from Salla/supplier-verified data. AI-derived copy must not invent materials, dimensions, certifications, guarantees or availability.

## Season Intelligence

The system should maintain upcoming opportunity cards by country.

Example shape:

- Country: UAE
- Season/event: verified national/retail event
- Event date
- Preparation start recommendation
- Product themes appropriate to Amasi's actual assortment/audience
- Supplier lead-time risk
- Photography/content deadline
- Inventory commitment deadline
- Campaign test window
- Evidence and confidence

The same framework applies to Saudi Arabia, Qatar, Kuwait, Bahrain and Oman.

A season recommendation must be created early enough to account for sourcing, production/customization, photography, listing, logistics and creative testing—not merely a few days before the event.

## Liquidity Intelligence

Liquidity events are contextual demand variables, not automatic budget rules.

Track separately where verified:

- Government salary schedules.
- Private payroll windows when evidence supports a window.
- Citizen/support programs.
- Pension/social-support schedules.
- Large holiday/season spending windows.

Growth Intelligence prepares opportunity context before the window. Campaign AI later decides whether an active campaign should actually scale based on live performance, product capacity and profit.

## GCC market expansion

Do not rank GCC countries using cultural stereotypes.

For each product/category compare:

1. First-party Amasi order history by country when available.
2. Product/category demand signals.
3. Audience/use-case fit.
4. Competitive density and price/value positioning.
5. Shipping cost/time and return feasibility.
6. Payment support.
7. Product restrictions/compliance where relevant.
8. Season timing.
9. Localization needs.
10. Stock and operational capacity.

### Example: abayas / modest-fashion product

The system may discover that one or more GCC countries show stronger evidence for a specific abaya style, but it must cite the observed evidence. It must not simply say "country X likes abayas" without market or first-party support.

A proven Saudi product can receive:

- `WATCH` for another GCC market when evidence is weak.
- `TEST` when fit is plausible but unproven.
- `EXPAND` only after enough market and operational evidence exists.
- `DO_NOT_EXPAND_YET` where economics, logistics or fit are weak.

## Relationship to Campaign AI V3

Growth Intelligence may publish a compact, versioned context object containing only:

- verified upcoming events/liquidity windows;
- market-opportunity summaries;
- product/category opportunity context;
- source references, freshness and confidence.

Campaign AI may use that context to explain or challenge a decision, but:

- context never forces Pause/Scale;
- market research never becomes campaign revenue attribution;
- external trend claims never override actual Amasi performance;
- Growth Intelligence never writes to Snapchat/Meta;
- Campaign AI does not perform supplier sourcing during every five-hour run.

## Relationship to Product Agent / Product Control

Growth Intelligence proposes **what may be worth adding or testing**.

Product Agent/Product Control owns **product knowledge quality and governed product creation/editing**.

A future approved workflow can be:

`Growth candidate → owner approves sample/test → verified supplier/product facts → Product Agent drafts title/description/media plan → owner approves publish → Salla product → small campaign test → outcome memory`

## Governance

V1 is recommendation-only for external growth actions.

Requires owner approval before:

- Ordering supplier stock or samples.
- Creating/publishing a new Salla product.
- Changing price.
- Increasing inventory commitment.
- Opening paid media in a new country.
- Expanding a live campaign into a new market.

No autonomous external purchasing.

## Data quality / anti-hallucination requirements

- Every market/trend claim has provenance.
- Every source has `observed_at` and reliability.
- Unknown cost remains unknown.
- Unknown market size remains unknown.
- No invented "probability of success" percentage.
- Confidence is qualitative unless a calibrated model with historical labels exists later.
- Retail popularity does not prove supplier quality.
- Supplier order count does not prove Amasi audience fit.
- Saudi success does not prove GCC success.
- Product fit must be evaluated against actual Amasi customer/product evidence.

## Initial UI destination

A separate `Growth Intelligence` section, not the Campaign Recommendations page, with four primary views:

1. **المواسم والسيولة** — upcoming calendars and preparation windows.
2. **فرص المنتجات** — watch/sample/test-stock candidates.
3. **فرص دول الخليج** — product-by-country fit and blockers.
4. **تجارب النمو** — approved tests and measured outcomes.

Campaign Recommendations may show a small contextual badge/link back to the relevant Growth Intelligence evidence, but not duplicate the whole research workspace.

## Initial implementation milestones

### Milestone A — governed data model

- Schemas for evidence, liquidity, seasons, GCC opportunities and product candidates.
- Persistent source registry with freshness/reliability.
- First-party geographic/product-history aggregation.

### Milestone B — season/liquidity calendar

- Official-source ingestion.
- Preparation windows.
- No automatic media action.

### Milestone C — product opportunity discovery

- Approved external search/source adapters.
- Deduplication and canonical candidate identity.
- Audience/portfolio similarity.
- Unit-economics gate.
- Owner review queue.

### Milestone D — GCC expansion intelligence

- Country-level first-party evidence.
- Shipping/payment/economics constraints.
- Market test recommendations.

### Milestone E — product launch package

- Product draft plan.
- Photography/gallery brief.
- Video/Story creative brief.
- Description/title outline.
- Small-stock and campaign experiment plan.

## Non-goals for V1

- No unreviewed web scraping hidden inside Campaign AI.
- No autonomous supplier checkout/order.
- No autonomous Salla publishing.
- No autonomous GCC campaign launch.
- No fake trend score.
- No country stereotypes as evidence.
