# P01 MZ2 report isolation — source verification, not release approval

This source branch starts at A `f1bb92adeb3a835f032fb4cd7ddbf6e9447331fd`,
not its intent child B `3d73591d15472df39922dc28270374934c1f8d88`.
PR #1109 and both frozen identities remain unchanged. P01 IN_PROGRESS; P02
LOCKED. Production is unchanged. Prior refund, period, Tabby and advance
acceptance remains evidence for those features, not proof of report isolation.

## Reader contract

`accounting_mz2_reports.read_mz2_ledger` is the shared report boundary. It
resolves the persisted actor's owner server-side, fixes operation ID to
MZ2-FIN-CUTOVER-001, requires the approved balanced opening batch and cutover
approval/evidence, and includes only supported producer journals by economic
date after cutover. Other opening batches and untagged legacy journals are
excluded even without a legacy_orphan flag. Malformed eligible data blocks
the report. No financial number is read from accounts.current_balance.

Missing opening returns needs_opening_balance/not_ready and null financial
maps, never an approved zero. Account documents supply identities and types
only. A bank/provider with later activity still requires opening coverage.
Because ledger_core forbids zero-valued legs, an explicit approved zero may
be recorded in the opening contract's opening_balance_zero_accounts list:
entity_type, entity_id, sub_account, evidence_ref, accounting_at, and
opening_balance_txn_group_id. It must bind to the exact approved opening
group and cutover instant. This patch only reads this contract; it does not
approve openings, infer zero, or supply Production values.

## MZ2 consumer inventory

| Consumer / frontend | Previous endpoint and reader | New MZ2 boundary |
|---|---|---|
| AccountingWorkspace journals-reports | PartialWorkflowPage links to legacy pages | AccountingReports calls dedicated financial-position, trial-balance, journals |
| Financial position | /accounting/financial-position, FinancialPositionLedger; current legacy balance fallback, historical untagged GL | /financial-provider-apps/accounting-module/reports/financial-position; shared approved opening + eligible economic-date scope |
| Trial balance | /accounting/trial-balance, all owner posted GL; no direct frontend consumer found | dedicated MZ2 /reports/trial-balance |
| Journals | /ledger/entries, LedgerTransactionsPage at /transactions; owner GL without fixed operation | dedicated MZ2 /reports/journals |
| AccountingHome | /financial-provider-apps/accounting-module/status; operation/date but no exact approved opening boundary | ledger_only_home_balances uses shared reader |
| SettlementJournalDialog | settlement register detail; owner + transaction group | shared reader followed by selected group; blocked status displayed; journal link remains inside MZ2 |
| Refund period journal | customer-refunds/journal; operation/version/date | shared reader then narrower refund/date filter |
| Migration reconciliation | /accounting/migration/reconciliation, ReconciliationReport; legacy comparisons and actions | removed MZ2 report landing link; legacy page preserved |

Legacy endpoints/pages remain unchanged. Bank selectors use reference metadata;
workflow documents retain their own operational amounts. Other shared legacy
shipping/purchase/payroll pages are not newly certified MZ2 reports. P02/P03
remain locked. Existing compute_balance reads in settlement/refund/advance
write validation are outside this reader-only patch and are not certified as
isolated by this evidence.

## Verification checkpoint

- 17 focused local accounting module, settlement register and date tests pass.
- 16 real-Mongo legacy sentinel/refund/employee/consumer/corrupt-data tests PASS.
- 5 affected refund-entitlement integration tests PASS.
- 17 report/register/page frontend tests PASS; real dialog tests 5 PASS.
- Additional real-router navigation verification and final exact identities are recorded in Issue #1006.
- New isolated Preview current/historical acceptance: pending.
- New final source A, direct intent-only B, governed CI and clean clone: pending.

Production DB transaction/index/backup and tested restore evidence, owner tax
policy/effective date, cutover instant/timezone, owner/permissions and activation
approval remain independent launch gates. Taxable advances remain held for
accountant review. No merge, publication, Production writes or lease creation.
