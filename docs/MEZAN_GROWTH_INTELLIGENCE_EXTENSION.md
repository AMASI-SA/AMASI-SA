# Mezan Growth Intelligence Extension

Status: Proposed roadmap extension  
Date: 2026-09-12  
Scope: Goals and integration design only; no production implementation or deployment  
Parent roadmap: [AI_COMMERCE_OPERATING_SYSTEM_ROADMAP.md](AI_COMMERCE_OPERATING_SYSTEM_ROADMAP.md)

## 1. Decision Summary

This review evaluated five ideas surfaced from recent ecommerce and AI podcast discovery, plus the proposed external-learning layer.

| Idea | Existing coverage | Decision |
| --- | --- | --- |
| General agentic commerce executive | Already explicit in Commerce Executive Agent, autonomy levels and decision/action ledger | Do not duplicate; preserve existing objective |
| Cross-channel advertising optimization | Campaign Control and attribution exist, but portfolio-level allocation is not explicit | Clarify existing objective |
| Retention and repeat-purchase automation | Repeat-purchase probability and CLV exist as features; lifecycle execution is not specified | Add explicit lifecycle objective |
| Customer service as a revenue engine | Conversation ingestion and Voice of Customer exist; governed reply/assisted-sale operation is only partial | Add service-to-revenue objective |
| AI answer-engine visibility (AEO/GEO) | Search Console is planned, but AI answer discovery and factual representation are absent | Add new acquisition objective |
| External knowledge scout | Learning from internal outcomes exists; external evidence intake, deduplication and hypothesis governance are absent | Add new evidence objective |

The additions are useful because they close operational gaps while remaining subordinate to Mezan's primary target: sustainable net profit with traceable evidence and bounded autonomy.

## 2. Lifecycle and Retention Intelligence

### Business outcome

Increase profitable repeat purchases and recover customers without relying exclusively on increasingly expensive paid acquisition.

### Inputs

- Canonical customers, orders, order items, products and variants.
- Paid amount, refunds, cancellations, contribution profit and product cost.
- Consent and channel preferences.
- Conversation and complaint history.
- Product availability, preparation time and delivery capacity.
- Campaign and acquisition source.
- Message delivery, click, conversion and opt-out events.

### Intelligence

Mezan should calculate:

- Repeat-purchase probability and predicted purchase window.
- Customer lifetime value and contribution-profit history.
- Product affinity and next-best-product candidates.
- Churn or win-back probability.
- Suppression risk from refunds, complaints, excessive contact or unresolved service cases.
- Expected incremental profit after discount, channel cost, fulfilment cost and return risk.

### Actions

The engine first recommends, then prepares drafts, and only later executes within approved limits:

- First-purchase follow-up.
- Product replenishment reminder where the product genuinely has a replenishment cycle.
- Cross-sell or complementary-product proposal.
- Win-back offer.
- High-value-customer recovery.
- Suppression of messages when contacting the customer is unsafe, irrelevant or non-compliant.

### Required controls

- Explicit consent and lawful channel use.
- Frequency caps and quiet hours.
- No fabricated discount, stock or delivery promise.
- Holdout/control groups for incrementality.
- Human approval until reliability gates are met.
- Emergency stop, audit trail and per-channel allowlist.

### Success measures

- Incremental repeat-order rate versus control.
- Incremental contribution profit, not attributed revenue alone.
- 30/60/90-day retention.
- Opt-out, complaint, refund and return rates.
- Message cost and profit per contacted customer.
- Calibration of repeat-purchase and churn predictions.

## 3. Service-to-Revenue Intelligence

### Business outcome

Resolve customer questions faster and recover legitimate sales while protecting trust and policy compliance.

### Inputs

- WhatsApp, email, live-chat and approved social conversations.
- Customer, session, cart, product, order and campaign identities.
- Current catalog, price, stock and preparation facts.
- Store policies, delivery rules, refund and replacement policy.
- Prior replies, resolution state and customer feedback.

### Intelligence

The engine classifies:

- Pre-sale intent.
- Objections about price, delivery, size, trust, payment or product details.
- Post-sale support, complaint, refund and exception cases.
- Urgency, sentiment and risk.
- Whether identity linking is certain, probable or unknown.
- Whether an answer can be grounded in current verified facts.

### Actions

- Suggest a factual response with citations to internal facts.
- Recommend a relevant product only when it fits the expressed need.
- Recover a cart without exposing private cart information to the wrong identity.
- Summarize the conversation for the human agent.
- Escalate low-confidence, angry, legal, refund, payment or delivery-exception cases.
- Learn from accepted edits and measured outcomes without silently changing policy.

### Required controls

- Human review before sending until approved thresholds are proven.
- No autonomous promises, refunds, discounts or policy exceptions.
- PII masking, least privilege and retention limits.
- Clear separation between generated language and verified store facts.
- Identity-confidence gate before exposing order or cart details.
- Quality evaluation in Arabic and relevant dialects.

### Success measures

- First-response and resolution time.
- Factual accuracy and policy compliance.
- Assisted conversion within defined attribution windows.
- Customer satisfaction, repeat-contact rate and escalation rate.
- Complaint/refund impact.
- Incremental contribution profit after service cost.

## 4. AI Discovery and Answer-Engine Visibility

### Business outcome

Make Amasi products and policies accurately discoverable when customers use AI assistants and answer engines, while preserving conventional SEO.

### Inputs

- Product, category, policy, shipping, returns and merchant pages.
- Structured data and crawl/index state.
- Search Console and GA4.
- A versioned query library in Arabic and relevant Saudi buying language.
- Observable answer-engine results, citations, recommendations and factual errors.
- Orders, qualified sessions and contribution profit from attributable discovery traffic.

### Intelligence

- Detect missing, contradictory or stale product and policy facts.
- Score crawlability, structured-data coverage and answerability.
- Track whether Amasi is cited, omitted or misrepresented for important queries.
- Separate observed answers from inferred visibility.
- Identify content gaps with commercial relevance rather than chasing raw mention volume.

### Actions

- Propose product-page, category-page, FAQ, policy or structured-data improvements.
- Create reviewed content experiments.
- Recheck the same versioned queries after an agreed observation window.
- Measure downstream qualified traffic, conversion and profit.

### Required controls

- Never generate false reviews, authority signals or unsupported claims.
- Respect robots, platform terms and rate limits.
- Version prompts, query location/language and observation time because answers are non-deterministic.
- Do not treat a single model answer as rank truth.
- Require factual and policy review before publishing content.

### Success measures

- Share of tracked queries with accurate Amasi citation or representation.
- Reduction in material answer errors.
- Qualified organic/AI-referred sessions.
- Conversion and contribution profit from those sessions.
- Structured-data validity and index coverage.
- Experiment lift versus baseline.

## 5. External Commerce Knowledge Scout

### Business outcome

Continuously turn useful external knowledge into prioritized, testable ideas without allowing trends or podcast claims to control the business.

### Sources

Approved sources may include:

- Official platform and provider documentation.
- Credible ecommerce research and market reports.
- Podcasts and interviews with identifiable publisher, guest and date.
- Competitor or market observations collected under approved rules.
- Internal owner or team notes.

### Evidence model

Each external item must store:

- Source type, publisher, title, URL and publication date.
- Retrieval time and source version where available.
- Summary or excerpt provenance.
- Extracted claim and whether it is directly stated or inferred.
- Geography, business type and applicability limits.
- Evidence strength and confidence.
- Expiry/review date.

External claims remain in a quarantined evidence layer and do not become canonical commerce facts.

### Deduplication and scoring

Before recommending an idea, the scout checks:

- Existing roadmap objectives.
- Experiment registry.
- Decision history.
- Rejected, expired and superseded ideas.
- Current phase gates and unavailable dependencies.

Candidate ideas are scored by:

- Expected sustainable net-profit impact.
- Evidence strength.
- Relevance to Amasi and the Saudi market.
- Cost and implementation effort.
- Operational, brand, legal, privacy and financial risk.
- Data readiness and reversibility.

### Output

The scout may create only a draft opportunity record containing:

- Problem or opportunity.
- Source evidence.
- Applicability assumptions.
- Proposed experiment.
- Required data and dependencies.
- Success, guardrail and stop metrics.
- Owner approval state.
- Review or expiry date.

It may not directly change campaigns, budgets, prices, customer messages, catalog content or production systems.

### Success measures

- Percentage of recommendations that are genuinely new.
- Acceptance rate and reason-coded rejection rate.
- Time from discovery to approved experiment.
- Experiment success and incremental contribution profit.
- Rate of stale, duplicate or unsupported recommendations.
- Source diversity and evidence-quality distribution.

## 6. Cross-Channel Portfolio Optimization

### Business outcome

Allocate advertising effort by marginal contribution profit and constraints across Snapchat, TikTok, Meta and Google Ads.

### Required inputs

- Reconciled provider spend and delivery facts.
- Canonical campaign/ad/creative identities.
- Salla orders and order items.
- Attribution uncertainty.
- Product margin, refunds, cancellations and fulfilment cost.
- Inventory and preparation capacity.
- Platform budgets, bids and delivery state.

### Decision design

The system compares marginal opportunities, not only average ROAS. A recommendation must state:

- Amount to move, source platform and destination platform.
- Expected incremental orders and contribution profit.
- Confidence interval and attribution assumptions.
- Inventory or fulfilment constraints.
- Observation window, cooldown and rollback trigger.

No cross-platform budget move is executable until Campaign Copilot controls, financial truth, simulation and rollback gates are proven.

### Success measures

- Incremental contribution profit versus unchanged-allocation baseline.
- Forecast calibration.
- Spend and loss guard violations.
- Budget churn and unnecessary reversals.
- Delivery stability and stock/fulfilment incidents.

## 7. Integration Sequence

1. **Foundation:** complete canonical order, order-item and financial truth gates.
2. **Facts:** add consent, conversation, lifecycle, search visibility and external-evidence records.
3. **Observe:** run all new capabilities read-only with provenance and confidence.
4. **Recommend:** create reviewable recommendations and experiment drafts.
5. **Draft:** generate messages, replies, content and budget proposals without execution.
6. **Approve and measure:** execute only explicitly approved experiments and maintain holdouts where appropriate.
7. **Bounded autonomy:** permit narrowly allowlisted, reversible actions only after reliability and safety thresholds are proven.

## 8. Shared Architecture Contracts

New capabilities should reuse the roadmap's canonical entities and add only necessary records:

- `lifecycle_signal` and `lifecycle_recommendation`.
- `consent_state` and `contact_policy_decision`.
- `conversation_intent`, `reply_draft` and `assisted_conversion`.
- `discovery_query`, `answer_observation` and `visibility_experiment`.
- `external_source`, `external_claim` and `opportunity_hypothesis`.

Every record needs source identity, event/source/ingestion time, version, confidence, privacy class and links to the decision/experiment ledger where applicable.

## 9. Acceptance Gates

These objectives are not implementation-ready until:

- Order and order-item identities are production-validated.
- Financial truth reconciles profit inputs.
- Consent and privacy rules are approved.
- Attribution uncertainty is documented.
- Current product, stock, policy and delivery facts are queryable.
- Recommendation evaluation datasets and holdout methods exist.
- Audit, approval, cooldown, rollback and emergency-stop controls are tested.

## 10. Deliberately Excluded

This extension does not authorize:

- Autonomous publishing or customer messaging.
- Autonomous refunds, discounts or policy exceptions.
- Unbounded ad spend or cross-platform budget movement.
- Treating podcast, competitor or AI-generated statements as verified facts.
- Scraping or monitoring that violates access rules or platform terms.
- Replacing the approved roadmap phase order.
