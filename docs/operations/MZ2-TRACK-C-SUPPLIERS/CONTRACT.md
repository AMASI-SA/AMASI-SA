# Track C supplier settlement contract — review candidate, not financial activation

Base fetched 2026-09-30: `728730366cfee6c906172c7c14a2a67251ed1065`, tree `70b4c3d7b501f7c8a31767ceb61d5ac4cb204df9`.
Branch: `codex/mz2-track-c-suppliers-payments`.

## Identity and opening

Exact `(user_id, mezan_suppliers_v2.id)` is the only supplier identity accepted by the new supplier flow, onboarding supplier picker/mapping/coverage and native V2 journal insertion. No aliases or name matching. Canonical archived suppliers are permitted only for native journal reversals. Existing history is not rewritten.

Opening requires separately documented payable and advance, including explicit zero. A legacy invoice for 1000 paid externally before cutover does not imply opening payable 1000: documented opening payable zero remains zero. No automatic invoice replay, import, opening post or activation is implemented or executed by this task.

## Financial model

`accounting_supplier_payments_v2.py` reads only group-verified native V2 ledger snapshots after the verified active opening. Monetary calculation uses integer halalas. Supplier payable (credit balance) and advance (debit balance) are separate.

- Invoice payment: Dr supplier/payable, Cr canonical funding account.
- Unallocated payment: explicit choice to reduce payable or create an advance; it does not silently change invoice state.
- Overpayment: rejected unless explicitly authorized to create the excess Dr supplier/advance. Funding credit covers both portions.
- Allocation of an unallocated payable payment: immutable allocation record, no second funding/payable debit.
- Allocation of an advance: explicit Dr supplier/payable, Cr supplier/advance. It never happens on reads or invoice recognition.
- Invoice paid/partial/unpaid and remaining are derived from verified debit allocations, ignoring cached invoice paid/status fields.

Owner-serialized Mongo transactions contain permission refresh, identity validation, pause/transition gates, verified opening, balance reads, evidence checks, period checks, append-only native journal and operation record. Same operation ID with same payload replays; changed payload conflicts. Concurrency cannot overconsume the same payment. Reversed invoice/payment/allocation groups require reconciliation; no automatic repair.

## Invoice GAP — upstream producer not supplied

Current `mezan_supplier_invoices_v2` receiving producer uses `supplier_receiving_financial_integrity_v1`, whose validator reads **legacy general_ledger**. Its name does not establish MZ2 financial provenance. `purchase_invoices` / `liabilities` belong to the separate G47 path; this task neither consumes nor modifies them.

The proposed invoice binding seam is `mz2_supplier_invoice_v1`: existing source row must explicitly carry `mz2_financial_contract`, `mz2_txn_group_id`, exact V2 supplier ID, SAR, positive integer `total_halalas`, and `experiment_mode=false`. Its native group must verify and contain exactly one matching supplier/payable credit with `entry_type=supplier_invoice`, matching amount and metadata `supplier_invoice_id`, `supplier_source=mezan_suppliers_v2`, `invoice_contract=mz2_supplier_invoice_v1`.

No production writer emits this binding yet. The synthetic test producer proves the consumer seam; it is not approval of a live invoice producer. Unbound/historical invoices are displayed as nonfinancial history with unknown payment remaining; never converted to debt. Opening payable can be paid without an invoice, but opening-to-historical-invoice allocation needs a separate documented contract and is not inferred here.

## Track A dependency — blocked by default

`CanonicalFinancialAccountPort` is a server-injected interface, not a bank resolver. Default registration has no port and cash payment fails closed with `track_a_canonical_account_contract_required`. No legacy `accounts` query or fallback exists in this flow.

Track A must provide owner-scoped active SAR bank/cash listing and transaction-bound validation returning exact `id`, `entity_type`, `entity_id`, `sub_account`, `currency`, `status`, `account_type`. After its reviewed commit is available, rebase/wire the port, then rerun integration and browser tests. Test-only `BankSeam` is never registered in production. Allocation does not withdraw new funds and does not depend on this port.

## Scope boundaries

Legacy G47 `supplier_payment_service.py`, legacy `require_linked_supplier`, P01 daily movements, inventory, and P02 remain unchanged. They are not approved MZ2-only paths. New native V2 supplier journal legs cannot emit a legacy-only identity; old tests that expect such native writes must adopt canonical synthetic identities or retain a fail-closed expectation. This is not a claim that prohibited historical legacy writers were migrated.

`/suppliers-v2/financials` becomes a read compatibility alias to the new native financial workspace, removing that endpoint's legacy-ledger balance dependency. Supplier directory operations keep their existing permissions; financial detail requires accounting journal-report permission and writes require movement-import permission.

Production financial writes = 0. Merge = NO. Deploy = NO. No release guard lease, intent or control mutation.
