# Operational Financial Balances — approved contract

Status: implementation under isolated verification. Production financial writes = 0.
The user's latest `SUPPLIER_OPERATIONAL_RECEIPT_FINAL_RULE` supersedes all earlier suggestions of a separate supplier receipt action.

## Boundary

This temporary system records operational estimates, confirmed obligations, actual movements and remaining balances. It is not an accounting ledger. It does not call accounting writers, create journals, run accounting opening, activation or inventory initialization, or modify MZ2 Accounting. MZ2 identities and existing operational evidence are the only business sources. Missing contracts are visible as incomplete; there is no Legacy fallback.

## Supplier rule — final owner decision

Product cost → unassigned estimate → supplier assignment → supplier estimate → **existing application supplier invoice issuance/approval** → supplier confirmed obligation → actual supplier payment → remaining amount.

There is **no new operational receipt confirmation event, endpoint, button or stage**. The adapter reads the existing `mezan_supplier_invoices_v2` invoice and its lines as operational evidence. Reading an invoice does not execute its producer or any accounting writer. Internal normalized `supplier_receipts` / `receipt:` names denote invoice-line evidence, not a newly created receipt workflow.

Before invoice issuance, product cost remains estimated. Only the invoice-covered quantity/value moves to confirmed. Example: estimated 8,000, issued invoice 5,000 → estimated 3,000 and confirmed 5,000. Never 8,000 plus 5,000.

Stable evidence identity uses the supplier invoice, invoice line and covered order/item/piece identity. Re-reading or synchronizing the same invoice, retrying a request, or later closing that invoice cannot create another obligation. A changed already-confirmed evidence identity is flagged for explicit reconciliation instead of silently rewriting history.

Cancellation before issuance removes the estimate. Cancellation after issuance preserves the confirmed covered amount. Assignment changes move only the unconfirmed estimate; confirmed amounts stay with the invoiced supplier. A customer return or a cancelled order is not evidence of an accepted supplier return. Only an actual accepted supplier return with its reference and evidence reduces confirmed liability by the accepted amount. A return after payment creates a supplier credit to collect; it must not erase the historical bank payment.

## Opening screen and system start

One page: type → native MZ2 entity → له / عليه → amount → save. Save clears the form and updates “الأرصدة المدخلة: X”. Save and finish atomically saves any final entered row and fixes the system start instant.

Types: employees, payment platforms, shipping companies, store drivers, advertising accounts, suppliers, external parties, banks and cash. Only cash and external-party types show an add button using the existing native MZ2 metadata contracts. No technical identities, order IDs, cutover fields, hashes, allocations, workflow or accounting vocabulary appears on this page.

Openings are immutable approved baselines, not incoming/outgoing movements. Later changes are documented correction movements. One start instant applies to the system; no per-order cutover, per-order opening or manual entry of new orders is required.

## Daily movements

The employee application and Mezan 2 use the same operational movement API and form. Incoming/outgoing, amount, party, bank/cash where applicable, kind, optional order, note and receipt are recorded. Actor, authenticated source and timestamp come from the server. A bank statement or settlement file is never a prerequisite. A bank receipt is evidence attached to the movement itself.

An order-linked bank movement is allowed when the native order is reviewed or in progress according to its contract; delivery is not a prerequisite. Record order reference when present, bank, amount, direction, receipt when required, source, actor and time. Do not create an accounting entry.

A transfer or settlement is one atomic operation: its bank effect and counterparty effect cannot succeed separately. Replays use the same request identity; conflicting payloads fail. Duplicate receipt content and bank references cannot create another financial effect. Excess payments are explicit advances, never silently allocated beyond confirmed obligations.

## Other automatic rules

- Shipping/driver: estimated until delivered; freeze confirmed cost and COD receivable on the actual executor. Carrier changes before delivery replace the old estimate atomically. A later cancellation does not erase completed service.
- Payment platforms: Gross − Cancelled − Refunded = Net; Net − estimated fee = Expected. Read commission, tax and effective dates only from `mz2_provider_fee_policies_v2`. Missing refund/cancellation fee terms or tax terms remain incomplete. No manual commission-percent screen. Estimated fees never reduce actual bank money; actual settlement fees are applied once, while only net cash reaches the bank.
- Refunds: requested is not executed. Stable native refund identity prevents double subtraction. A refund after settlement creates a payable credit to settle separately without repeating the original bank receipt.
- Employees: native MZ2 salary contract and effective date, from the day after system start, divided by actual calendar month days. Immutable employee/day accrual identity; no retroactive salary rewrite. Calendar cumulative rounding conserves a full month's salary. Existing employee receivable offsets entitlement before a payable builds.
- Advertising: cumulative within-day snapshots replace earlier observations (200 → 300 → 450 means 450). Finalize a complete previous day once after 02:00 in its contract timezone. Preserve currency and available FX evidence. No guessed exchange rate. Prepaid spend consumes wallet balance; postpaid spend creates payable. A changed closed day requires documented adjustment, never silent replacement. The partial startup day needs evidence separating pre-start spend from post-start spend.
- Recurring obligations: due estimates or evidenced confirmed costs do not change a bank until actual payment.

## Independent persistence and APIs

`operational_balance_states_v1`: tenant aggregate containing status/revision, fixed start, immutable baselines, source facts/projections, movements, request replay results, audit and final snapshot. One compare-and-swap protects both transfer sides, allocations, audit and replay receipt from concurrent writes. A hard size limit fails explicitly rather than truncating financial history.

`operational_balance_receipts_v1`: tenant-scoped receipt content, MIME, hash, actor, source and timestamp. Native cash/external-party creation writes only their existing metadata collections. All other MZ2 business sources are read-only.

API prefix `/api/operational-balances`: context, native entities, openings, finish, receipts, movements, safe allocation choices, reports, audit, accepted supplier returns and freeze. There is no independent supplier receipt-confirmation API. Authentication is tenant/actor scoped with separate daily-movement and broad-report permissions. Revalidate authorization before persistence and on every CAS retry.

## Stop and reconcile

An explicit freeze preserves an immutable hashed final snapshot and audit, stops calculations and rejects new movements. Detecting active MZ2 Accounting also blocks operational financial writes. This implementation does not activate accounting. Final reconciliation and enabling production require separately authorized owner action.

## Acceptance evidence

Local tests must cover MZ2-only reads and Legacy rejection, concurrent duplicate movements, repeated order states, carrier changes, full/partial supplier invoices, repeated invoice reads and later closure, cancel before/after invoice, accepted supplier returns, settlements, refunds, bank transfers, effective salary accrual, cumulative advertising and daily close, recurring costs without bank effect, freeze, authorization and tenant boundaries.

Supplier acceptance values are derived from the owner's examples and exercised through real isolated Mongo source adapters and service, not only pure engine mocks. Test/source/preview evidence and any unresolved contracts are recorded in the task status and delivery record. No readiness marker is issued while required checks are failing or unfinished.

## Final UI/scope update — owner approved

The latest owner attachment extends this contract with independent operating expenses and owner/manager withdrawals in opening and daily forms; employee custody as a separate operational balance; and hybrid advertising funding according to the native binding. No Legacy directory or accounting writer may be reused.

Employee custody funding: bank → custody 5,000, then custody → expense 500, leaves custody 4,500 with only the first bank outflow. Report funded, spent, returned, remaining and receipts, separately from employee salary.

Operating categories requested: fuel, rent, internet/telecom, utilities, food/hospitality, drinks/hospitality, subscriptions/services, maintenance, stationery/supplies, transportation, other operating expenses. Reuse the native MZ2 category contract and allow authorized additions. Owner/manager withdrawals reduce the funding account but never enter operating-expense totals.

Daily source account may be native bank, cash, or employee custody as appropriate. Bank order status alone never creates money; an approved evidenced movement must exist. External parties may be added during daily operations under permissions. Reports keep balances, stage amounts and flows distinct and never sum them as independent balances.

This extension is in progress at the first remotely authorized WIP checkpoint; the readiness marker remains withheld until it is implemented and verified.
