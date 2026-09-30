# C2 explicit supplier debit identities and native invoices

Base after fresh fetch: Production HEAD `728730366cfee6c906172c7c14a2a67251ed1065`, TREE `70b4c3d7b501f7c8a31767ceb61d5ac4cb204df9`.
Branch: `codex/mz2-supplier-debit-identity-native-producer-20260930`.

PR #1215 remains frozen at `49cf4fa967fa17792b45d94613e5ba89111c2b60` / `075c62d5f68082c6bccad73b5c61f58e6852f3ab`. C2 starts independently from Production, without copying C1 or Track A. No edits to `accounting_ledger_v2.py` or `accounting_onboarding_identities.py`.

## Explicit authority

`mz2_supplier_debit_identity_v1` is a new, narrow contract implemented in `supplier_debit_identity_v2.py`:

- `mz2_supplier_debit_mappings_v2`: owner (`user_id`), source kind/id, optional variant, treatment, exact ledger identity, SAR, active/inactive status, confirmation actor/time/reason, monotonically increasing version. Audit before/after snapshots are stored in `mz2_supplier_debit_identity_audit_v2` in the same owner transaction.
- `mz2_supplier_expense_identities_v2`: **new C2 authority**, not an existing Opening or Track A account. An owner explicitly creates and confirms a named supplier expense identity; its opaque deterministic ID derives from owner + creation request ID. Its ledger key is `expense / exact approved ID / null`. Name is display-only. No expense account is created while resolving mappings or posting invoices. Explicit service mapping is a separate operation.
- Product mappings accept `INVENTORY_ASSET`; service mappings require `CAPITALIZE_TO_INVENTORY` or `EXPENSE`. No classification is derived from name, category, cost or inventory tracking flags.
- Product variant mapping wins over product mapping, which wins over explicitly confirmed owner default. A present inactive specific mapping blocks; it never silently falls through. No mapping means a named fail-closed error.
- Product keys are canonical `mezan_products_v2.mezan_product_id`. Receiving's exact operational product reference (`id`, `mezan_product_id`, `salla_product_id`) must resolve to exactly one owner-scoped V2 row. The canonical `product_v2_id` is retained in the immutable financial snapshot. Ambiguous or missing references fail. Variant and service existence/activity are rechecked when posting, including zero-value line sources.
- Inventory and Input VAT identities must be explicit identities in the active approved MZ2 Opening. Validation binds the draft's preview manifest hash to the sealed Opening journal's approved hash (or approved evidence and posted audit for zero-only Opening); changing or inventing a draft line does not authorize an account. Sources are read-only here. No Opening is posted or activated by C2.
- Every mapping and referenced identity is revalidated inside the transaction. Inactive/deleted/foreign-owner identities fail. Defaults are never inferred.

## Owner setup API

The registered `/supplier-debit-mappings-v2` router provides GET context and PUT explicit mapping, plus POST `/expense-identities` and PUT `/expense-identities/{identity_id}` for expense identity creation/state. Requests require explicit `confirmed=true` and a reason; mapping/state changes use optimistic versions. Fresh owner authentication and `atomic_owner` enforce ownership and financial pause. No migration, name matching or bank resolver is introduced. This is an API contract; no new mapping UI is included.

## Producer trace and legs

`POST /supplier-receiving-v1/sessions/{session_id}/close` reads the explicit ledger transition state. For a real receiving session with `v2_active`, it enters `atomic_owner`, serializing on `mz2_atomic_owners` and enforcing write pause. It rechecks the receiving actor, scans/current pieces, exact coverage and allowed service catalog; `build_supplier_receiving_invoice` calculates product/service totals in integer halalas. Authorized price changes and receiving completion remain within that same transaction.

`post_native_invoice` then freshly checks `accounting.purchases.post`, owner, active exact `mezan_suppliers_v2.id`, safe active cutover and verified Opening. It resolves every nonzero product/service debit, checks exact amounts, period and accounting date, and calls `accounting_ledger_v2.post_journal_v2`. Close atomically inserts the invoice and completes the session. There is no native-to-legacy fallback on failure.

| Leg | Identity | Amount |
| --- | --- | --- |
| Product debit | `asset / explicitly mapped inventory identity / inventory` | Piece count × approved product unit halalas |
| Capitalized service debit | `asset / explicitly mapped inventory identity / inventory` | Approved unit halalas × per-piece service quantity × piece count, HALF_UP to halala |
| Expensed service debit | `expense / owner-confirmed C2 expense identity / null` | Same exact service calculation |
| Optional purchase tax debit | `tax / explicitly approved Opening input-VAT identity / input_vat` | Explicit purchase-tax amount with preserved evidence |
| Supplier credit | `supplier / exact mezan_suppliers_v2.id / payable` | Exact approved invoice total |

No component legs are inferred. Sum of all debits = approved invoice total = supplier payable credit. No supplier payment or bank leg is emitted here.

Each native leg has `entry_type=supplier_invoice`, metadata `supplier_invoice_id`, `supplier_source=mezan_suppliers_v2`, `invoice_contract=mz2_supplier_invoice_v1`, accounting time and sealed invoice digest. The invoice stores `mz2_financial_contract=mz2_supplier_invoice_v1`, `mz2_txn_group_id`, debit mapping snapshots and payload hash atomically. Historical `ledger_*` presentation fields alias this same native group; they do not cause a second posting. Native persisted verification reads only the sealed V2 journal and invoice/session, never the old ledger.

Effective time is explicitly supplied `accounting_date` at Riyadh midnight, or the timezone-aware approval time. Future dates, pre-cutover dates and closed periods fail; dates are not shifted to obtain permission.

## Purchase tax contract

Tax is optional and never assumed. `purchase_tax` requires explicit `INPUT_VAT` treatment, positive integer amount, exact approved tax entity, preserved `accounting_source_files` file ID and SHA256, and owner confirmation. Posting rehashes original file bytes and requires owner authority. The invoice binds the tax declaration/evidence to its supplier, invoice ID and exact amount through the sealed digest. Total equals untaxed receiving subtotal plus this explicit amount. Unsupported/uncontracted tax fails closed; Sales VAT policy is not consulted. This contract records an owner's explicit purchase-tax classification; it does not infer tax eligibility from a document or product.

## Retry, immutability and reversal

Invoice ID remains deterministic from owner/session. Journal idempotency key is `supplier-native-invoice:<invoice ID>`; sealed content hashing rejects changed economics. Owner serialization prevents concurrent closes from writing two invoices or journals. Native replay compares the submitted close payload hash (excluding nonfinancial note), then verifies saved invoice economics against the native journal. It does not rebuild liabilities from a historical invoice. Failed resolution, period checks or later receiving writes abort invoice, journal, audit and session effects together.

`POST .../invoices/{invoice_id}/reverse` deliberately fails with `supplier_native_reversal_reconciliation_required` and the exact original `mz2_txn_group_id`. It performs no write, delete or repost, even if no payment is present. This is the explicitly allowed fail-closed lifecycle boundary. Integration must add payment/allocation reconciliation before enabling native reversal. Current Production exposes no general operational V2 journal reversal route; the underlying sealed library is not an invoice lifecycle API. Out-of-band reversal detection also prevents a reversed native invoice from being returned as a valid original.

## Collections and remaining integration

New identity writes: the three `mz2_supplier_*identity/mappings*` collections listed above; owner coordination revision. Native financial writes: `accounting_journal_groups_v2`, `accounting_general_ledger_v2`, `accounting_audit_log_v2`, ledger counters, and `mezan_supplier_invoices_v2`, inside the receiving transaction. Reads include users/permissions, settings, active Opening draft/evidence/sealed ledger, accounting periods, supplier/product/service V2 catalogs, mappings and optional preserved tax evidence. Existing receiving session/event/piece/cost updates remain atomic; inventory algorithms are not changed.

The new native producer does not read or write `general_ledger`, `accounts`, `suppliers`, `counterparties`, `purchase_invoices` or `liabilities`. Mongo command monitoring tests measure this boundary. The existing pre-cutover legacy-active receiving path and historical legacy invoice presentation remain separate. Their continued existence is not native authority or a fallback for unresolved mappings.

Integration must combine this independent branch with frozen C1 supplier/payment consumers and ledger identity guard, and frozen Track A `92eda46c71e039800a7e91db8a814e5b86161b74` / `0c1886fe28d96bb37552192849ff2b9173c793e8`. No Track A code or bank wiring is copied here. Supplier reversal remains disabled until reconciliation is implemented. Owner setup must explicitly confirm real mappings and evidence through the reviewed contract before live usage; this task performs no such Production setup.

Final HEAD/TREE, PR, exact fresh test/CI results and continuation checkpoint are recorded in Issue #1006. Financial writes Production = 0; Merge = NO; Deploy = NO; Opening Post = NO; Activation = NO; Release Guard = NO.

## Acceptance evidence

Final local combined command (see STATUS.json) passed **146 tests and 7 subtests**, exit 0, 105.12s. This includes 26 native producer tests, 14 mapping/administration tests, existing receiving/integrity suites and unchanged ledger/write-control/period suites. A broader architectural test exposed one direct physical ledger read; that read was removed in favor of the sealed query API, preserving the test unchanged.

| Required cases | Evidence |
| --- | --- |
| A/B/C | Product mapping, variant override, explicit default and missing mapping tests |
| D/E/F | Service capitalization/expense positive tests and missing classification rejection |
| G/H | Foreign-owner, inactive/deleted identity, lifecycle timestamps and manifest-tamper rejection |
| I/J/K | Exact independent halala assertions on debit/credit legs and mismatched invoice rejection |
| L/M | Uncontracted tax rejection; explicit Input VAT identity, amount and original-file hash proof |
| N/O/P | Same/concurrent native posting and real concurrent HTTP close; changed payload conflict |
| Q/R | Legacy-only supplier rejection; Mongo command listener observes zero forbidden legacy collections |
| S/T | Pause/permission/period/cutover checks; failure after journal and missing resolution roll back all transaction effects |
| Lifecycle | Reversal API refuses; library-only synthetic reversal causes read reconciliation failure without mutating original invoice |

CI and final immutable HEAD/TREE are linked from the final Issue #1006 checkpoint. Setup remains explicit owner API work; no Production mappings/accounts were created by this task.
