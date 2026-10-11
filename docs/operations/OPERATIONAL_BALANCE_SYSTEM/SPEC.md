# Operational Financial Balances — approved contract

Status: implemented in isolated worktrees; verification and remote handoff are recorded in DELIVERY.md and STATUS.json. Production financial writes = 0.
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

Types: employees, payment platforms, shipping companies, store drivers, advertising accounts, suppliers, external parties, banks, cash, employee custody, operating expenses and owner/manager withdrawals. Cash and external-party types show an add button using existing native MZ2 metadata contracts. No technical identities, order IDs, cutover fields, hashes, allocations, workflow or accounting vocabulary appears on this page.

Openings are immutable approved baselines, not incoming/outgoing movements. Later changes are documented correction movements. One start instant applies to the system; no per-order cutover, per-order opening or manual entry of new orders is required.

## Daily movements

The native employee application and Mezan 2 render the same operational movement contract using their native UI components and one operational API. Incoming/outgoing, amount, currency, party, bank/cash/custody where applicable, kind, optional order, note and receipt are recorded. Actor, authenticated source and timestamp come from the server. A bank statement or settlement file is never a prerequisite. A bank receipt is evidence attached to the movement itself. The native client has a default-off operational write switch and separate page/create/manage grants; enabling this switch is outside this delivery.

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

This extension is implemented. Custody uses existing employee identity, never salary balances. Funding and return touch bank/cash once; expense payment from custody touches custody only. Insufficient custody is rejected atomically. Expense flows and owner withdrawals have separate report totals, excluded from one another and from summing balance stages.

Hybrid advertising requires a complete versioned native policy. Wallet and debt openings remain separate even on the same account; each day's cumulative spend is split once by the approved wallet fraction. An incomplete split is not guessed. A changed closed source day remains flagged until an explicitly documented operational correction is made; no accounting-linked correction producer is called.

Recurring obligations use the full eligible contract period amount as Expected. A native invoice is not payment and cannot promote the unpaid amount. Their selectable payee is the native operating-expense category; original context remains internal. An explicit allocated payment settles only its paid portion, records one bank/cash outflow and one expense flow. Rent 5,000: Expected 5,000 / Actual 0; payment 5,000: Expected 0 / Actual-Settled 5,000. Automatic allocation never pays an unconfirmed estimate.

Both clients persist the exact pending movement payload before sending it, preserve its request identity across uncertain responses, and prevent editing that unresolved operation into a second payment. Server-side CAS and evidence identities remain authoritative across clients.

The direct MZ2 expense registry is `expense_categories`, as used by the existing MZ2 daily-movement contract. It is physically shared with older consumers: this implementation does not read the Legacy category tree or claim that every untagged registry row has proven historical provenance. Additional native expense types are metadata records only; no accounting writer is invoked.


## Authorized final-review fixes — 2026-10-05

- COD keeps Gross, customer Collected, Customer Outstanding, cash Custody, Settled and outstanding remittance separate. Gross 300 less native paid 80 leaves 220. Native non-cash driver collection does not create cash custody or a second bank movement.
- Supplier estimate 100 / invoice net 80 leaves Expected 20. Native purchase tax 12 makes Confirmed gross 92, with net/tax/gross and evidence retained separately. Invoice line identity remains the sole confirmation authority.
- Shipping uses the approved native rich contract's pure calculator. Base shipping and COD commission are independently identified components; an incomplete commission does not suppress verified base cost, supplier components or COD. Unapproved/invalid common contract terms remain incomplete. No financial writer is imported or called.
- Advertising calendar/start boundary and 02:00 close use the advertising account's ZoneInfo timezone. Cumulative daily snapshots are replaced, not added; incomplete close evidence cannot finalize.
- Movement API requires expected_session_scope. Actor + request ID pins one operation across ownership changes in independent operational_balance_operation_claims_v1 metadata. Its unique Mongo _id arbitrates concurrent claims; existing WIP movements are honored. Claims have no cash effects.
- Definitive business rejection is committed as a terminal result under the same owner aggregate CAS as movement acceptance. Only a committed rejection returns not_applied=true. A concurrent copy replays the winning accepted/rejected result; unknown, scope and transport outcomes stay pending. A corrected intent uses a new request ID.
- Native financial writers and production configuration remain disabled. An isolated Android package and actual device UAT are required before readiness; browser/unit checks do not substitute for Device UAT.
# Latest owner decisions — Web first (2026-10-05)

These decisions supersede earlier operational permission and receipt requirements below.

- Active authenticated members of the same owner account share operational functions without special operational grants or owner/staff role tiers. Authentication, current membership, tenant isolation, disabled-user rejection and actor audit remain mandatory. Accounting access is unchanged.
- Daily manual movements do not require receipt upload; Web no longer shows an upload field. Historical receipt records and evidence-specific supplier-return contracts remain intact.
- A new order paid by bank transfer credits its full order amount to the selected MZ2 bank when its canonical status is reviewed or in progress (including the approved Arabic labels). No receipt, bank file, settlement file, ledger or accounting writer is needed.
- Read the existing confirmed `mz2_bank_transfer_bindings` contract (`salla.payment_method_bank`) and `mz2_financial_accounts` only. Missing/ambiguous routing is incomplete configuration, never a guessed destination.
- The credit uses stable order identity and a durable cross-owner operation claim. Status transitions, concurrent refresh, retry or owner reassignment must not add another credit. Later cancellation/delivery cannot erase or repeat an already credited amount. Changed amount/destination requires explicit correction; show a conflict and preserve the recorded amount.
- Manual incoming movements tied to an automatically credited bank-transfer order are rejected to avoid duplicate bank money. Supplier and shipping costs retain their separate approved behavior; the bank credit creates no COD receivable.
- Android development remains paused. The existing APK is only a frozen UAT candidate; Web/API owner approval comes before any Android resumption.
