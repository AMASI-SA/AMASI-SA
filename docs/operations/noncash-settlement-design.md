# Operational carrier settlements: proposed command

Status: design for a separate accounting change. No noncash posting is enabled by
the unified workspace release. Existing carrier actions record actual money
received or paid. A signed net balance is a display calculation, not a settlement.

## Existing accounting contract

The operational saved aggregate already holds confirmed carrier COD receivables,
outbound shipping liabilities, reverse-shipping liabilities, claim allocations,
cash movements and separate unconfirmed estimates. Return freight is identified
by `return_case_id` or `salla_return_shipment_id`; a cancelled order alone is not
return-freight evidence. Previously confirmed product and freight costs survive
cancellation. Forecast money must not settle a confirmed liability.

`record_movement` requires allocations to match a single direction. It cannot
settle a COD receivable and a shipping payable together. Account-level movements
can reduce an aggregate balance without attributing the reduction to individual
claims. The new read-only `courier_summary` therefore returns `null` for affected
COD/fee settled and remaining breakdowns, while retaining the canonical company
receivable and payable. It never assigns old payments to arbitrary orders.

The summary is cumulative for the same saved `revision` and `as_of` as its ledger,
not a subtotal of its page, search or selected period. `expected_cod`,
`expected_shipping` and `expected_return` cover saved assigned obligations only;
they do not claim complete source coverage. Confirmed carrier responsibility does
not itself prove that the carrier collected cash from the customer.

## Proposed user flow

Open a carrier and choose **Reconcile carrier statement**. Enter its statement
reference and evidence, select confirmed COD claims and confirmed freight claims,
then review gross collections allocated, outbound freight, return freight,
documented adjustments, offset, and cash actually received or paid. The preview
must explain every difference and show the remaining balances. Commit only the
reviewed command. No automatic offset occurs merely because both sides exist.

Example: allocate SAR 1,000 of COD responsibility and SAR 100 of confirmed freight,
then record the actual SAR 900 bank receipt. The COD and freight remainders become
zero; bank cash increases by SAR 900 exactly. Freight expense is not charged a
second time. A full noncash offset can have no bank leg. An offset must not be
described as cash received from the carrier.

## Command and evidence

Proposed dedicated operational command fields:

- `request_id`, `expected_session_scope`, `expected_revision`.
- Carrier identity, currency, business date and statement reference.
- Owner-scoped evidence receipt identifier and immutable evidence hash.
- Explicit receivable and payable allocations, each with obligation ID and amount.
- Optional actual bank/cash account, direction and amount.
- Explanation for a separately authorized adjustment; no implicit balancing entry.

All allocations must identify the same carrier and currency and be within current
confirmed outstanding amounts. Already paid prepaid freight is not selectable.
Expected shipping or potential COD cannot be selected. The preview and commit
must bind to the same evidence and accounting revision. Repeated submission must
return the original result, and concurrent changes must fail before any leg posts.

For a net incoming settlement, allocated COD equals allocated freight offset plus
actual incoming cash. For a net outgoing settlement, the reverse equation holds.
No artificial incoming/outgoing bank legs may be created to make the gross totals
appear settled. The reference/evidence cannot be consumed twice.

## Implementation boundary

Prefer one immutable settlement event in the operational aggregate with explicit
cash and noncash allocations, committed through its existing owner-scoped CAS and
idempotency mechanism. Do not use the legacy accounting ledger as a second source.

Before implementing, centralize the existing allocation readers. The following
currently sum `movement.allocations` directly and must recognize the same
settlement event exactly once:

- `operational_balance_service.report` and its COD settlement details.
- Engine allocation/reconciliation helpers and derived-credit handling.
- Shipping completion/review admission and immutable evidence checks.
- Operational activity, statement/running-balance and resource projections.
- Financial-position and profit readers that distinguish expense from settlement.
- Relevant packed-state/read manifests and read-runtime bounds.

The event must preserve cash received, offset applied and attributed allocations
as different amounts. The existing `cod_reports.settled` incoming-movement sum
cannot simply be extended and still be labelled cash received. Reversal needs an
explicit compensating event with all prior allocations retained for audit; deleting
old movements or changing source orders is not an acceptable undo mechanism.

This surface is broader than a form change. Do not ship a partial implementation
that updates one report while leaving other allocation readers unaware.

## Required verification

Use isolated synthetic data only. Test partial offsets, zero-cash offsets, incoming
and outgoing net settlement, mixed outbound/reverse freight, existing prepayments,
old openings, unknown payment attribution, and unsupported/ambiguous evidence.
Assert owner, resource, currency, operation and bank permissions at preview and
commit; protect session/revision changes and reference replays. Include concurrent
attempts against the same claim, repeat request IDs with changed payloads, failed
CAS with no partial cash effect, and reversals. Verify bank cash and COD/freight
balances across the ledger, financial position and reports, with expense charged
once and no artificial profit. Cancellation must retain already incurred costs.

No production receipts, debts, source order statuses or old ledger entries are to
be modified as a test of this design.
