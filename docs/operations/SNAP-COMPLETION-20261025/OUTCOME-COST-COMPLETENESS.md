# Decision outcome cost completeness

Base: `72ab1aee0` on `hotfix/prod-snap-meta-final` (PR #1564 included). This independent change affects only the decision outcome evaluator and its tests; it does not alter Saudi reporting, provider writes, approvals or release controls.

The outcome evaluator previously rejected a scope only when `cost_complete is False`. Missing/null/numeric/string values therefore passed as complete. It also accepted a claimed-complete scope without product-cost or profit amounts. A sales-increase expectation could consequently be permanently recorded as successful while its financial evidence was unknown, contrary to the evaluator's own `all_comparable_scopes_and_product_costs_must_be_complete` policy.

The evaluator now requires explicit boolean `True` and finite product-cost and contribution-profit amounts (store scope uses gross profit before marketing). The existing capture producers already emit these fields; complete valid snapshots retain behavior. Unknown financial scopes remain inconclusive and retryable without appending a final evaluation. Explicit complete zero outcomes remain measurable. Profit need not be positive: this is a completeness check, not an automatic marketing threshold or causality assertion.

Baseline reproduction: the new public `evaluate_ad_decision` regressions failed **42 cases**, all because baseline returned `successful` instead of `inconclusive`. They cover both baseline/post windows and campaign/account/store scopes, unknown proof types, and missing financial amounts.

Focused verification: outcome, decision metrics and decision ledger suites pass; final count is recorded in the coordinator handoff. A broader scheduler suite had one unrelated failure, `test_campaign_ai_proof_window_is_three_days_only_for_meta_and_snapchat` (`KeyError: meta_ads`). It reproduces in isolation and with the exact original outcome `_scope_complete` function restored in memory. No scheduler change is included.

No production change, real decision, advertising mutation or publication was performed by this isolated task. Root owns fresh integrated verification and any governed release.

## Review hardening and CI selection

The public evaluator also used to accept negative product cost and negative/fractional order counts as complete. New regressions reproduced 18 failures on the previous implementation (18 additional invalid/non-finite cases were already rejected). It now requires a nonnegative integral order count and nonnegative gross sales, realized product cost, and selected-account campaign spend. Finite negative contribution/gross profit remains valid; explicit zero costs, sales and spend remain valid.

These signs follow the current producer contract: `snapchat_campaign_profitability._order_cost_and_products` clamps gross order sales at zero; `order_status_policy.effective_product_cost` treats cancelled/full-refund costs as zero and partial refunds as proportional reductions bounded between zero and original cost. Refunds are not negative cost ledger entries in these snapshots. The spend rows represent provider delivery spend, not signed settlement adjustments. No rule has been added to a refund or accounting ledger. The multiplier fixture now rounds simulated order counts to actual integers while leaving monetary expectations unchanged.

`.github/workflows/snapchat-reporting-v2.yml` now includes exact source/test path triggers and runs the outcomes, metrics and ledger suites in its existing dependency-provisioned job. YAML parsing and source-to-test trigger/command mapping passed. The related settings workflow already allows this reporting workflow path; no new workflow, production credentials or runtime write is introduced.

Fresh targeted execution after hardening: outcomes, metrics, ledger and unified cutover suites: **147 passed, 2 skipped**, exit 0 (3.21 seconds). The two skips are inapplicable whole-store spend cases: that scope is explicitly before marketing costs. The 12 unified-cutover checks confirm other-platform routes remain compatible. Existing finalized outcome ledger entries are immutable and are not rewritten by this change.
