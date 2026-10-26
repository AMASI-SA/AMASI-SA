# Decision outcome cost completeness

Base: `72ab1aee0` on `hotfix/prod-snap-meta-final` (PR #1564 included). This independent change affects only the decision outcome evaluator and its tests; it does not alter Saudi reporting, provider writes, approvals or release controls.

The outcome evaluator previously rejected a scope only when `cost_complete is False`. Missing/null/numeric/string values therefore passed as complete. It also accepted a claimed-complete scope without product-cost or profit amounts. A sales-increase expectation could consequently be permanently recorded as successful while its financial evidence was unknown, contrary to the evaluator's own `all_comparable_scopes_and_product_costs_must_be_complete` policy.

The evaluator now requires explicit boolean `True` and finite product-cost and contribution-profit amounts (store scope uses gross profit before marketing). The existing capture producers already emit these fields; complete valid snapshots retain behavior. Unknown financial scopes remain inconclusive and retryable without appending a final evaluation. Explicit complete zero outcomes remain measurable. Profit need not be positive: this is a completeness check, not an automatic marketing threshold or causality assertion.

Baseline reproduction: the new public `evaluate_ad_decision` regressions failed **42 cases**, all because baseline returned `successful` instead of `inconclusive`. They cover both baseline/post windows and campaign/account/store scopes, unknown proof types, and missing financial amounts.

Focused verification: outcome, decision metrics and decision ledger suites pass; final count is recorded in the coordinator handoff. A broader scheduler suite had one unrelated failure, `test_campaign_ai_proof_window_is_three_days_only_for_meta_and_snapchat` (`KeyError: meta_ads`). It reproduces in isolation and with the exact original outcome `_scope_complete` function restored in memory. No scheduler change is included.

No production change, real decision, advertising mutation or publication was performed by this isolated task. Root owns fresh integrated verification and any governed release.
