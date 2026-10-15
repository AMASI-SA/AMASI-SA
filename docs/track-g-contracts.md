# Track G native setup contracts

These helpers are setup metadata only. Authentication, fresh owner resolution,
the setup route allowlist and section/session persistence are supplied by the
onboarding integration. They do not post opening journals, mutate cutover,
activate accounting, or write recurring operating invoices.

## Stage 11

`mz2_provider_fee_policies_v2` stores one schedule envelope per owner, canonical
provider and currency, with immutable policy records and audit events inside.
Providers are exactly `salla`, `tamara`, `tabby`, `emkan`; no aliases or defaults.
Effective start/end dates are inclusive. Adjacent schedules therefore require
the next start to be after the previous end. Policies hold percentage, fixed
amount, optional explicit minimum/maximum, VAT treatment, currency, evidence,
confirmation actor/time and version.

The deterministic envelope `_id` uses Mongo's built-in unique index. Initial
concurrent creation races on that index; later appends compare the envelope
revision, re-read and repeat overlap validation on conflict. This avoids a
check-then-insert window between independent policy documents. No process-local
lock, financial transaction or paused-owner financial barrier is used.

Settlement integration port:
`resolve_fee_policy(db, owner, provider, transaction_date, currency)` returns
exactly one confirmed active policy. Missing/multiple policies fail closed.
Settlement must resolve at its operation date and retain the returned immutable
policy ID/version/snapshot. This track does not copy settlement capabilities
from the excluded pull requests or wire financial posting.

## Stage 13

Actual producers inspected: `recurring_obligations_routes.py`,
`ObligationCreate`, `InvoiceCreate`, `create_invoice`, historical invoice
insertion and `cycle_bounds`. Native obligations hold title, `expense_type`,
entity type/ID/name and `auto_renew`. Native invoices hold `obligation_id`,
`amount`, `period_start`, **inclusive** `period_end`, `payment_status` and
`paid_date`. They do not produce currency or an evidence file ID. Those two
missing facts must be explicitly confirmed in the setup selection; they are
never defaulted or inferred from an obligation's budget/period amount.

`mz2_prepaid_selections_v2` persists the owner, selected real invoice/obligation,
cutover, currency, evidence, exact source snapshot and derived calculation.
The new accounting identity is the adapter ID, not the operating obligation ID.
Reload re-reads native sources and flags changed source snapshots. Historical
invoices marked paid with `paid_date=None` cannot be treated as paid before
cutover without native evidence of the payment date.

Calculation example: inclusive invoice 21 August 2026 through 21 August 2027,
paid 21 August 2026, amount 3,660 SAR, cutover 1 October 2026. There are 366
contract days, 41 consumed days and 325 remaining days: 410 SAR consumed and
3,250 SAR remaining. A producer cycle for an annual subscription ending
20 August 2027 instead has 365 days; adapters never silently change the actual
invoice's contract end date. Unpaid, same-day/future payment and fully expired
coverage do not produce prepaid candidates. Amount rounding uses Decimal,
half-up to cents; remaining is payment minus consumed so the split reconciles.

Exceptional manual prepaid uses `TypedFactCreate(category='prepaid_expense')`
with a required separate `manual_contract` and evidence. It creates no operating
obligation or invoice.

## Stage 14

`mz2_opening_facts_v2` stores independent immutable, owner-scoped facts. Each
has an exact accounting entity ID, category, amount, currency, cutover date,
evidence, source, creator and version. Supported contracts are proven by
`accounting_module_opening_balances.py` and `accounting_opening_categories.py`:

| Category | Entity type | Subaccount | Side |
| --- | --- | --- | --- |
| accrued_expense | liability | accrued_expense | credit |
| other_payable | liability | other_payable | credit |
| other_receivable | asset | other_receivable | debit |
| sales_vat_payable | tax | sales_vat_payable | credit |
| input_vat | tax | input_vat | debit |
| prepaid_expense (exceptional contract) | asset | prepaid_expense | debit |

Sales VAT and Input VAT remain distinct records, identities and lines. No
netting exists. A generic `deposit` category is unsupported: the opening
catalog has no explicit deposit contract. This exact gap is retained rather
than silently classifying a deposit as an ordinary receivable. A documented
ordinary receivable may use the existing `other_receivable` contract.

## Review integrity and verification

`verify_selected_contracts` re-reads owner-scoped facts and recurring producers,
returns stable sorted snapshots for session review hashing and named blockers.
It checks selected IDs, status, cutover, source changes, missing line mappings,
and exact original amount/currency/evidence matches. Typed-category opening
lines, including explicit zero, cannot use arbitrary free-text IDs. Explicit
zero also requires the central onboarding evidence contract. Paid native
prepaid candidates cannot be omitted by marking the stage not applicable;
missing adapter currency/evidence and selection are named blockers. Provider-receivable
lines require a uniquely resolved selected fee policy at cutover.

Local disposable test command:
`python -m pytest backend/tests/test_onboarding_ssot_contracts.py -q`.
Fresh result with the disposable localhost Mongo replica: 14 passed. The
updated central onboarding HTTP suite additionally passes all 47 tests.
Tests cover concurrent fee creation, inclusive
effective boundaries, resolver missing/multiple failures, provider aliases,
calendar-day prepaid calculations, unpaid/date/expired exclusions, persisted
selection reload, changed-source detection, owner scope, typed categories,
VAT separation, missing evidence, unsupported deposit and exact review facts.
The fake database rejects access to every collection outside the five native
setup/recurring collections. An additional real Mongo race exercises eight
competing creators with exactly one winning overlapping policy. The actual
HTTP router test runs setup under pause, verifies only allowlisted collections
changed, persists/reloads session references, previews/reviews successfully,
checks financial handoff remains HTTP 423, then changes the native invoice and
verifies readiness is blocked by the named stale-source reason.

## Exact adjacent runtime integration gap

The broader adjacent regression run exposes two failures in
`backend/tests/test_mz2_receivable_workflow.py`:
`test_new_sale_to_existing_settlement_service_and_balance_guard` and
`test_partial_full_refund_uses_original_rate`. Both use the historical
`provision_write_opening` fixture from `backend/tests/mz2_report_fixtures.py`,
which inserts opening entries into `general_ledger`, uses legacy `accounts`
for bank identity and leaves the owner in `legacy_active`.

Their failure is not safely resolved by substituting only a native opening
fixture. The real paths currently have incompatible runtime dependencies:

| Runtime port | Current implementation | Required integration |
| --- | --- | --- |
| Sale recognition | `accounting_receivable_service.py` calls `ledger_core.post_txn_group` | Native V2 recognition writer and canonical recognition identities |
| Settlement balance check | `accounting_settlement_service.py` calls `read_mz2_write_balances` | Verified native opening and native provider receivable balances |
| Settlement post | The same service calls `ledger_core.post_txn_group` after its balance check | Native V2 settlement writer and canonical bank identity |
| Refund entitlement | `accounting_refund_entitlements.py` calls `ledger_core.post_txn_group` | Native V2 entitlement writer |
| Refund payment approval | `accounting_customer_refunds.py` calls `ledger_core.post_txn_group` | Native V2 refund payment writer and native balance integration |

`read_mz2_write_balances` consumes the now-native-only report reader and fails
with `mz2_balance_not_ready` / `mz2_native_ledger_required` for a legacy-active
owner. `ledger_core.post_txn_group` independently requires the legacy writer
via `assert_writer_allowed(..., 'legacy')`. Switching a fixture owner to
`v2_active` would therefore reject those unchanged writers with
`accounting_legacy_writer_disabled`; it would not establish a working native
flow. The refund failure occurs at bank-payment approval; the settlement
failure occurs at the provider receivable balance check.

These migrations belong to the independent runtime integration tracks. Track G
does not copy excluded pull requests, restore legacy balances, patch writer
barriers, invent native runtime entries in tests or weaken the existing success
expectations. The two adjacent tests remain failing and are explicitly retained
as delivery blockers until those native integration ports are implemented and
verified. The final adjacent run recorded 14 failures and 140 passes; the other
12 failures are the documented P02 native-writer dependency. This finding alone
prevents a claim that all adjacent accounting flows have been verified.
