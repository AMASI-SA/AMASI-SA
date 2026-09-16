# Amasi test-order execution plan

## Owner decision — 2026-09-16

The owner explicitly accepts testing order-item mutations against the real
Amasi Salla store using newly created disposable test orders. A separate Demo
Store is no longer the only accepted test environment. This authorizes the
test-order scenario; it does not identify any existing customer order as a
test fixture or authorize general feature activation.

No Amasi test order has been identified or mutated in this task yet. No live
test evidence exists. The first required input is the reference of a new,
unpaid test order containing a product with customer options. Resolve that
reference to an exact Salla store/order/item identity before execution; keep
filled fixture identifiers and customer details outside GitHub.

## Implementation boundary

The existing CLI verifies that `/store/info` reports `type=demo`. It still
rejects a real store and must not be used with a fabricated Demo identity.
Neither an Amasi test-order executor nor the Mezan order-page controls are
implemented. This document is an execution plan, not a runnable capability.

A dedicated live-test path must bind the verified Amasi store and exact test
order/product/item/option identifiers, preserve one-attempt locking and
before/after evidence, and reject any order outside that fixture list. Retain
the current Demo path and its tests. Do not enable a store-wide write switch
or send ad-hoc provider writes to bypass the existing executor.

## First sequence

Start with one unpaid, unshipped disposable order and a controlled test
contact. Inspect the applicable shipping, notification and Qoyod automation
before creating or mutating it; a test label alone does not suppress those
systems. Do not claim downstream isolation until it has been observed.

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
