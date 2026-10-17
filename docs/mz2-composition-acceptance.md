# MZ2 composition acceptance evidence

Date: 2026-10-17. Scope: the owner's comprehensive Smart Bundles continuation request, including its 45 numbered acceptance cases. This extends Product V2 / Components V2; it is not a replacement inventory or supplier system.

## Readiness and evidence boundaries

**Acceptance readiness: PASS within the isolated tested scope of the 45 criteria below.** Review findings were fixed and regression-tested. Unresolved supplier assembly and included-cost allocation intentionally block execution rather than inventing costs or procurement instructions; these explicitly unsupported policy cases are listed below. Production publication has not been performed for this extension and remains pending owner approval and the repository release guards.

Final source: `5ee379556c8a05f75a2f77e1727afaa87aac0de5`, branch `codex/mz2-composition-current-prod`, [PR1426](https://github.com/AMASI-SA/AMASI-SA/pull/1426). It is tree-identical to tested `47554c20fed335d132d50f2f73683f752d982c90`; task-only commits were replayed on production29ccdb81a to satisfy the full-history source/intent contract. PR1424 is superseded and its branch retained. Candidate source-base validation passes locally; current branch release-candidate CI remains pending.

Fresh clean-install CI on the identical source tree: [full frontend](https://github.com/AMASI-SA/AMASI-SA/actions/runs/39046159785) **1888 passed across285 suites**, [G47 integration](https://github.com/AMASI-SA/AMASI-SA/actions/runs/39046159797) **277 passed +106 subtests**, UI118 passed and ordinary build passed; zero skipped acceptance cases. These broader CI counts overlap local focused totals. Local full frontend executed1870 passing tests but three suites failed to load due local package junction resolution; clean-install CI passes those suites. Local focused backend total is now **491 +7 subtests**, including the additional selected-option supplier separation regression; post-fix catalog/fulfillment/lifecycle99 passed. Native duplicate details-toggle retry is also fixed with a deterministic regression.

Fresh direct evidence from this implementation agent: **60 catalog tests + 28 policy tests = 88 passed**, including five real Mongo transaction tests. Command:

```powershell
$env:MZ2_TEST_MONGO_URI='mongodb://127.0.0.1:27462/?directConnection=true'
C:/Users/amasi/build37-test-venv/Scripts/python.exe -m pytest backend/tests/test_product_bundle_policy.py backend/tests/test_bundle_catalog_v2.py -q
```

Additional exact-gap suite: **5 real Mongo tests passed in 19.02s**, using the same URI and `PYTHONPATH=backend;backend/tests`: `python -m pytest backend/tests/test_bundle_composition_acceptance.py -q`. This run closes criteria17/18/19/28/30 below through reservation and consumption, including supplier receipts where applicable.

Final coordinator-reported integrated evidence: **490 backend tests + 7 subtests** (154 tests + 7 subtests across five combined suites with no skips, 331 distinct broader regressions, and the five exact acceptance tests above). Frontend: **76 non-overlapping tests** (61 across editor/policy/operations/service suites plus15 across report/product creation/order details). Ordinary Vite compilation exited0. Earlier88-test focused results above are included in these integrated totals and must not be added again.

Coordinator verified local visual previews for two/three/four products at **88 / 113 / 153**. Screenshot files are present in `docs/evidence/mz2-composition/`: `direct-add.png`, `inherited-after-reload.png`, `two-products-cost.png`, `three-products-cost.png`, and `four-products-cost.png`. Final tested commit identity remains a coordinator release deliverable.

`PASS` below means the cited named test directly proves the bounded behavior. `PARTIAL` means related behavior is covered but the exact requested scenario or its full path still lacks direct evidence. Passing component tests does not mean production UAT occurred.

## Test references

All test names in the matrix are exact repository names. Prefixes expand as follows:

| Prefix | Repository file |
|---|---|
| C | `backend/tests/test_bundle_catalog_v2.py` |
| P | `backend/tests/test_product_bundle_policy.py` |
| F | `backend/tests/test_bundle_fulfillment_v2.py` |
| L | `backend/tests/test_bundle_composition_lifecycle.py` |
| A | `backend/tests/test_bundle_composition_acceptance.py` |
| U | `frontend/src/components/products/BundleEditor.test.jsx` |
| UP | `frontend/src/components/products/BundleProductPolicy.test.jsx` |

## Acceptance matrix

| # | Owner acceptance criterion | Status | Exact test evidence and limits |
|---|---|---|---|
| 1 | Selecting a real search result immediately adds its product | PASS | U: `one searchable picker shows image and name and adds selected product`; `rapid repeated selection adds one card, persists identity, and allows explicit quantity`. Typing alone does not add a card. |
| 2 | Hide unnecessary manual product unit | PASS | U: `products use catalog units without a manual unit field` directly verifies catalog unit presentation without a manual input; `failed product details block saving and do not claim a verified unit` handles missing metadata. |
| 3 | Components show their registered unit | PASS | U: `resource card hides supplier for stock and locks catalog unit` verifies the registered component unit and absence of unnecessary manual unit editing. |
| 4 | Products do not require manual sourcing during composition | PASS | C: `test_v2_auto_policy_and_options_are_frozen`; U: `rapid repeated selection adds one card, persists identity, and allows explicit quantity`. Missing operational policy remains visible as unresolved shortage. |
| 5 | Add several distinct products | PASS | U: `four independent suppliers survive edits and optimistic save`; C: `test_four_suppliers_and_nested_snapshot`. |
| 6 | General components | PASS | C: `test_parent_components_packaging_retained`; L: `test_88_composition_consumption_retry_and_frozen_policy`. |
| 7 | Products linked to customer options | PASS | U: `conditional binding uses separate stable field and value and blocks incomplete rule`; C: `test_required_conditions_once_and_explicit_sum`, `test_v2_maps_parent_size_color_and_text_to_existing_child`. |
| 8 | Save and restore after reload | PASS | U: `saved product identity, quantity and condition survive a fresh mount`; coordinator verified real local source UI save/reload: six cards, necklace box checked, bags unchecked, no errors, cost88. Evidence: `docs/evidence/mz2-composition/inherited-after-reload.png`, `two-products-cost.png`, `direct-add.png` (coordinator captured). |
| 9 | Display inherited internal components | PASS | U: `inherited exclusion saves a row override without altering source product`; `unknown inherited policy is displayed explicitly and incomplete expansion warns`. |
| 10 | Enable/exclude components | PASS | Same U exclusion test; C: `test_v2_hoodie_necklace_exclusions_and_frozen_evidence`; L: `test_88_composition_consumption_retry_and_frozen_policy`. |
| 11 | Hoodie + necklace in one bundle | PASS | C: `test_v2_hoodie_necklace_exclusions_and_frozen_evidence`; L: `test_88_composition_consumption_retry_and_frozen_policy`. Synthetic catalog entities, not live stock. |
| 12 | Keep necklace box | PASS | Same C/L tests assert the box is present and consumed. |
| 13 | Exclude necklace bag | PASS | Same C/L tests assert no executable bag leaf and unchanged bag stock. |
| 14 | Exclude hoodie bag | PASS | Same C/L tests assert no executable bag leaf and unchanged bag stock. |
| 15 | Add bundle wrap once | PASS | Same C/L tests assert one wrap per sale unit, including quantity amendment to two sale units. |
| 16 | Standalone source products remain unchanged | PASS | C: `test_v2_nested_composite_native_exclusion_is_local`; L: `test_88_composition_consumption_retry_and_frozen_policy`; P: `test_real_policy_transaction_commits_both_profiles_preserves_product_data`. Source binding equality and standalone expansion invariance are asserted; no live standalone sale is claimed. |
| 17 | Different exclusions in different bundles | PASS | A: `test_distinct_parent_bundles_keep_opposite_exclusions` creates two parents with opposite bag decisions, reserves/consumes both and verifies only the including parent consumes bags; original bindings/other definition remain unchanged. |
| 18 | Different exclusions for different customer choices | PASS | A: `test_customer_options_have_opposite_exclusions_in_same_bundle` runs shared/individual selections through reserve/consume, proves costs88/91, and verifies bag/wrap balances. |
| 19 | Three-product bundle | PASS | A: `test_three_product_composition_full_lifecycle_cost_113` proves three product leaves, expected/actual113, one wrap, excluded bags unchanged and repeated consumption idempotent. |
| 20 | Four-product bundle | PASS | C: `test_four_suppliers_and_nested_snapshot`; F: `test_four_products_two_suppliers_wait_then_consume_once`. These prove four products, supplier waits and one-time consumption. |
| 21 | One current-product unit | PASS | C: `test_current_terminal_units_and_packaging_once`; F: `test_current_product_one_through_four_and_sale_multiplier`. |
| 22 | Two current-product units | PASS | Same C/F tests. |
| 23 | Three/four current-product units | PASS | Same C/F tests. |
| 24 | Sale quantity3 × selected2 =6 actual units | PASS | F: `test_current_product_one_through_four_and_sale_multiplier`; U: `sale quantity three multiplies two physical units without changing definition`. |
| 25 | Two different sizes of the same product | PASS | C: `test_current_slots_distinct_variants_and_supplier_customization`; F: `test_current_product_supplier_slots_preserve_sizes_and_text_edits`; U: `slot resizing preserves different M and XL identities`. |
| 26 | Same component in two intentional rules sums | PASS | C: `test_v2_intentional_rules_sum_and_duplicate_rule_rejected`, `test_v2_extra_explicit_packaging_and_product_supplier_are_independent`. Rule identity prevents accidental replay while distinct rules contribute quantities. |
| 27 | Silver variant from stock | PASS | L: `test_silver_abeer_only_matches_exact_variant_and_name` proves exact silver/name lot selection and excludes wrong lots. |
| 28 | Gold variant from supplier | PASS | A: `test_gold_ordinary_linked_product_routes_supplier_to_receipt_and_consume` maps parent gold to existing child gold, preserves silver stock, creates supplier demand, attaches matching receipt twice safely and consumes exactly once. |
| 29 | Name عبير from stock | PASS | L: `test_silver_abeer_only_matches_exact_variant_and_name`. Exact matching only; no generated text SKU. |
| 30 | Another custom name from supplier | PASS | A: `test_other_name_ordinary_linked_product_preserves_abeer_stock` maps parent name سارة to ordinary child engraving, preserves exact silver عبير stock, receives from supplier and consumes once at actual receipt cost12. |
| 31 | One demand split between stock and supplier | PASS | F: `test_stock_two_supplier_two_and_receipt_retry`, `test_automatic_split_native_receipt_cost_and_replay`; C: `test_v2_preview_projects_split_without_writing_or_mutating_snapshot`. |
| 32 | Hoodie supplier A, necklace supplier B | PASS | F: `test_four_products_two_suppliers_wait_then_consume_once`; C: `test_four_suppliers_and_nested_snapshot` prove independent suppliers for different product identities. Product labels in the four-product fixture are synthetic. |
| 33 | Stock component alongside supplier products | PASS | F: `test_current_product_mixed_supplier_and_conditional_stock_addon`; C: `test_v2_extra_explicit_packaging_and_product_supplier_are_independent`. |
| 34 | Purchased finished product includes packaging in purchase price | PASS | C: `test_v2_fixed_purchase_price_exclusion_no_extra_resource`. Catalog estimate retains fixed purchase price and emits no separate packaging demand; real supplier receipt of that exact packaging case remains unverified. |
| 35 | No double-count of inherited cost | PASS | C: `test_v2_fixed_purchase_price_exclusion_no_extra_resource`, `test_v2_hoodie_necklace_exclusions_and_frozen_evidence`; L: `test_88_composition_consumption_retry_and_frozen_policy`. |
| 36 | No consumption cost for excluded components | PASS | Same L test verifies cost176 for two units and unchanged excluded bag stocks. |
| 37 | No unsupported reduction of fixed purchase price | PASS | C: `test_v2_fixed_purchase_price_exclusion_no_extra_resource`, `test_v2_supplier_omission_requires_evidence_and_foreign_override_rejected`. |
| 38 | Preview creates no real movements | PASS | C: `test_v2_preview_projects_split_without_writing_or_mutating_snapshot`, `test_v2_preview_unknown_source_reports_needs_source_without_writes`; database before/after equality, detached inventory lots and `pin=False` are asserted. |
| 39 | Order snapshot | PASS | F: `test_intake_snapshot_freezes_before_reservation`, `test_nested_snapshot_cost_survives_catalog_changes_and_replay`; L: `test_88_composition_consumption_retry_and_frozen_policy`. |
| 40 | Cancellation releases reservations correctly | PASS | L: `test_cancel_before_consumption_releases_without_touching_exclusions`. Repeated cancellation has no stock mutation and consumption is rejected afterward. |
| 41 | Webhook retry does not duplicate movements | PASS | F: `test_verified_salla_webhook_replay_keeps_single_bundle_unit`; L: `test_88_composition_consumption_retry_and_frozen_policy`. |
| 42 | Ordinary products unaffected | PASS | Scoped regression: C `test_ordinary_product_ignores_option_answers`, `test_v2_nested_composite_native_exclusion_is_local`; coordinator-reported broader regression331 includes ordinary stock paths. This is tested scope, not a claim of exhaustive coverage. |
| 43 | Employee/preparation pages unaffected | PASS | Scoped regression: F `test_operational_view_hides_costs_without_cost_permission`, `test_bundle_parent_excluded_from_legacy_supplier_catalog_and_stale_pieces`; coordinator-reported frontend76 including operations/order-details and broader backend331. No live employee business actions were needed. |
| 44 | Existing Salla integration unaffected | PASS | F: `test_verified_salla_webhook_replay_keeps_single_bundle_unit`, `test_pending_payment_lifecycle_captures_intake_before_acceptance`; C: `test_hidden_required_salla_option`. Verified isolated authenticated webhook/lifecycle regression; no live Salla write is required or claimed. |
| 45 | No unauthorized financial entries | PASS | P: `test_real_policy_transaction_commits_both_profiles_preserves_product_data`, `test_real_policy_second_profile_failure_rolls_back_first_profile`, `test_real_policy_foreign_supplier_rejection_has_zero_writes`; no journal entry is created by policy editing. Existing restricted operational transaction/receipt paths remain authoritative for fulfillment. |

## Verified main example

Hoodie base50 + necklace base30 + retained necklace box3 + shared wrap5 = **88 per bundle**. The hoodie bag2 and necklace bag1 are excluded from executable requirements. The real Mongo lifecycle test reserves two sale units after an amendment, consumes **176**, preserves both excluded bag balances, and verifies repeated consumption is idempotent. The original product bindings are unchanged. Frozen policy and cost evidence survives later source profile edits.

## Data and runtime controls

- Definitions stay in `mezan_bundle_definitions_v2`. Schema2 is opt-in; schema1 and frozen historical behavior remain supported. Rule IDs and per-rule `component_overrides` distinguish intentional quantities from duplicate rule execution.
- `mezan_product_cost_profiles_v2` gains `composition_components` and composition evidence. Existing `base_cost` / `variant_costs` are preserved.
- `mezan_product_operation_profiles_v2` gains revisioned `bundle_sourcing`. Source product bindings and source product records are not rewritten by bundle composition editing.
- No new inventory, supplier, or financial collection is introduced for this feature. Existing native reservations, receipts, ownership boundaries, and fulfillment transactions remain in use. No bulk backfill is performed.
- The narrow `bundle_policy` operational transaction profile can write only the two existing policy/profile collections. Tests prove atomic rollback and same-revision concurrency protection.
- Policy reads/writes require `products.cost.read` / `products.cost.write` and resolve the permitted owner. Suppliers must be active, canonical Product V2 supplier identities belonging to that owner.
- Runtime allocation freezes the policy, matches existing variants/exact text specifications, and can split stock/supplier demand only under the policy. Preview invokes the allocator on detached lots and never persists reservations or purchases.
- Text customizations accept text/textarea/long_text (historical missing type retained), not file/date/time/number fields. No free-text stock variant is invented.

## Implementation surfaces

- `backend/bundle_catalog_v2.py`: V2 rule identity, component overrides, provenance/cost decisions, mappings, frozen metadata, component details endpoint, read-only sourcing preview.
- `backend/product_bundle_policy_routes.py`: existing-profile policy endpoints, evidence/type/ownership validation and optimistic revision.
- `backend/bundle_sourcing_v2.py`, `backend/bundle_fulfillment_v2.py`: automatic source rules, native stock/supplier segments, receipt/retry/amendment handling.
- `backend/operational_atomic.py`: narrow policy write capability.
- `frontend/src/components/products/BundleEditor.jsx`, `BundleProductPolicy.jsx`: direct picker addition, separate product/component cards, native exclusions, stable customer-choice mappings, explicit policy editing and preview diagnostics.
- `frontend/src/components/orders/BundleOperationalPanel.jsx`: operational source/cost presentation; verify current exact diff before release.
- Tests are listed in the matrix plus associated policy/operational UI tests. This overview is not a substitute for the final Git diff.

## Remaining limits and required final evidence

1. Unknown cost inclusion or assembly ownership yields incomplete estimated cost and blocks strict execution. The owner must supply trusted provenance; no implicit policy migration is performed.
2. Independently charged supplier components/services without a supported executable receipt workflow remain unresolved. A supplier-omission request is not treated as confirmed without policy evidence.
3. Inhouse components already included in an undivided base price remain unresolved when physical inventory cost allocation cannot be justified. The engine does not arbitrarily subtract packaging or silently double-charge it.
4. Shared per-bundle current-product component overrides must agree; conflicting decisions surface explicitly instead of being ignored.
5. All45 matrix entries have evidence for their scoped acceptance claims. Exact opposite-parent/opposite-option exclusions, three-product lifecycle, literal gold supplier order and ordinary linked different-name supplier lifecycle pass. Review additionally proved distinct customer selections retain distinct sourcing requirements while identical selections sum. No live production tests are required for these isolated acceptance claims.
6. Coordinator-reported visual2/3/4 previews are local. No production stock was used for testing, no live supplier order was sent, and no live Salla business-write UAT is claimed.
7. Six screenshots are retained, including `current-product-six.png` proving sale3 x selection2 =6. No release identity was frozen for this extension, no production lease was created, and no deployment is claimed. Owner approval is required before publication.


Final CI result: code-equivalent documentation checkpoint d49047ccaa9de24377b7092544182622d41f412d passed all PR checks, including Frontend build39048678179, full frontend/security39048678301 and G47 integration39048678368. Operational Balance39048678203 initially failed package download with registry502; failed-job rerun passed without source changes. No known source/test failures remain. Release intent is not frozen and production approval remains required. Source code remains5ee379556c8a05f75a2f77e1727afaa87aac0de5; subsequent changes are documentation only.
