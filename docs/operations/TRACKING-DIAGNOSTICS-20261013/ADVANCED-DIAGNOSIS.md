# Additive campaign and product diagnosis

Owner direction (2026-10-13 17:56–17:58 Asia/Riyadh): preserve the existing
recommendations and extend them with professional diagnostic capabilities.
Owner ideas are examples, not an exhaustive checklist. Choose suitable methods
based on available evidence, business value, operating cost and measured benefit.

## Existing surfaces stay authoritative

- AdvancedDashboard campaign recommendation cards and CampaignRecommendations:
  extend the existing shared V3 analysis and recommendation explanation.
- Per-campaign analysis: native TikTok context and panel now share the same
  product/market investigation instructions. Other account-page entry points
  still require an explicit wiring/coverage audit; do not claim every platform's
  Analyze button is integrated because the shared prompt changed.
- Preserve current metrics, hypotheses supported by existing evidence, recommendation
  ranking, approval and execution gates. Missing external research must not erase
  a valid existing recommendation or create a new automation authority.

## Proposed investigation architecture (not yet operational)

1. Evidence reliability: ownership/store/product/account binding; collection time,
   completeness, currency, event definitions, attribution window, sample size.
   Distinguish missing measurement from absent demand or broken tracking.
2. Localize the loss: coherent purchase journey, then aggregate country/device/
   product/new-versus-returning segments when legitimately available. Event totals
   with different users/windows are not a measured cohort funnel.
3. Form competing explanations: creative curiosity vs shopping intent, audience,
   product suitability, timing/seasonality/demand, price/value, availability,
   mobile experience, trust, checkout, shipping, payment and measurement.
4. Select bounded research that can change the decision, rather than browsing
   every source every time. Inspect canonical product pages, exact competitor
   offers and applicable market reports. Reuse fresh scope-specific observations;
   stale sources cannot establish current market conditions. No universal Saudi
   market assumption for every tenant.
5. Review evidence for and against each explanation. Calendar events, social
   engagement, one cheaper competitor or a shared downturn do not prove causality.
   Separate measured fact, supported hypothesis and unanswered research question.
6. Recommend the smallest useful intervention; attach source/time, uncertainty,
   expected mechanism, and how to measure it. No invented lift percentages.
7. Experiment and learn: define one primary outcome, margin/refund safeguards,
   allocation and sample requirements before starting. Prefer valid randomized
   tests where available. Before/after comparison remains confounded by time,
   spend and audience mix. Store experiment outcomes with product/offer versions
   and tenant scope. Do not automatically launch experiments or change live ads.

## Priorities and acceptance gates

First deliver one real campaign -> product -> browser observations -> native
measurement comparison -> evidence-backed recommendation in existing surfaces.
Then add competitor/market collectors and measured experiment history. Do not
start marketing-mix modeling, automated budget optimization or numerical causal
claims without adequate historical data and validation. More model calls alone
are not an advanced diagnostic method.

A complete release must show: no media on Mezan server; bounded external worker;
current owner/store scopes; identical evidence for dashboard/detail consumers;
no fabricated visits or citations; safe behavior when blocked/insufficient;
no unsupported price/seasonality claim escaping output validation; retained
existing recommendations; proof from a real authorized product and campaign.

## Official methods researched on 2026-10-13

- Google Analytics purchase journey and funnel exploration:
  https://support.google.com/analytics/answer/13128171
  https://support.google.com/analytics/answer/9327974
  Useful for locating abandonment and segmenting coherent journeys; not a claim
  that Mezan has a connected GA4 property or complete funnel data.
- Google Merchant API market reports:
  https://developers.google.com/merchant/api/guides/reports/understand-the-market
  Reports include competitive pricing and demand/popularity by country/category.
  Account eligibility and terms apply. No access or coverage is assumed; personalized
  products may lack comparable catalog matches.
- TikTok split testing:
  https://ads.tiktok.com/help/article/split-testing?lang=en
  Controlled groups/variables are preferable to attributing all before/after change
  to an edit. Actual availability and sample adequacy must be checked.
- TikTok Conversion Lift:
  https://ads.tiktok.com/resources/help/article/about-conversion-lift-study?lang=en
  Distinguishes incremental conversions from attributed totals; managed eligibility
  requirements mean this cannot be promised as a universal self-service feature.

## Implemented versus remaining

Implemented: expanded shared prompt methodology; server-owned external-research
status in V3 evidence and normalized recommendation metadata, TikTok context;
additive status component in dashboard, recommendations page and TikTok panel.
These are contracts/visibility, not a live research engine. Prompts alone are not
proof of compliance or deterministic enforcement.

Missing: external browser/search credentials and callable service; verified live
collectors, investigation selection/orchestration, evidence-bound output validation,
remaining account-page integration audit, real end-to-end tests, release gates.
Production has not changed.
