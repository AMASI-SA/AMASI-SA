# Operational financial position report contract

User approved the three coordinated table designs and requested parallel implementation. Production publication must follow the other task's product-cost release, fresh integration, tests, and the repository's v5 release protocol.

## Source and boundaries

- Read-only `GET /operational-balances/financial-position` uses existing operational report permissions and the current owner-scoped state. It never refreshes sources, posts accounting entries, or accepts financial writes.
- `operational_balance_service.report(state)` remains the current balance authority. The optional `operational_open_cost_exposure.project(db, owner, state, as_of=...)` supplies the approved disjoint pre-start/current cost-stage projection after its prerequisite becomes available. Absence or incomplete evidence remains visible.
- Do not add historical or branch filters: the current snapshot has no historical reconstruction contract. Display the authoritative time; filter currency only. Do not combine currencies or invent FX.
- Previous supplier invoice fix is preserved. Product-cost recognition remains owned by the prerequisite task. The initial parallel report slice does not change cost recognition; the delivery continuation below is a separate, tested recognition change.
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

## Delivery continuation — event cutoff

The user requested continuation after the report checkpoint. The next recognition slice addresses old customer orders delivered after operational startup. A separate normalized `delivery_events` stream may contain those deliveries; old orders remain excluded from `orders`, so their product estimates, provider captures and automatic order-bank credits cannot be replayed.

- Require a unique owner-scoped canonical order and actual executor, explicit delivery timestamp, and immutable assignment/shipment identity. An unrelated order update timestamp is not delivery evidence.
- Opening balances describe the position at `started_at`. Only an actual delivery strictly after that instant and no later than the saved calculation time enters this new stream. Deliveries before or exactly at startup remain opening history. Do not match or subtract arbitrary party opening amounts as if they were per-order allocations.
- Keep existing shipping/COD obligation identities and immutable confirmed facts. Store delivery provenance separately, so a changed executor, event or timestamp cannot reassign a confirmed balance. Missing evidence must leave a visible issue while retaining previously confirmed money and settlements.
- Existing approved fee contracts remain the fee authority and are evaluated at the proven delivery time. Driver application earnings are not a second payable. The new stream initially supports the native SAR shipping contract; other currencies require explicit support and must not be relabeled or converted.
- Driver cash responsibility and carrier COD remain receivables. Driver card/bank proof stays separate from cash custody. This slice must not invent receiving-bank/POS movements, approve evidence, or call a native accounting writer.

Acceptance includes source-to-reducer-to-report checks for old/new delivery dates, exact startup boundary, missing/ambiguous evidence, partial prepayment, all collection methods, effective fee dates, owner/currency isolation, repeated refreshes, preserved settlements and changed-source conflicts. Publication remains held for the prerequisite release and fresh combined verification.

## Operational noncash driver review

The existing driver payment review page and endpoint dispatch explicitly by financial phase. Native approval retains its existing SessionDatabase, P02, pause and posting guards. Operational approval uses a separate, narrowly scoped Mongo transaction; it does not extend `operational_atomic` profiles or write native journals. The transaction serializes on the existing owner revision fence, revalidates current accountant authorization and active operational/native-inactive phase, and commits the aggregate, immutable decision, evidence consumption and review/collection status together.

- Operational requests pin `expected_financial_source: operational`, `expected_review_revision` and `expected_session_scope` returned by the per-review approval-options endpoint. Stale phase/revision/scope requests fail. A sealed completed decision replays through its original backend, including after a later phase transition; a changed request is not a retry.
- Require one exact canonical owner/order/assignment/collection, positive SAR amount and bound receipt bytes/hash. Delivery, collection and receipt binding must be strictly after operational startup. No prior native settlement, prior operational order receipt, reused receipt or transaction reference may be replayed.
- Bank transfers require an explicitly selected active native bank identity matching the submitted review and the original imported bank-statement bytes, row, direction, amount and reference. A date-only statement row must have a Riyadh business date strictly later than the startup day. Earlier or same-day rows remain blocked with `operational_cutover_reconciliation_required`; this feature supplies no manual timestamp override or inferred opening coverage.
- Card approval requires the accountant to enter the exact receipt amount and select an existing documented SAR receivable. It creates a confirmed POS receivable and no bank movement or driver cash custody. A later existing operational settlement must allocate to that exact obligation; approval is not evidence of bank arrival.
- Bank approval appends one bank-owned incoming collection with no driver allocation. POS approval inserts one confirmed `driver_payment_review` obligation for party type `pos_receivable`. Neither approval changes shipping fees, sales, tax, provider captures or supplier cost recognition. Rejection has no monetary effect; approval reversal needs a separate contract.
- Accepted approvals are immutable `state.driver_payment_review_facts[event_id]` records: `id`, `financial_source`, `review_id`, `review_revision`, `assignment_id`, `order_id`, `order_number`, `driver_id`, `source_collection_id`, `payment_method`, `amount`, `currency`, `destination_id`, `destination_type`, `destination_name`, `source_namespace`, `source_record_id`, `source_revision`, `receipt_reference`, `receipt_hash`, `collection_at`, `delivered_at`, `approved_at`, `approved_by`, `movement_id`, `obligation_id`, `bank_movement_id`, `bank_movement_date`, `transaction_reference`, and `seal`. `validated_review_facts(state)` validates these records before reporting receiving-account linkage. Money remains authoritative in the existing aggregate movements/obligations.
- Bank movement IDs are `driver-review:<event_id>`; POS obligation IDs are `pos:driver-review:<event_id>`. Source collection and review references are retained on their financial effect. Ordinary refresh preserves the sealed facts and confirmed POS obligation. Imported bank rows become `operational_consumed` with an operational event ID, so native unclassified-row readers cannot consume them again.
- Review, receipt and collection metadata identify `financial_source: operational`, an operational event/revision, and an operational approval status. No native journal ID or native posted status is fabricated. Source `payment_confirmed` is updated only atomically with the approved aggregate effect.

Acceptance requires real local Mongo rollback, concurrent retry/deduplication and phase-boundary checks; proof tamper, owner, actor, currency, stale revision, opening cutoff and manual/automatic prior-payment checks; native-path preservation; and the existing page showing its actual phase and exact eligible destinations. No production data or publication belongs to this implementation step.
