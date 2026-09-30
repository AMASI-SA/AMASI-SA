# Track F — native V2 shipping, couriers, store drivers and COD

Review delivery: **MZ2_SHIPPING_COURIER_COD_V2_READY_FOR_REVIEW**.
This is source readiness for a Draft PR. Runtime P02 activation, bank integration,
opening approval and release are separate gates and are not performed here.
The final user policy supersedes the earlier proposal to require an external
collection receipt: canonical Salla **delivered / تم التوصيل is sufficient**.

## Source and isolation

- Repository: AMASI-SA/AMASI-SA; delivery record: issue #1006.
- Fresh production base HEAD: `5a7b44b71c6c9974aba358493b3267a47d6e6314`.
- Production base TREE: `87f9a44af5dc7d00fbc62417fb17e2f6273d8d93`.
- This includes merged [PR #1211](https://github.com/AMASI-SA/AMASI-SA/pull/1211).
  Reviewed PR head: `d910b455efa03691db18dd7b3044bcaa2426489e`; same tree.
- Branch: `codex/mz2-shipping-courier-cod-accounting-20260930`.
- Worktree: `C:/Users/amasi/.codex/worktrees/mz2-track-f-20260930`.
- Track A [PR #1212](https://github.com/AMASI-SA/AMASI-SA/pull/1212) was inspected
  at `92eda46c71e039800a7e91db8a814e5b86161b74`; no copied/cherry-picked resolver.
- Final F HEAD/TREE, Draft PR and CI links are recorded in issue #1006 outside
  this file to avoid a self-referencing commit identity.

These are Git source facts, not a fresh claim about deployed replica identity.
No live database was queried. Tests use synthetic isolated local databases.

## Production source/provenance trace

| Concern | Existing production source | Authority / Track F decision |
| --- | --- | --- |
| Canonical Salla facts | `order_engine/mapper.py`, `order_engine/repository.py` | Read the exact owner/order provider snapshot `raw_by_source.salla_direct` through the repository and canonical mapper. Root `unified_orders` aggregates, settings, fees and aliases are not financial SSOT. Validate raw decimal fields rather than accepting DTO float/default zero. |
| Accepted source ordering | `fulfillment_v2_routes.persist_component_source_snapshot`, callers in Salla webhook and Order Engine refresh | Existing operational transaction/watermark rejects stale/cancelled/conflicting snapshots. New observer runs **after** that transaction commits. Duplicate snapshots reattempt the same sealed recognition. |
| External COD old candidate | `accounting_salla_order_evidence.py`, `accounting_shipping_contract_service.py`, `accounting_shipping_evidence.py` | Old path was `waiting_p02_cod`, required an existing debit for reclassification and had `shipping_evidence_service_not_ready`. New delivered recognition does not call this service. Old guards remain intact; no Legacy fallback. |
| Courier catalog | `accounting_courier_bank_routes.py` | Settings/default names are display/catalog inputs only. New owner-confirmed exact registry supplies financial authority. |
| Opening identity | `accounting_onboarding_identities.py`, `accounting_module_opening_balances.py` | Reuse courier `cod_receivable` / `payable` and driver `cod_receivable` / `delivery_fee_payable`. Add confirmed registry identities to the existing Opening catalog; approved old MZ2 identities remain visible without authorizing new F recognition. |
| Driver operational evidence (#1211) | `store_delivery_driver_app_routes.py` delivered action | Existing assignment, cash collection, bound proof and exact `store_drivers.id`, all owner scoped. Rows marked `operational_only` / `pending_mz2_driver_balance_link` are input evidence, never balances. Existing backend is preserved. |
| Driver rates | `store_delivery_domain.py` | Mutable driver/assignment fee snapshots do not authorize financial fees. Use the separately confirmed effective-dated MZ2 contract. |
| Old fee and settlement writers | `accounting_shipping_p02.py`, `accounting_shipping_settlements.py`, `ledger_core.py` | Tagged MZ2 source still writes Legacy `general_ledger`. New F never invokes these writers or imports the old settlement implementation. |
| Native ledger and reports | `accounting_ledger_v2.py`, `accounting_mz2_reports.py` | Require V2 writer state before the shared report boundary; verify active Opening and every journal. New public verified journal-metadata API preserves order linkage for prior-sale detection. Physical V2 storage remains owned by the ledger module. |
| Bank/cash identity | Track A `require_financial_ledger_identity` | Unconnected port only in F. No settings/account/counterparty resolver. |

## Narrow owner-confirmed setup contract

`mz2_shipping_setup_v2` is one owner-scoped CAS document (`_id=user_id`). It
contains courier identities, rate versions, intended bank bindings, request
fingerprints and audit together. This is intentionally one atomic aggregate
rather than three partially committed registries.

- Courier: exact `courier_key`, display name, explicitly confirmed exact Salla
  source carrier keys, active status, version, `confirmed_by`, `confirmed_at`.
  No fuzzy matching or automatic approval of the string SMSA/سمسا. Source keys
  cannot overlap across couriers. Store delivery/pickup keys are not couriers.
- Driver: exact active owner-scoped `store_drivers.id`; no counterparty fallback.
- Rate: party type/ID, service `context`, `[effective_from,effective_to)`, SAR,
  delivery fee, COD fixed fee and percentage (2 means 2%), VAT percentage,
  VAT included flag, gross expense/no input VAT or net plus input VAT,
  contract reference, approved status, version/actor/time and audit.
- No default financial rate. Missing/overlapping/unapproved rate fails closed.
  Rate intervals are append-only; an open-ended version cannot silently be
  replaced by an overlapping policy. Existing recognized costs never reprice.
- Binding: exact intended Track A financial account ID, confirmation and audit.
  Saving a binding is not validation of that bank identity.
- Owner-only setup accepts `request_id`, expected `version`, `confirmed=true`,
  reason and typed fields. Repeated identical requests are idempotent; changed
  payloads conflict. Concurrent stale CAS writes return an exact conflict and
  can safely retry. The aggregate has an explicit 1,000-version scope limit.
- Setup does not call financial atomic transactions or alter pause/P02 controls.
  Every financial action pins setup in its owner transaction.

## Delivered evidence contract and recognition

`mz2_courier_delivery_evidence_v1` is a sealed delivery-evidence envelope with
explicit `party_type` and `operational_source`. External and store-driver
adapters are separate. The shared per-owner/order key prevents recognition
of the same order once as courier and again as driver.

Each record stores owner, exact party ID, order ID/number/creation time,
delivery event time, payment method, exact COD amount, sale total, currency,
operational source, source record ID/revision/hash, canonical delivered status,
provenance status, version, created/recognized actor/time, economic hash,
recognition mode and verified journal ID. A SHA-256 seal covers the complete
record; no update/delete endpoint exists. Retry verifies the seal and economic
identity. A changed courier, order, payment or amount requires reconciliation.

External evidence requires POST-CUTOVER order creation, canonical COD,
confirmed exact courier, canonical delivered, unambiguous decimal amounts and
explicit SAR currency. `completed`, تم التنفيذ, delivering, processing and
reviewed are not delivered. Cancelled/refunded/conflicting source facts fail.
No receipt, image, uploaded proof, manual delivery confirmation, old evidence
service or prior bank transfer is consulted.

The COD amount is the provider-reported remaining amount where present, or
source total less explicit paid amount. All supported raw root/payment/
`payment_actions.remaining_action`/refund-action paid facts must agree.
DTO-synthesized zero is never evidence. Missing/ambiguous/nonpositive due fails
closed. Missing explicit delivery time uses the accepted source status revision
time, never local order insertion time. Dates must be timezone-aware and sane.

Driver COD separately requires the #1211 delivered assignment, cash collection,
exact owner/driver/order, matching amount/custody amount and bound proof. It
pins these records and the canonical provider snapshot in the transaction.
External couriers never read these driver collections. Driver evidence can be
submitted through the native driver action after source synchronization; this
patch does not turn #1211's existing operational completion into a GL writer.

The automatic external observer persists pending failures and exact codes in
`mz2_shipping_inbox_v2`. Its attempt token prevents an older observer from
overwriting a newer result. Retry uses the same current canonical source and
financial gates; no historical bulk replay job or pause bypass exists.

## Journal legs (all native V2)

| Event | Debit | Credit |
| --- | --- | --- |
| First delivered COD sale (no prior sale) | courier/ID/cod_receivable, gross due | revenue/bnpl_sales, net; tax/sales_vat_payable, tax from approved MZ2 sales-tax policy |
| Sale previously recognized | courier/ID/cod_receivable, exact remaining due | Exact existing order-linked receivable, same amount; **no revenue or tax** |
| Store-driver COD | store_driver/ID/cod_receivable | Same first-sale or exact prior-sale rules |
| Courier fee | expense/shipping; tax/input_vat only under confirmed treatment | courier/ID/payable |
| Driver fee | expense/store_delivery; supported input VAT | store_driver/ID/delivery_fee_payable |
| Receive COD | Track A bank/cash ledger identity | courier or driver cod_receivable |
| Pay fee | courier payable or driver delivery_fee_payable | Track A bank/cash ledger identity |

First sale and COD debit share one V2 journal and owner transaction. Prior sale
lookup reads verified journal AND leg provenance. Multiple sale groups,
ambiguous pooled receivable credits or missing order allocation fail closed.
Exact allocated prepayments are supported: sale 500, paid 200 => transfer 300.
A prior sale already on the correct courier creates no second journal.

Fees are independent of COD: COD500 plus fee17.25 remains AR500 and AP17.25.
Missing fee policy does not undo/block valid delivered COD recognition.
Prepaid delivered orders can accrue delivery fees with zero COD and no sale
journal; COD-only fee components do not apply to prepaid orders.

Settlement consumes one unclassified owner-scoped native daily movement;
provider/receipt-assigned movements are rejected. Direction, explicit currency,
confirmed binding, canonical bank port, amount, date, available balance,
Opening coverage and open period are checked. No over-receive, excess fee
payment, silent netting, advance creation, revenue or fees at settlement.
A backdated movement cannot consume liability first recognized later.

## API and Stage 7 / 8 / 9

Base: `/accounting-module/shipping-v2` (under the application's API prefix).

- GET `/context`: precise readiness, missing contract/opening codes and locked bank port.
- POST `/couriers`, `/rates`, `/bindings`: setup-only CAS; works while paused.
- POST `/recognize-courier` (`order_number`), `/recognize-driver` (`assignment_id`).
- POST `/accrue-fee` (`evidence_id`), `/courier-delivery-fee`, `/driver-delivery-fee`.
- POST `/settlements`: `receive_cod` or `pay_fee`, party, movement ID, request ID, reason.
- GET `/statements/{kind}/{identity}`: verified V2-only AR, AP, collections,
  payments, recognized fees, entries and unexpected-account unreconciled legs.
- POST `/retry/{order_number}`: explicitly retry the durable canonical observer.

Stage 7 requires confirmed courier + approved effective delivery rate. Stage 8
requires its exact two Opening accounts. Stage 9 requires exact drivers,
approved rate and both driver Opening accounts. Missing identity/rate/opening
never passes because of Legacy evidence. Context reports missing driver scope
rather than silently treating it as not applicable. These backend contracts do
not certify an actual owner's unconfigured setup or add a frontend wizard.

Fresh authorization is checked inside all services. Recognition/accrual/
settlement run through `atomic_owner`; paused financial writes return 423.
P02 and V2 transition gates remain required. Setup/readiness never activate them.

## Remaining integration and exact fail-closed conditions

1. `mz2_shipping_bank_port_not_integrated` (503): final A+F integration must call
   `require_financial_ledger_identity` on the same transaction-bound DB with
   bank/cash + SAR constraints and return its canonical tuple unchanged.
   Local successful-settlement tests substitute this port **only in fixtures**.
2. P02 remains gated/locked, and approved V2 Opening, tax policy and open period
   remain prerequisites. No activation/release bypass is added.
3. `MZ2_COURIER_COD_COLLECTION_EVIDENCE_REQUIRED` only means no valid canonical
   Salla snapshot/order facts; it does not mean an upload is required.
4. `shipping_cod_amount_missing_or_ambiguous`, `shipping_canonical_identity_required`
   and `shipping_source_conflict` retain ambiguous source facts for correction.
5. A partially prepaid first sale without a previously recognized allocated
   sale returns `shipping_partial_sale_allocation_required`: F will not invent
   the missing bank/prepayment leg. Ambiguous prior-sale allocations use the
   `shipping_prior_*allocation*` codes. No second revenue is a workaround.
6. Historical refunds/reversals and retrospective rate replacement are not
   fabricated by this delivery observer. Changed sealed facts require their
   explicit reviewed correction contract.

## Verification and non-actions

The checked-in workflow `.github/workflows/mz2-track-f-contract.yml` runs the
native real-replica suite plus ledger, Opening, router, Order Engine, old locked
shipping and #1211 operational regressions. `VALIDATION.json` records the local
result and tested suite list; CI run/check links are in the PR/issue handoff.
Native tests monitor Mongo commands and assert zero reads/writes to Legacy
`general_ledger`, `accounts`, `counterparties`, `shipping_company_settings`.

Production financial writes = **0**. Merge = **NO**. Deploy = **NO**.
Opening Post = **NO production action** (synthetic isolated test fixtures only).
P02 Activation = **NO production action** (test DB flag only). Release Guard = **NOT RUN**.
