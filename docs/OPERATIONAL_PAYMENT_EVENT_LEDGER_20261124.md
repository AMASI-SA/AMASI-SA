# Operational balances — event-driven receipts and COD confirmation

Status: IMPLEMENTATION BRIEF / NOT DEPLOYED
Owner approval: 2026-11-24
Tracking: #2107

## Isolation
Only operational balances. Do not create Qoyod invoices, alter Salla order states, or inadvertently double-write both legacy and new accounting stores. Identify the authoritative operational-balance ledger and deployment SHA first.

## Data and posting contract
- PaymentEvent: order_id, installment_id, provider, provider_transaction_id, amount_minor, currency, type (payment|refund|settlement), evidence_source, evidence_ref, occurred_at, observed_at.
- Collection/unique index: stable source transaction identity and event type plus partial-refund id; never deduplicate solely by order_id or amount.
- Lifecycle: observed -> awaiting_evidence -> confirmed -> posted. Rejected/invalid evidence does not affect posted balance. A late confirmation resumes same record.
- Posting is an atomic idempotent operation: persist evidence, event, ledger entry and state transition consistently, with unique enforcement and audit trail. Error/retry must be safe.
- For order initial awaiting-review: automatically post ONLY when actual successful payment proved. Additions become separate transactions under same order.
- Provider-specific reconciliation: use transaction IDs, currency, amount, refund IDs; for payout net amounts account separately for commission and VAT and settlement; prohibit fuzzy amount-only matches.
- Refunds: pending unless accountant explicitly confirms or trusted provider statement verifies; one financial effect regardless of source ordering.
- COD cash confirmed at delivered with verified collection to courier/shipping receivable; bank/POS collected at delivery becomes pending accountant validation until approved or independently verified by bank/acquirer statement; delivered state never rolled back because receipt is pending.
- Bank-transfer order progression is a reconciliation trigger, not independent proof of funds.
- Daily reconciliation and automatic retry + failure alerts; never silently resume long-missing historical data into opening balances.

## Required tests before enabling
1. Initial 300 Tabby, additional 150 Tamara: two receipts, distinct provider balances.
2. Additional unpaid: no posting; later provider statement confirms: exactly one entry.
3. Same webhook/csv resent >=10 times: no duplicates.
4. Partial/full refund manual vs statement in either order: one refund posting.
5. COD delivery before accountant POS/bank confirmation: status delivered, receipt pending, no bank balance change; approval posts once.
6. COD cash creates courier receivable; subsequent remittance decreases it and increases bank/cash once.
7. Revocation/correction is an auditable reversal, never delete history.
8. Rebuild since actual operational-balance start: opening balances preserved, difference report zero per account, no duplicate postings.
9. Multi-currency and fee/VAT precision; no fabricated payment provider when Salla order payment method changes.
10. Permission checks and upload parsing errors, replay after outage, concurrent-event race, backward compatibility and performance.

## Release safety gates
- Inspect production and Preview before code changes.
- Confirm source-of-truth ledger and schema by tracing UI -> API -> DB writes.
- Feature flag defaults OFF; no old-balance mutation on initial deploy.
- Backfill dry-run report first.
- Regression tests, code security review, preview UAT, bank/provider statement reconciliation, release guard.
- No merge or production deployment until explicit approval.
