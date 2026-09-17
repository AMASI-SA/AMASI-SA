# Amasi test-order execution plan

## Owner decision — 2026-09-16

The owner explicitly accepts testing order-item mutations against the real
Amasi Salla store using newly created disposable test orders. A separate Demo
Store is no longer the only accepted test environment. This authorizes the
test-order scenario; it does not identify any existing customer order as a
test fixture or authorize general feature activation.

The owner supplied a disposable test-order reference, which was resolved to a
unique order using authenticated Salla reads. A later read establishes a
positive full unpaid balance. The owner clarified that delivery uses the
store's own courier, which does not have a tracking number. The initial
no-shipment predicate was too narrow for that approved disposable scenario;
changing carriers does not resolve it. The runner now has an explicit bound
pending-courier policy described below. A zero-value bank-method row is no
longer confused with a positive payment. The owner has now explicitly confirmed that the attached receipt image is
synthetic. The optional exact attachment policy below records that decision;
runtime configuration and fixture/downstream review still block live execution.
Customer options were observed in shipment packages only. No item mutation or
CLI readiness run was performed, and no live mutation evidence exists. Keep
all filled identifiers and customer details outside GitHub. Do not remove a
real receipt or assume it is synthetic simply because the order is for testing.

## Implementation boundary

The default CLI still verifies that `/store/info` reports `type=demo`.
The explicit `--environment amasi-test-orders` mode uses separate configuration,
manifest and evidence classification. It is implemented and verified with
synthetic I/O only; actual Amasi fixtures and live behavior remain unverified.
Mezan order-page controls are still not implemented.

The live-test path binds one exact order, its reference and controlled test
customer, the verified store ID and observed non-Demo store type, and allowed
product/item/option tuples. It uses the same canonical clone-local lock and
quarantine ledger as Demo execution, with an environment-bound one-use
capability. A final read under that lock must still match the reviewed baseline.
Redirects and automatic retries are rejected. Each invocation sends at most
one mutation and never exposes a public raw-token write transport.

## First sequence

Start with one unpaid, unshipped SAR bank-transfer order and a controlled test
contact. Inspect the applicable shipping, notification and Qoyod automation
before creating or mutating it; a test label alone does not suppress those
systems. Do not claim downstream isolation until it has been observed.

The initial mode supports only `pending` or `under_review`, quantity at most
two, and no direct price/cost/weight overrides. COD can qualify for automatic
fulfillment, so it is excluded. The order must have positive total, explicit
zero paid, remaining equal to total, no unreviewed receipt or contradictory payment/refund
facts, and either no shipments or one explicitly reviewed pending store-courier
record. The original line is protected from PUT and DELETE.
The mutation case must include independently reviewed before/after total
assertions; unchanged or incorrect totals cannot pass on item evidence alone.

1. Read Order Details, Order Items and the selected Product Details. Record
   exact IDs, quantities, selected options, totals, payment and inventory
   facts available from the existing read scopes.
2. Add one selected product with explicit options. Fetch the order and items
   again and verify the new line plus stability of all previous lines.
3. Change the added line's quantity, then its customer options as separate
   operations. Verify each requested value, source item identity and total.
4. Delete the added test line while retaining the original order contents.
   Verify that only that line disappeared and reconcile totals and inventory.
5. Correlate the resulting webhooks and verify what Mezan receives. Missing
   or ambiguous evidence must remain incomplete; never repeat a mutation
   merely to recover an event or a timed-out response.

Add another disposable fixture when replacement or a different product-option
type cannot be proved on the first order. Do not change a real customer's order
to fill a missing matrix cell. Preserve test evidence and inspect cleanup
effects before cancelling or otherwise changing the test order's state.

## Acceptance and remaining coverage

Classify results as real-store test-order evidence, never Demo evidence.
An unpaid-order pass proves only that tested state and those option types.
The 21-cell state/operation matrix, paid-order differences, partial payments,
inventory, variant changes and lost-response handling remain unproven until
their respective evidence is collected. The owner changed the permitted test
environment, not the meaning of a passing result.

P1's required provider evidence is still outstanding. General activation and
production deployment are separate from this controlled test authorization.

## Inputs and commands

Copy `.env.salla-amasi-test.example`, `fixtures/amasi-test-manifest.example.json`
and `fixtures/amasi-add-case.example.json` outside Git. The example totals are
illustrative, not defaults for an actual order. Fill the exact reviewed
identifiers/totals; `downstream_reviewed` starts false and is an operator
attestation, not an automated guarantee. Keep the encrypted credential resolver
and existing five-minute single-attempt review metadata; never copy a token.

For a disposable order assigned to the store's courier, optionally add this
object to its single manifest order row (identifiers stay private):

```json
"pending_store_courier": {
  "shipment_id": "FILL_REVIEWED_SHIPMENT_ID",
  "courier_id": "FILL_REVIEWED_STORE_COURIER_ID",
  "not_dispatched_confirmed": false
}
```

Set `not_dispatched_confirmed` to true only after the operator confirms that
this test order has not been handed over. Missing tracking is not such proof.
The runner additionally requires exactly one matching shipment/courier/order/
reference, complete pagination, `pending`, `shipment`, dashboard source, bank
payment, `trackable=false`, and explicitly null label/tracking/pickup/driver/
route fields. Populated label or tracking aliases and dispatch/delivery
timestamps are rejected. Order shipping status must be `shipping_ready`.
Removing, replacing or advancing the record blocks the operation; changing
carriers is not an alternative. Without the object, the no-shipment rule stays.

Shipment checks bracket the order/items reads and are repeated under the
one-attempt lock immediately before writing. Evidence records count one and
hashed bound shipment facts for this policy, never claims shipment absence.
These reads cannot make Salla state atomic or establish downstream isolation.
The payment-method list may be empty or exactly one bank row with explicit zero
amount and null provider/transaction reference; unknown fields or additional
rows are rejected. Other receipt fields and all positive-payment/refund guards remain.

Scopes are `orders.read_write`, product read, and additionally `shipping.read`
for this live mode. A declared scope is not proof it was granted: the actual
read must succeed. Store identity and non-Demo type were observed through the
authenticated connector. The executor must still confirm them fresh, and the
connector shipment read does not establish the encrypted runtime token scope.

```bash
python scripts/research/salla_order_item_contract_runner.py readiness \
  --environment amasi-test-orders
```

After fixture review, readiness and one-case review, enable the dedicated
`SALLA_AMASI_TEST_RUN_WRITES` opt-in and run:

```bash
python scripts/research/salla_order_item_contract_runner.py run \
  --environment amasi-test-orders \
  --case-file /private/reviewed-case.json \
  --webhook-events /private/atomic-case-events.json
```

After POST, use the observed new item ID for each PUT and DELETE case and the
manifest's target `item_id`; retain `preserve_item_id` for the original line.
DELETE uses an empty body and empty steps. No fabricated replacement proof is
needed to remove the added line while preserving the original. Each subsequent
case needs fresh expected totals, correlation and one-attempt metadata.
CLI exit zero means evidence saved, not that the case passed. Inspect both
`observed_verdict` and final `verdict`. A missing webhook is inconclusive; an
uncertain write/verification quarantines the order without replay.

## Source and evidence limits

[Order Details](https://docs.salla.dev/5394147e0) exposes payment-action facts;
its light response does not establish shipment absence. The mode separately
uses [List Shipments](https://docs.salla.dev/shipments/list) with the exact order
ID, no status/type filter, and requires complete pagination for either zero
records or the one explicitly bound pending-courier record. The latter is a
reviewed test policy based on observed provider fields and owner confirmation,
not a claim that missing tracking proves non-dispatch.
[Store Information](https://docs.salla.dev/merchants/store-info) supplies
the identity/type match, and [payment methods](https://docs.salla.dev/payments/available-methods)
distinguishes `bank` and `cod`.

Current code inspection shows webhook ingestion can call fulfillment routing;
no generic test-order exclusion was found. Opening Order Details may remove
Salla's new-order indicator. These effects are confined to the selected fixture
by the executor, but surrounding automation still needs an actual review.
The test runner does not create orders, ship, capture/refund payments, write to
Qoyod, verify inventory conservation, or implement variant-changing runtime UI.
Those outcomes must never be inferred from an item-operation PASS.

## Owner-confirmed synthetic receipt

The owner confirmed the selected attachment is a test image, not evidence of
a real transfer. Do not delete it. Only in the explicit Amasi test mode, the
single private manifest order may include `synthetic_receipt` with
`owner_confirmed: true` and `receipt_image_sha256`: the runner's canonical
JSON SHA256 of the exact nonempty top-level `receipt_image` string. Do not
store its URL, hash or actual fixture identifiers in GitHub. This is a binding
to the provider attachment reference, not a downloaded image content hash.

The binding is checked at every order validation, including before a write.
A removed or replaced reference, another populated receipt/proof field, or
positive payment still blocks. The original image is never edited by the
runner. Acceptance does not prove no courier handoff or downstream isolation.

Fresh local verification: 101 isolated tests pass with full schema validation.
Live readiness returned `AMASI_TEST_NOT_CONFIGURED` (exit 2) in this session;
no live order-item mutation occurred. The current toolset provides connector
reads but no order-item write or authenticated cloud terminal/browser access.
Next: restore the configured encrypted runtime, complete the private manifest
and downstream review, then run readiness and one reviewed operation.

## Browser Console execution decision — 2026-09-17

The owner requires the live test through the production browser Console.
Use [CONSOLE-TEST.md](CONSOLE-TEST.md) for the new disabled HTTP bridge.
The CLI commands remain for synthetic regression and historical documentation;
do not run them against the live fixture. The HTTP bridge is not yet deployed.
