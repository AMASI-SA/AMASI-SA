# Track C native supplier invoice producer follow-up

**MZ2_SUPPLIER_NATIVE_INVOICE_PRODUCER_BLOCKED_BY_EXACT_GAP**

Resume verified after fetch: PR #1215 HEAD `3b9f2a59d5972fac0eec2963ad936c34f7b55a85`, TREE `4d8e50ca34bf6d876520682fc4b912062d44ebb6`. This follow-up changes tests, their isolated CI dependencies and documentation only. It does not enable a native producer. The final commit identity and CI are recorded in Issue #1006.

## Exact missing contracts

These are named design gaps, not newly implemented runtime error codes:

| Gap | Missing evidence required before posting |
| --- | --- |
| `MZ2_SUPPLIER_PRODUCT_DEBIT_IDENTITY_REQUIRED` | An approved owner-scoped MZ2 debit identity (`entity_type`, exact `entity_id`, `sub_account`) bound to each charged product/variant line, including the rule deciding inventory capitalization versus expense. Product IDs and cost profiles are not financial account IDs. |
| `MZ2_SUPPLIER_SERVICE_DEBIT_IDENTITY_REQUIRED` | An approved MZ2 financial identity and capitalization/expense rule for each charged service resource. `service_id`, service cost and `track_inventory=False` do not prove a debit account. |

The receiving builder has product charges and service charges; it has no separately approved component debit allocation contract. Component/resource costing must not be expanded into additional financial legs by inference. No tax leg is evidenced by this receiving invoice contract either. Do not invent clearing, tax, component, inventory or expense accounts.

`purchase_receiving_service.approved_account_mappings` exposes opening-approved inventory/supplier/input-VAT choices. Its separate purchase contract requires explicit `inventory_account_id` and other selections. The receiving close request and persisted receiving lines contain no such selection or binding. Available opening accounts do not prove which one owns this invoice's product/service costs. Importing the G47 purchase path or its legacy purchase/supplier dependencies would not close these gaps.

## Actual live producer trace

References below are repository paths/functions on the verified resume source, unchanged by this follow-up.

1. `backend/order_engine/__init__.py` registers `make_supplier_receiving_router`; its router prefix is `/supplier-receiving-v1`.
2. Session creation in `backend/supplier_receiving_routes.py` reads `mezan_suppliers_v2` by owner and exact `payload.supplier_id`, excluding inactive rows, and stores `supplier_snapshot`. Close verifies session `supplier_id == supplier_snapshot.id` and the optional expected supplier. This is canonical operational supplier identity, but close currently uses the snapshot; a future native writer must revalidate the current canonical supplier inside the posting transaction.
3. `POST /supplier-receiving-v1/sessions/{session_id}/close` (`close_session`, around line 4690) checks actor/receiving permission and session state, then runs `finalize` with `mongo_session.with_transaction`. It rereads the session, receiving scans and current pieces, checks blockers/experiment consistency, resolves the service catalog, and calls `build_supplier_receiving_invoice`.
4. `build_supplier_receiving_invoice` (line 480) requires exact scanned piece coverage. Quantity is the count of pieces. Product price must have Mezan V2 authority (`mezan_v2_variant`/`mezan_v2_base`, from `resolve_base_unit_cost`); permitted price overrides are explicit. Service lines must be eligible product services. Product total is quantity times approved integer unit halalas. Service total is unit halalas times per-piece Decimal quantity times piece count, rounded to one halala using HALF_UP. Invoice total is the sum of product and service totals; close checks the confirmed total. None of these cost authorities establishes a financial debit identity.
5. Invoice ID is deterministic: `msiv2_` plus UUID5 of `owner:session_id`. Approval time is the server's timezone-aware `now` (`approved_at`); the old journal uses that time as `posted_at`/`created_at`. There is no native `effective_at` producer. A future contract must validate its explicit accounting date against cutover and period rules.
6. Real close first applies authorized price changes within the transaction, then calls `_post_supplier_invoice_ledger` (line 1322). That function immediately calls `assert_writer_allowed(..., "legacy", mongo_session=...)`. With `v2_active`, it raises `accounting_legacy_writer_disabled` before any legacy ledger read/write. Earlier transaction changes roll back. Experiment invoices do not post financial entries.
7. If still legacy-active, the old helper aggregates `general_ledger` entry numbers, writes the two legs below and writes `accounting_audit_log`. Its journal group is UUID5 of `invoice_id:ledger`. Close stores `ledger_txn_group_id`/`ledger_entry_ids`, inserts the invoice, completes receiving state and verifies persisted integrity. The old marker is `supplier_receiving_financial_integrity_v1`, not `mz2_supplier_invoice_v1`.
8. Retry of a closed session calls `closed_result`, which reads the saved invoice and `verify_persisted_supplier_invoice` (`backend/supplier_invoice_integrity.py`). That verifier reads legacy `general_ledger` and requires the old two-leg structure. Transaction plus deterministic invoice/group IDs provide the existing retry scheme; this is not evidence of native atomic/idempotent posting. A closed historical retry can still perform legacy diagnostic reads, so zero legacy reads is not claimed for every existing receiving route.
9. `cancel_session` rejects closed sessions (`supplier_receiving_session_closed`). No receiving invoice native reversal producer exists. Generic `reverse_journal_v2` and the payment consumer's native reversal detection do not supply the missing invoice reversal lifecycle. Before go-live, invoice cancellation/reversal must bind the original group and reconcile allocations; no deletion/reposting workaround is introduced.

## All financial legs

| Path | Debit | Credit | Amount / provenance |
| --- | --- | --- | --- |
| Existing legacy producer | `expense / inventory / None` (hardcoded) | `supplier / exact snapshot supplier_v2_id / payable` | Entire approved invoice amount on each side; `general_ledger`; source `supplier_receiving_v2`; metadata `supplier_invoice_v2_id` |
| Required native producer, BLOCKED | Product/service identities unresolved as named above | `supplier / exact mezan_suppliers_v2.id / payable` | Sum of proved debits must equal exact approved invoice amount; supplier leg `entry_type=supplier_invoice`; native `accounting_ledger_v2` |

No native legs are emitted. The credit identity alone is insufficient. The future native invoice must atomically store `mz2_financial_contract=mz2_supplier_invoice_v1` and `mz2_txn_group_id` together with the native group, use stable retry identity, and carry `supplier_invoice_id`, `supplier_source=mezan_suppliers_v2`, `invoice_contract=mz2_supplier_invoice_v1`. The consumer already requires this provenance. Synthetic test invoice debits are fixture data only, never production account authority.

## Collections and legacy boundary

Existing receiving producer business reads: `mezan_suppliers_v2`, `mezan_supplier_receiving_sessions_v1`, `mezan_supplier_receiving_events_v1`, `mezan_preparation_pieces_v1`, `mezan_products_v2`, `mezan_cost_resources_v2`, `mezan_product_cost_profiles_v2`, `mezan_product_option_cost_bindings_v2`, `mezan_product_resource_bindings_v2`; saved-invoice verification reads `mezan_supplier_invoices_v2`, sessions and legacy `general_ledger`. Actor/permission and index setup run before these business operations.

Existing transaction writes can include `mezan_supplier_invoices_v2`, sessions, receiving events, `mezan_preparation_pieces_v1`, `mezan_preparation_piece_events_v1`, cost profiles/resources, resource bindings, `mezan_cost_change_log_v2`, `mezan_product_cost_revisions_v2`, plus the old `general_ledger`/`accounting_audit_log` when legacy-active. This trace is not an authorization to change inventory or to execute that path in Production.

**New native producer: no enabled path, no collection writes. Legacy reads = 0 in the tested post-cutover writer denial boundary.** The denial test invokes the actual receiving writer with only `mz2_atomic_owners` accessible and rejects any other collection access. This does not relabel the old producer as native or claim its entire route is legacy-free. No `purchase_invoices`, `liabilities`, legacy `accounts`, `suppliers` or `counterparties` are introduced as fallback.

## Track A and verification

Track A remains a separate dependency: `track_a_canonical_account_contract_required` stays fail-closed with no bank port wired. Added adapter contract tests exercise transaction-scoped lookup, exact public selection ID versus returned ledger identity, invalid ID/currency/status/type/missing identity rejection, and atomic absence of settlement/journal writes on rejection. Existing payment tests retain full/partial/unallocated/advance/allocation behavior. No resolver is added.

Local targeted suite: `MZ2_TEST_MONGO_URI=<loopback replica> PYTHONPATH=backend python -m pytest backend/tests/test_mz2_supplier_payments_v2.py -q`: **23 passed**, including 9 added cases. Full eight-suite backend regression: **159 passed**, exit 0, 53.00s. Synthetic UUID databases are dropped; no Production connection is used.

Frozen `backend/accounting_ledger_v2.py` and `backend/accounting_onboarding_identities.py` are unchanged from the resume commit. G47 fixtures/workflows are untouched. G47 + A+B failures on the resume HEAD are legacy-only supplier fixtures rejected by the intended V2 guard; Integration must update expectations after combining branches. Do not weaken `supplier_v2_identity_required`.

Next safe action: obtain approved product/service debit binding and classification contracts, then implement native atomic posting/retry/reversal with those exact identities. Track A FINAL HEAD enables separate bank adapter integration; it does not resolve these invoice debit gaps.

Financial writes Production = 0. Merge = NO. Deploy = NO. Opening Post = NO.
