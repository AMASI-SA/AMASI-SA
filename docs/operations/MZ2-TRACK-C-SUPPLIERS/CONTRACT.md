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

## Review evidence and acceptance limits

Implementation source checkpoint: `983ce3f9cce8e30ba4923e8ae55964f8069da893`, tree `c440b692a3ca6eadb9dc84fe82b12036c32b6171`. Draft PR #1215. Final handoff SHA/TREE is recorded in Issue #1006; later documentation commits do not change this source.

|Acceptance|Evidence / limitation|
|---|---|
|A / B|Canonical directory and Opening picker tests include a legacy-only sentinel which is absent.|
|C / D / E|Native invoice test fixture derives 1000 unpaid, 400 paid/600 remaining partial, then zero remaining paid, ignoring false cached paid status. Live native invoice producer is still GAP.|
|F / G|1300 payment against 1000 requires explicit advance authorization; later 500 invoice leaves payable 500 and advance 300 separately until explicit allocation.|
|H|Historical invoice 1000/unpaid cannot alter documented opening zero or accept financial payment.|
|I / J|Payment legs and native journal insertion use exact owner-scoped V2 supplier ID. Legacy-only/new-opening identities fail. Historical legacy writers are not migrated.|
|K|New service has no legacy accounts access; missing Track A port rejects payment. Existing out-of-scope legacy/G47 service still contains an accounts fallback and must not be certified as MZ2-only.|

Additional tests cover duplicate and conflicting retries, concurrent same/different payment attempts, unallocated payable allocation without duplicate ledger legs, explicit advance allocation, invalid evidence, insufficient funds, future/pre-cutover dates, closed periods, pause and permission rejection, transaction rollback after journal insertion, reversal reconciliation, inactive suppliers with unknown balances, HTTP registration and disabled default bank port. Frontend mounted tests exercise same-intent retries, definitive validation correction, stale financial refresh clearing and supplier-switch form reset.

Track A #1212 inspected at `e0a983a9677268bfb342a4dd8ed5b79e07714afb`: status SOURCE_IMPLEMENTED_CI_REVALIDATION. Its shared contract is `accounting_financial_identity.find_financial_account/list_financial_accounts`, with transaction-bound db, explicit SAR and bank/cash types. Do not duplicate it. The reviewed integration should adapt these two APIs to the declared port and wire router registration after Track A validation/rebase. It is not included in this Production-based branch.

### CI risk requiring reviewer action

Initial #1215 CI demonstrates a deliberate compatibility break: G47 fixtures create **legacy-only** suppliers and now native V2 journal insertion correctly returns `supplier_v2_identity_required`. The G47 workflow has 26 failing cases; A+B integration has one same-cause case. These failures are **caused by this stricter supplier boundary**, not pre-existing green results or unrelated infrastructure. G47/inventory implementation and fixtures remain untouched per the task prohibition. They require their owning track to adopt canonical supplier identity before combined integration can be green. Do not bypass the identity guard or weaken tests.

Security Gate also fails on unchanged production requirements (`urllib3 2.7.0`, `PyJWT 2.14.0`, four advisories); dependency remediation is outside Track C. Initial dedicated supplier CI failed at test collection due to missing reportlab; the dedicated workflow now installs the route dependencies. No check was skipped to hide these results.

No governed release build, browser screenshot review, live bank transaction or live invoice-producer verification has been performed. This is a Draft source review handoff, not merge readiness or financial go-live approval.
