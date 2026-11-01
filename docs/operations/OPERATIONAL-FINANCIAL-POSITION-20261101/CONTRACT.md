# Operational financial position report contract

User approved the three coordinated table designs and requested parallel implementation. Production publication must follow the other task's product-cost release, fresh integration, tests, and the repository's v5 release protocol.

## Source and boundaries

- Read-only `GET /operational-balances/financial-position` uses existing operational report permissions and the current owner-scoped state. It never refreshes sources, posts accounting entries, or accepts financial writes.
- `operational_balance_service.report(state)` remains the current balance authority. The optional `operational_open_cost_exposure.project(db, owner, state, as_of=...)` supplies the approved disjoint pre-start/current cost-stage projection after its prerequisite becomes available. Absence or incomplete evidence remains visible.
- Do not add historical or branch filters: the current snapshot has no historical reconstruction contract. Display the authoritative time; filter currency only. Do not combine currencies or invent FX.
- Previous supplier invoice fix is preserved. No new cost logic belongs in the source collector or engine during this parallel task.
- Money is an exact fixed-two-decimal string or null. Missing evidence is never zero. Numeric totals come from the backend only.
- Current monetary position excludes prepaid advertising and employee custody; show these separately in a second all-displayed-assets subtotal. Do not count them again as collectible debts.
- Confirmed provider capture receivables are gross; remaining estimated fees are a distinct forecast deduction. Never subtract fees twice from already-net provider metrics.
- Expected product costs distinguish unreceived from received-but-uninvoiced. Use incoming cost-exposure stage buckets, not aggregate `current_supplier_expected` in addition to those buckets.
- Expected profit needs a complete order cohort, net sales and applicable tax, cost, consumed advertising, salary, operating, financing and tax evidence. No native GL/legacy fallback or balance-as-profit calculation. Unsupported profit lines and final profit stay null with a visible explanation.

## JSON v1

Top-level fields: `schema_version: 1`, `authority: operational_balance_report`, `status`, `started_at`, `as_of`, `generated_at`, `currency_order` (array), `currencies` (map), `issues` (array of code/message objects), `dependencies` (array of code/status/message objects). The route adds `session_scope` and `permissions: {reports: true}`.

Each `currencies[currency]` object has:

- `tables`: ordered array using the table shape below. Section is one of `actual`, `fulfillment`, `forecast`.
- `debt_rows`: `{id, name, receivable, payable}` rows followed by backend-provided `debt_totals: {receivable, payable}`.
- `current_rows`, `forecast_rows`, `profit_rows`: ordered `{id, label, amount, emphasis?, reason?}` entries. `emphasis` is `total`, `positive`, `negative`, or absent. Do not re-sum these display rows in the browser.
- `metrics`: `{liquidity, debts_receivable, debts_payable, monetary_position, prepaid_ad_credit, employee_custody, displayed_position, forecast_change, forecast_monetary_position, operating_profit, net_profit}`; strings or null.
- `completeness`: `{actual: boolean, forecast: boolean, profit: boolean}`.
- `notes`: array of readable Arabic strings.
- `deliveries`: `{complete: boolean, rows: [], notes: []}`. Each order row has stable `id`, `order_id`, `order_number` (null when not proven), `party_id`, `party_type`, `party_name`, `delivered_at`, `payment_method`, `collection_destination`, `collection_status`, `shipping_confirmed`, `shipping_settled`, `shipping_outstanding`, `collection_amount`, `driver_custody`, `collection_settled`, `collection_outstanding`, `store_collection`, `customer_outstanding`. Unknown fields remain null; never infer receipt approval from a mere order status.

Table shape:

```
{id, title, section, kind, columns, rows, totals, complete, notes}
```

`kind`: `actual` or `forecast`. `columns`: `{key,label,type,role?}` where type is `text`, `money`, or `count`; role may be `receivable`, `payable`, or `neutral`. Row keys match columns, with additional stable `id`. `totals` uses the same keys and is backend-provided. `notes` is an array of Arabic strings.

Required actual/fulfillment table IDs: `banks_network`, `cash`, `suppliers`, `employees`, `external`, `ads`, `employee_custody`, `couriers`, `drivers`, `providers`.

Required forecast table IDs: `shipping_expected`, `products_unreceived`, `products_received_uninvoiced`, `cod_expected`, `provider_fees_expected`. Additional future obligations may be exposed separately when required to explain the stated forecast scope. Group counts must not double-count overlapping orders/components; use null when an exact unique count is unavailable.

## User's delivery clarification

Show per-order earned shipping fees separately from collection responsibility. Cash collected by a store driver belongs in driver custody. Bank/card receipts for the merchant must not become driver cash custody; pending review remains pending until existing approval evidence confirms it. Carrier COD stays due from the carrier until settlement. Confirmed fees require the actual delivered executor and its existing fee contract. The UI must show these conditions in Arabic and include detailed rows in export.

This report does not change recognition timing. Driver delivery saves its earning and collection evidence during successful delivery. The central operational worker refreshes active owners sequentially and sleeps 30 seconds after each completed cycle; the central balance is therefore not synchronous with pressing Delivered and has no guaranteed 30-second SLA. The report refresh button reads the last saved snapshot and displays its real `as_of`.

Public order references are enriched by an owner-scoped, identity-only bounded read of `unified_orders`. Missing, ambiguous, stale or truncated identity evidence leaves `order_number` null without modifying money. The screen labels a retained `order_id` explicitly as an identifier, and Excel keeps identifiers and public order numbers in separate columns.

`other_balances` is an optional actual table for residual recorded obligations, including supported expense and owner-related balances. It participates once in debt totals; it must not be dropped because its party type is outside the named tables.

Known optional cost-source boundary rejections with exact codes `operational_source_rejected`, `operational_source_scope_too_large` and `operational_source_owner_mismatch` preserve the saved actual report while withholding cost forecasts. Unexpected reader errors propagate; there is no general silent fallback.

## Frontend

Single scrollable RTL route `/operational-balances/financial-position`, under the existing protected layout. Match approved dense bordered tables, blue actual headers, amber forecast headers, visible per-currency subtotals and separate expected profit. Reuse existing fonts, Phosphor icons, react-to-print and safeSpreadsheet. No illustrative production records. Provide refresh, currency selection, search/filter for delivery details, report-only print, and workbook export. Empty, loading, permission, missing-source, and failed-refresh states must be truthful.
