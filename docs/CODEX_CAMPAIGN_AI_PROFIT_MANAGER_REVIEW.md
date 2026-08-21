# Codex Review Brief — Campaign AI / Profit Manager

## Mode: REVIEW ONLY

This task is a **read-only audit**. Do not modify repository files, do not create commits, do not open or update PRs, do not merge, do not deploy, and do not change prompts, schemas, workflows, tests, production data, or configuration.

If you identify a defect, document it. Do **not** fix it.

## Scope

Review **Campaign AI as an integrated subsystem inside Mezan**, including every Mezan data path that materially affects its decisions. Do not perform a general audit of unrelated Mezan features such as fulfillment, employee management, Qoyod, customer service, or shipping unless they directly feed profit, product, inventory, order, or campaign-decision evidence.

The review should cover, at minimum:

- Campaign AI V3 decision pipeline.
- Meta data and decision paths.
- Snapchat data and decision paths.
- Salla campaign/order/sales/profit evidence.
- Mezan authoritative profit context.
- Monthly net-profit goal manager.
- Product intelligence, product association, page checks, inventory checks, pricing/margin evidence.
- Creative / visual evidence, image/video asset handling, and what OpenAI actually receives.
- Candidates, evidence packs, OpenAI first pass, review pass, normalization, snapshots, UI, approval, and execution safety.
- Prior decisions, experiments, campaign history, and any learning/memory behavior.

## Product goal

The end-state is **not merely an advertising recommendation engine**.

We are building an AI profit/store manager that behaves like an accountable store owner whose primary financial objective is to achieve at least **SAR 100,000 monthly net profit**, then expand above that floor without recklessly risking the minimum.

Advertising metrics such as ROAS, CPA, CTR, clicks, conversions, funnel metrics, creative metrics, product signals, stock, pricing, and page quality are instruments for achieving the profit goal; they are not the goal themselves.

The intended manager should ultimately be able to reason about:

- Month-to-date net profit.
- Remaining gap to the monthly target.
- Days remaining.
- Required daily net profit.
- Current pace and projected month-end result.
- Whether the store is behind target, on track, or already above the minimum.
- Which campaigns/products/actions can close the gap with the least reasonable risk.
- Waste prevention and capital protection.
- Product, inventory, pricing, page, checkout, creative, and attribution constraints.
- Repeated causes of monthly underperformance.
- Future expansion, product opportunities, and Saudi-market trends.

**Current execution policy must remain approval-gated.** The review must not recommend enabling unrestricted autonomous Ads writes now.

## Current behavior/issues observed in testing

Treat these as hypotheses to verify from code and data contracts, not as proven diagnoses:

1. The monthly target card can show SAR 100,000 while current net profit remains `بانتظار الحساب` and `overall_store_profit_context.progress_available = false`.
2. Campaign AI can still produce recommendations when monthly store-profit context is unavailable.
3. Scale recommendations have appeared while `product_count=0`, product page/inventory/margin evidence is unavailable or UNKNOWN.
4. Snapchat may report zero purchases while Salla has attributed orders/sales and positive campaign contribution; some UI profit-impact numbers appeared inconsistent with that evidence.
5. Creative recommendations may diagnose weak creative even where the report says video detail is unavailable or insufficient.
6. We need exact proof of whether OpenAI receives the actual current ad video/image, a thumbnail/frame, metadata only, or no visual asset for a given recommendation.
7. Some proposed creative-test success thresholds (CPA/ROAS) need provenance: business economics, account baseline, model heuristic, or arbitrary model output.
8. Some suggested A/B test budgets may be too small to generate a meaningful sample.
9. Snapchat can be present in providers/candidates while a final cycle contains only Meta recommendations.
10. User-facing language has improved but can still be repetitive/technical and may present uncertainty as certainty.
11. `profit impact` / `expected change` may use `0.00` where the true meaning could be “not computable,” which is materially different.

## Required review

### 1. Actual current state

Identify what is fully implemented and functioning versus what exists only as UI, schema, context, placeholder, or partial integration.

For each important capability, classify it as:

- **Confirmed from code**
- **Inference**
- **Requires production-data verification**

### 2. End-to-end dataflow

Map this path with concrete modules/functions/collections/routes:

`Meta / Snapchat / Salla / Mezan Profit / Product Data / Visual Assets`
→ `Candidates`
→ `Evidence Pack`
→ `OpenAI First Pass`
→ `Review Pass`
→ `Final Decision`
→ `Normalization`
→ `Snapshot`
→ `UI`
→ `Approval / Execution`

Identify every point where evidence can be lost, stale, transformed, mis-attributed, omitted, or contradicted.

### 3. SAR 100,000 monthly net-profit manager

Verify:

- Authoritative source of net profit.
- Month-to-date date boundaries and timezone.
- Remaining target gap.
- Days remaining.
- Required daily net profit.
- Current pace.
- Projected month-end net profit.
- Status calculation.
- Whether this context actually reaches first-pass and review-pass OpenAI calls.
- Whether it persists in snapshots.
- Fail-closed behavior when profit context is unavailable.
- Whether Campaign AI can make scale decisions that conflict with missing profit context.

Determine whether this is currently a real control objective or mainly a displayed target/context block.

### 4. Meta decision quality

Audit evidence requirements and behavior for:

- increase budget
- decrease budget
- pause/stop
- continue/monitor
- tracking diagnostics
- creative tests
- product/page/checkout diagnostics

Determine whether each action requires sufficient evidence and whether uncertainty blocks execution appropriately.

### 5. Snapchat decision quality and source reconciliation

Trace precisely how the system combines:

- Snapchat Ads Manager conversion reporting
- Snapchat spend/click/impression metrics
- Salla orders and sales
- campaign profitability/contribution
- attribution windows/report time

Verify the system does not equate `Snapchat purchases = 0` with campaign loss when Salla proves attributable sales or positive contribution.

Explain why a cycle can contain Snapchat in providers/candidates but no Snapchat item in the final recommendations. Determine whether omission occurs in candidate selection, first pass, review pass, normalization, or UI.

### 6. Creative / video / visual evidence

Provide a definitive technical answer for each supported provider/entity level:

- Does the actual ad image reach OpenAI?
- Does the actual video reach OpenAI?
- If video is not sent directly, are frames/thumbnails extracted and sent?
- What URLs/bytes/asset metadata are used?
- How many images/frames can be included?
- What happens on visual fallback?
- Which evidence fields prove that the model actually saw the visual?
- Can a creative diagnosis be produced when the model did not see the creative?

If creative diagnosis is based only on CTR/CPA/funnel metrics, label that explicitly as a hypothesis, not direct visual analysis.

### 7. Product, page, inventory, price, and margin evidence

Investigate why `product_count=0` / UNKNOWN can occur for an advertised product.

Trace product association from ad/campaign to Salla/Mezan product identity and verify whether scale can become executable before confirming:

- destination URL
- product visibility
- inventory/capacity
- advertised variant
- price
- product cost / contribution margin where available

Distinguish safety blockers from model advice.

### 8. Profit impact correctness

Trace how the UI fields for campaign profit contribution and expected change are produced.

Check for semantic mixing or double counting among:

- ad spend
- revenue
- gross profit
- contribution profit
- net profit
- operating expenses

Verify whether `expected_change = 0.00` means a real forecast of zero or merely unavailable/uncomputed. If unavailable, flag any UI/schema behavior that represents unknown as numeric zero.

### 9. Recommendation evidence integrity

Look for cases where:

- the model states a root cause not supported by evidence
- UNKNOWN is narrated as known
- a hypothesis becomes a fact
- missing visual evidence becomes a creative diagnosis
- a short sample produces overconfident action
- thresholds are model-invented without provenance
- recommendations conflict with stated limitations

### 10. Testing methodology

Review whether proposed experiment budgets, windows, success criteria, and sample sizes can generate useful evidence given current spend/CPA/conversion rates.

For example, a percentage-of-current-spend test may be statistically or operationally meaningless if it cannot reasonably generate enough conversions.

Do not invent a generic significance framework unless the current code has one; instead identify what exists and what is missing.

### 11. Memory and learning

Trace:

- campaign history
- prior decisions
- executed experiments
- archived/expired experiments
- monthly outcome history
- feedback loops

Determine whether the system **learns** from results (updates policy/model/state) or merely includes prior events as context in future prompts.

Assess whether repeated monthly failure causes can be detected and escalated.

### 12. Gap to a real store/profit manager

Identify everything missing before the system can reasonably act like an accountable store owner optimizing toward monthly net profit.

Classify findings:

- **P0** — blocks trust in current recommendations/data correctness.
- **P1** — required before larger execution permissions/autonomy.
- **P2** — growth/expansion intelligence and future capability.

Future P2 areas can include Saudi-market opportunity discovery, trend/product discovery, assortment expansion, stock planning, offer/pricing strategy, and learning from previous months, but do not implement them.

### 13. Safety and data integrity risks

Specifically review for:

- stale snapshots
- attribution mismatch
- duplicate/double-counted economics
- tenant/user leakage in Campaign AI/profit evidence paths
- hallucinated or unsupported evidence
- unsafe scale on missing data
- execution-capability mismatch
- stale/incorrect product association
- silent provider omission
- unbounded context causing evidence loss/truncation
- error fallback that changes business meaning

Do not broaden into a whole-repository security audit unless a finding directly affects this subsystem.

## Required final report format

Return one review report with these sections:

1. **Executive Summary**
2. **Current architecture and end-to-end dataflow**
3. **What is confirmed working**
4. **Top 10 findings ranked by severity**
5. **Detailed P0 findings**
6. **Detailed P1 findings**
7. **Detailed P2 / future growth gaps**
8. **Meta review**
9. **Snapchat review**
10. **Monthly-profit manager review**
11. **Product/inventory/page intelligence review**
12. **Creative/video/visual evidence review**
13. **Profit-impact/accounting semantics review**
14. **Memory/learning review**
15. **What is already excellent and should not be destabilized**
16. **Proposed roadmap only — no implementation**
17. **Production evidence still required**
18. **Numeric scorecard**

Score 0–100 for:

- Data correctness
- Analysis quality
- Profit management
- Meta intelligence
- Snapchat intelligence
- Product intelligence
- Creative intelligence
- Execution safety
- User clarity
- Readiness for autonomous management
- Overall score

For every material finding include:

- severity
- exact code/module/function evidence
- why it matters commercially
- whether it is confirmed from code, inferred, or requires production verification
- recommended direction (not code changes)

## Hard constraints

- **Do not modify any file.**
- **Do not create/update/delete files.**
- **Do not commit.**
- **Do not create or update a PR.**
- **Do not merge.**
- **Do not deploy.**
- **Do not change prompts.**
- **Do not change tests.**
- **Do not change production data/configuration.**
- **Do not implement fixes discovered during review.**
- Existing passing tests are evidence, but not sufficient proof of correct business behavior.
- Prefer tracing actual runtime code paths and data contracts over file-name assumptions.
- Explicitly separate confirmed facts from inference and production-dependent conclusions.

Start by reading this brief, then inspect the current `hotfix/prod-snap-meta-final` state and produce the review only.