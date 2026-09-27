# MZ2-SPECIAL-ORDERS-001 — core R1

## Authority and frozen scope

Owner approved completing the nucleus before screen work and a later cautious release.
This checkpoint adds an isolated package only. It does NOT complete live integration,
register routes, create operational orders in a merchant database, touch Salla/Qoyod,
post journals, merge, activate, deploy, prepare Release Guard, or regenerate intent.

- Core branch: `feature/mezan-special-orders-core-20260927`.
- Pinned parent: `1cbeccd84d58fb3397dcd42822d5feb92de06275`.
- Earlier UI remains separately preserved in Draft PR #1167 at
  `1b4a775397607d0c63a1c89b721ae7db85f2ebb3`. It is not imported here.
- Existing frontend, Android, fulfillment and accounting source bytes are unchanged.
- New private collection contract: `mezan_special_orders_v1`.

## Implemented behavior

1. Four purposes: replacement, gift, creator and marketing. Replacement requires a
   tenant-scoped original order and exact original item links. Other purposes use
   tenant-scoped catalog snapshots. No invented Salla order IDs or copied payments.
2. Independent namespaced order/item identities, immutable customer/product schema
   snapshots, current options and separately preserved original options. Required,
   duplicate, unknown, invalid-choice and oversized options fail closed. Edits are
   possible while pending review and before the shared-workflow freeze handshake.
3. Courier requires a full address. Carrier requires a new private evidence object,
   carrier key and tracking number. Original label/number reuse is rejected. Evidence
   ownership, bytes/hash, MIME/scan and tracking metadata must be verified by the port.
4. Creation always starts pending_review. Purpose is a badge, never a separate
   fulfillment state machine. Existing workflow owns progression. Observations must
   match tenant, order, frozen source revision/digest and monotonic workflow revision.
5. Per-line agreed contribution for products, shipping and services; bank/COD plan
   must equal that sum. Actual collections are apportioned by agreed-charge weights
   with deterministic largest-remainder allocation, preserving exact totals.
6. Currency minor units are strict integers (SAR/QAR/AED/USD=2; KWD/BHD/OMR=3).
   No float money input. Original currency and verified FX evidence/snapshot remain.
   SAR rate is 1. Cumulative rounding reconciles agreed SAR line totals to the order.
7. Bank receipt upload/attachment is only a claim, not cash. Pending/rejected claims
   do not increase bank or paid amounts. The MZ2 adapter must read back an already
   posted, correctly allocated movement and exact bank/evidence/amount to confirm it.
8. COD customer collection, courier custody and bank remittance are separate facts.
   A remittance never counts as a second customer payment. Overcollection, excessive
   remittance/refund, unlinked refund and cross-tenant proofs fail closed. Cancellation
   retains history and costs; refunds require a parent collection and, outside closed
   orders, a verified credit reference. No positive debt is fabricated after refund.
9. Recognized cost facts refer to existing MZ2 inventory/supplier/carrier/service
   expense movements. This is NOT a second journal/receipt/payable implementation.
   Per-unit cost coverage prevents inventory plus supplier double-costing. Exact
   reversal preserves audit and allows a corrected fact. Missing cost is unknown,
   not free: reports stay provisional until every physical unit/charge target is covered.
10. Marketing/sales-order/AOV/ROAS-revenue exclusions are explicit policy fields, not
    zero-price heuristics. Creator/marketing gifts contribute to total marketing cost;
    compensation does not inflate paid-media spend. Paid Salla gifts are unaffected.
11. Creation replay and command replay are merchant-scoped; mismatched bodies conflict.
    Optimistic revision CAS stores aggregate/audit/outbox together. Mongo unique
    multikey index prevents movement reuse across local orders. Carrier tracking is
    unique within a merchant/carrier. Writes are bounded to 2 MB / 512 commands;
    capacity failure is explicit, with no silent truncation of financial history.
12. No production permissions are widened. Authenticated principal adapter constructs
    Actor; clients cannot supply tenant, stages, balances, server cost/proof fields.
    Operational readers receive delivery collection amounts, not bank evidence/costs.
13. Router factory provides create/list/detail/commands/outbox dispatch but is unmounted.
    Create/commands/dispatch gates default false. Pausing creation keeps existing records
    readable and does not hide or delete in-flight orders.
14. Salla remainder sidecar calculation distinguishes existing balance from an additional
    documented agreement. It does not add an existing remainder twice, change Salla's
    total/paid, create a second sale, or deduct already-reflected payments twice.
    This is a calculation contract, NOT a persisted live sidecar or posted receivable.

## Integration ports are deliberately NOT claimed implemented

`UnboundPorts` fails closed. TestPorts is synthetic. Binding callbacks alone is NOT
acceptance. The following production adapters and cross-engine gates remain required:

| Area | Required work / evidence |
| --- | --- |
| Source/catalog | Real canonical Order Engine + option schema retrieval, exact merchant authorization, batch limits, original revision and alternative variant identity. |
| Canonical DTO/repository | Existing provider Literal supports only salla in reviewed source. Add mezan explicitly and route reads/statuses without fake Salla payloads, preserving Salla paging/search semantics. |
| Shared review | Local source freeze/CAS and existing workflow revision must form a coherent handshake. Do not call Salla refresh or status update for a local order. Review failures must remain retryable. |
| Preparation/My Products | Reuse existing eligibility, piece IDs/quantities, assignments, stock route, supplier receiving, selected services/components; no parallel state machine. |
| Prints/labels | Use current local option snapshot in actual supplier PDFs, protect missing required options, retrieve private new label at assembly, show correct remaining COD on courier label. No old label reuse. |
| Delivery/app | Real Android string-ID parsing, search/barcodes, permissions, mixed queues, delivery confirmation, old-client compatibility and custody/settlement parity. No APK/OTA issued here. |
| Evidence/MZ2 | Verify private blob ownership/hash and posted ledger movement allocations. Receipt does not post money. Retain the existing MZ2 journal owner and posting idempotency. Tax/revenue classification requires approved accounting policy; excluding marketing KPIs does not mean all income/tax is zero. |
| Costs | Bind already recognized unit expenses; do not book inventory purchase and later inventory issue twice. Define supplier/carrier recoveries separately from responsibility tags. |
| Salla sidecar | Persist approved agreement and source refresh/reconciliation, bind verified payments and actual courier label/collection without changing Salla sale total or advertising count. |
| Outbox | Install existing-workflow consumer. Transport is at-least-once, NOT magical exactly-once; receiver must dedupe event_id and reconcile current source revision. Events are invalidation signals with a current projection, not historical snapshot writes. |
| KPI reports | Consume source policy in every relevant sales/ads/report/export path; validate numerator versus denominator separately and do not duplicate shipping in purpose totals. |
| Rollback | Do not roll back to an old reader that loses local orders. Stop new creation, preserve in-flight visibility, drain/repair outbox, test downgrade compatibility before any activation. |

## Acceptance boundary

Local package and HTTP tests do not prove existing fulfillment or real MZ2 parity.
Real-Mongo tests use only `MEZAN_SPECIAL_TEST_MONGO_URI` and disposable
`test_mezan_special_<uuid>` databases. Never reuse application's MONGO_URI.

Full acceptance requires baseline-vs-candidate regression on ordinary Salla orders
alongside all special purposes; all stages, actual supplier PDF/label rendering,
multiple employees, concurrency/retries, receipt review, COD partial remittance,
refunds/cancellation, costs/FX, actual marketing reports, Android and a rollback drill.
No live activation until these pass on the exact candidate tree and a separate
owner release authorization. Creating a Draft PR or passing nucleus tests is not release approval.

## Method references

- MongoDB single-document atomicity / expected-value filters:
  https://www.mongodb.com/docs/v8.0/core/write-operations-atomicity/
- Pydantic frozen models are not deep immutability; nested contract collections are
  tuples and repository boundary values are deep-copied:
  https://pydantic.dev/docs/validation/2.12/concepts/models/
