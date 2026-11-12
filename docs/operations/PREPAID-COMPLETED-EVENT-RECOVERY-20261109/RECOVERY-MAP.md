# Source-only recovery inventory

Compared productioncedd715 against preserved historicalde76aed. No live counts or
financial completeness certification. Do not merge the historical branch wholesale.

1. Product option identity: extract operational_balance_sources _native_option_id,
   _native_option_value_id/option_tokens/catalog hunks; direct surcharge quantity
   normalization from product_option_cost_routes. Tests sources, shared option costs,
   direct_option_cost_quantity. Keep preparation/service assignment unchanged.
2. Tabby: operational_provider_fees selection, operational_provider_plan_scope,
   approved scope loader in accounting_onboarding_ssot and collect_sources, settings
   routes; uniform and plan_scope tests. Read actual approved saved scope first;
   do not manufacture duplicate policies. Provider_summary + fee_comparison tests
   separately display tariff fee/net versus actual allocated settlement fee.
3. Table coverage: operational_financial_table_coverage affected_tables/table_coverage,
   core and wrapper integration; table_coverage and financial_position suites. Unknown
   failure blocks all; prepaid failure still blocks cash/courier. Scope source failures
   correctly rather than suppressing them. Indexed transport is an independent
   optimization; avoid duplicating diagnostics in every table before indexing.
4. Forecast: operational_cod_forecast_coverage plus open_customer_cod/prestart proofs,
   exposure/core integration and computed_dependencies tests. Match currency, snapshot,
   date and full canonical-order cohort before setting complete.
5. Profit: operational_profit_period/order_costs/period_expenses/returns plus exposure
   and core integration. Historical open_cost_exposure eagerly awaits profit collect
   whenever as_of matches engine timestamp. Measure and bound read cost before restore.
   Payroll needs complete employee/day cohort, same period, effective salary versions.
6. Imkan: current operational_customer_returns.attach_returns already sends only
   provider-source refunds to provider projection; bank refunds excluded. Preserve it.
   Historical profit_returns.current_total_is_net addresses already-net Salla sales,
   not bank posting. Test partial/full/retry and provider fee preservation independently.
7. Shipping: this patch addresses prepaid completion without label. Deferred still uses
   delivered event rules. Historicalde76aed did not remove label prerequisite either.
   Preserve idempotency and do not autoactivate historical charges without impact review.
