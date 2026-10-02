# C1–C3 SSOT delta audit

Audit date: 2026-10-02. Product checkpoint: `d4f2cce588c290a77b4e318604637dcb1a1ffa72`; tree: `1de5898efc491689669e1ce56dca92c1ef911d72`. Product working-tree diff against this checkpoint was empty before and after inspection. The untracked C3 browser harness was not treated as product evidence. This is a bounded source audit, not a claim about every historical application route.

## Conclusion

No unresolved material SSOT violation found in the reviewed C1–C3 delta. C1 adds reviewed metadata and connects existing rich-term calculation to the existing native fee writer. C2 is a native decision-history reader. C3 adds immutable driver observations and explicit evidence matching; neither is a financial journal writer. Native financial authority remains the existing V2 journals, canonical identities and gated writers. Operational observations and retained originals are evidence, not substitute financial ledgers.

## Entry → evidence → authority → sink

| Scope | Source and entry | Validation and sink | Financial boundary |
| --- | --- | --- | --- |
| C1 rich courier terms | Stage7 UI → native rich draft/review/approve/revoke routes → `accounting_shipping_native_rich_contracts.py:90` | Fresh explicit manage/review authority; canonical Native courier; exact request hash/version CAS; approved immutable term version and audit in existing owner setup. Retained originals resolved owner/file/hash/size in `accounting_shipping_native_contract_evidence.py:47`; distinct contract/shipping-tax/commission-tax review. | Draft/upload alone is not approval. No journal/control mutation in metadata save. Opening balances and bank opening remain separate. `legacy_copy` cannot be directly approved (rich_contracts.py153). |
| C1 fee use | Existing `accounting_shipping_native.py:213–257` selects rich contract | Existing `require_shipping_contract_charges` calculates approved terms; existing Native `_post` → `post_journal_v2`; owner setup/evidence pinned in same transaction. | Existing payable, expense and input-VAT identities. Independent VAT/commission values come from the delivered rich contract calculator, not invented UI defaults. Failed/revoked evidence rolls transaction back. Flat-rate branch remains separate. |
| C2 history | `accounting_driver_review_history.py:175` → owner-scoped `kind=driver_payment_review` events | Fresh shipping-view authority; bound pagination/filter cursor; `present:69` verifies approved event against sealed V2 journal metadata/group, canonical destination and reversal history; rejected event must have no journal. | Reads only; unknown/unlinked current decisions are coverage gaps. Pending queue is not history, and history is not physical cash. No opening balance inference. |
| C3 driver capture | Existing delivered route `store_delivery_driver_app_routes.py:1683`, cash confirmation validation before external transition at1714 | `store_delivery_delivery_commit.py:48` rechecks actor/profile, active assignment, canonical order amount, exact proof, then atomically records original expected collection/earning, actual cash evidence, assignment/proof/order/workflow/event. Evidence keys reuse collection ID. | `operational_atomic.py:33–37,209–272,297–310` bounds collections/methods/fields and disallows financial/control entrypoints. Actual cash never overwrites expected COD or posts variance/waiver/fee/netting entries. |
| C3 replay | Delivered-cash retry at route1703 → `cash_delivery_retry:19` | Same driver/owner/collection, actor, amount and proof required; returns immutable observation without a second capture. Driver UI retains exact payload/references after uncertain responses. | No new financial effect. Fresh actual response/readback remains necessary after forced reload; UI pending state is not persistent storage. |
| C3 custody | Driver and H2 read routes → `store_delivery_cash_evidence.py:184` | Sealed actual evidence; owner/driver/order bindings; current assignment/Salla snapshot eligibility; missing captures, unmatched sources and changed financial proofs explicitly reported. | Totals cover recorded observations only. No cash opening or complete historical balance is inferred; expected COD and physical cash remain separate. |
| C3 matching | H2 explicit source/allocation form → `save_reconciliation:267` | Fresh owner/accountant authority including persisted denials; exact sealed existing handover source; unique allocations, positive amounts, exact source total, remaining-actual bound, chronology, owner-serialized idempotency and source uniqueness. Insert-only evidence link. | Native handover verifies existing V2 bank/driver journal. Existing operational COD-remittance source is explicitly labelled operational-only, never upgraded to Native proof. No invocation of old remittance POST, no new settlement or journal. |

## Existing financial paths and guards

The delivered cash branch calls the original Native observer only after successful operational commit (`store_delivery_driver_app_routes.py:1939–1947`). It still consumes expected COD. Existing P02, opening, identity, permission, paused-state and writer controls apply to that observer; actual-cash evidence does not activate, unpause or grant posting permission. When already active, original expected-COD/fee recognition may post under its existing rules; that is distinct from the evidence-only addition.

Manual POS/bank review and later POS-bank settlement remain in `accounting_driver_payment_review.py`, using their existing canonical destinations and V2 journal sink. C3 neither changes those economics nor treats review events as physical-cash handovers. Native cash settlement remains `accounting_shipping_native.py:260–355`, with canonical bank movement consumption and dated liability/cash bounds. A proposed same-day matching concern was withdrawn: this existing writer already imposes the exact Riyadh-midnight cutoff, so no independent new C3 regression was demonstrated.

Operational owner revision increments are serialization metadata. They are explicitly distinguished from `writes_paused`, control revision/audit, opening/activation flags and financial journal documents. C3 tests exclude only that serialization revision from the financial/control snapshot comparison; they do not ignore actual control fields.

## Legacy financial access classification

No direct Legacy financial read/write was found in these added producers/readers/matcher. `general_ledger`, Legacy `accounts`, `counterparties`, and `shipping_company_settings` are not authority for these paths. `accounting_source_files` and its retained bytes are documentary evidence. `store_delivery_collections`, assignments, delivery proofs, orders and operational settlement records are existing operational facts; reading them is not reading a Legacy financial ledger. The operational-remittance adapter verifies an existing row and labels its proof limits; it does not call the historical operational settlement writer or its `accounts` lookup.

The existing `NoLegacy` Mongo command monitor (`test_mz2_shipping_native.py:35–42`) watches direct command targets for exactly the four collections listed above. It is useful runtime evidence for these fixtures, not proof covering every possible collection alias, aggregation lookup or unrelated Legacy application route. Source tracing complements that bounded monitor.

## Evidence and current limits

- C1 tests: `backend/tests/test_mz2_shipping_native_rich_contracts.py` reuses strict TrackF replica/NoLegacy fixture; actual C1 HTTP/browser proof previously recorded separately. This audit does not recertify its execution.
- C2 tests: `backend/tests/test_mz2_driver_review_history.py` uses UUID-local manual-POS fixture with inherited NoLegacy monitor; separate actual history browser acceptance is outside this audit.
- C3 tests: `backend/tests/test_mz2_driver_physical_cash.py:194–333` capture/variance/missing-history/retry/concurrency;337–419 matching/idempotency/source ambiguity/authority;439–533 Native source/integrity/reversal;535–721 rollback, forbidden escalation, bounds, source changes and original economics. The fixture stubs only external Salla status response and uses synthetic auth dependencies for these focused route tests; it is not full-app login proof.
- Root reported fresh **76 backend + 61 UI PASS** at the checkpoint. This reviewer inspected source and test assertions but did not rerun them. The root full **142-suite run remains pending** at writing; no full-regression PASS is claimed. Root subsequently reported connected C3 browser **7/7 PASS** and inspected screenshots/hashes; this reviewer did not execute or independently inspect that browser run. Smoke B is prepared but unexecuted.
- The earlier reversed-linked-source completeness defect is fixed: changed financial proof IDs prevent complete coverage without inventing physical cash return. UI uncertain retry reuses exact proof/payload; dedicated frontend lost-response/503 tests passed in the earlier focused23-test run.

## Pinned product blobs

Git blob IDs below bind the reviewed source independently of later documentation or harness commits.

| File | Blob |
| --- | --- |
| `backend/accounting_shipping_native_rich_contracts.py` | `28c4c6002092eb9af401e5ae6242a5a283b76a59` |
| `backend/accounting_shipping_native_contract_evidence.py` | `e2f1784ddd4ef6c5329a0e7c88a510c2ce955139` |
| `backend/accounting_driver_review_history.py` | `8f4d259353397b5a3845446e0881ce18f9d36778` |
| `backend/store_delivery_cash_evidence.py` | `a6a96afae0db5e7e1201d4b7ddf41f2792e7cfb1` |
| `backend/store_delivery_delivery_commit.py` | `23559b8408313facdb48d611f0c93bb06ebfa687` |
| `backend/operational_atomic.py` | `967d911da5ecd9ba60a9a6231adc04f84370ba43` |
| `backend/store_delivery_driver_app_routes.py` | `eb70b3864ab7f1e461194609ffef73ec112ed7ad` |
| `backend/accounting_shipping_native.py` | `85b5ccce73311a8801ec25a83581f0b1fe6a0f1d` |
| `backend/accounting_driver_payment_review.py` | `4110cd531748b840f069e6773a8c0cbc75b4546b` |
| `backend/accounting_shipping_native_routes.py` | `93b51b25e8e09570cd581f2703bce4873d02a295` |
| `frontend/src/pages/AmasiDeliveryApp.jsx` | `1fe8b75bc8d9112c832066d86fd1e202c2f3a42e` |
| `frontend/src/components/driver/DriverPhysicalCash.jsx` | `1683f52b1c6b6f3f397457a47e24225b14c54b6e` |
| `frontend/src/pages/accounting/h2/DriverCashReconciliation.jsx` | `e66c2f2d0b35e03caab6acd9f4cda0abd382a4c7` |
