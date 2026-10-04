# Reuse decisions, 2026-10-04

Read-only contract investigation accompanies the backlog. These notes are not authorization to change economics, browser support or production configuration.

## WP03 historical COGS is a contract reference, not safe runtime to copy

Current source3a2 uses `moving-weighted-average-v1` in `backend/purchase_receiving_service.py` and explicitly nonfinancial physical consumption in `backend/stock_component_consumption_service.py`. `test_g47_purchase_approval_integration.py::test_purchased_receipt_is_reserved_then_physically_consumed_without_another_journal` remains unchanged.

Historical PR1126 SHA `594124827167dbcb2a1c7e8fdbd05d7aaac221f7`, `backend/accounting_inventory_p03.py`, contains sale-gated COGS, immutable receipt/opening cost and physical return-restock-gated reversal. Its tests cover original cost, partial/final remainder and resale. It is not in the current tree and is not an ancestor of the audited baseline.

Do not cherry-pick it: it imports `ledger_core`, reads `general_ledger`, hardcodes asset/inventory and expense/cogs, and consumes lot-cost events not supplied by the current G47 producers. Resolve current MWA vs historical lot basis, exact COGS debit identity, physical return producer, component/finished-goods ownership and cost snapshot timing before financial implementation. Reuse its independently stated economics and acceptance scenarios only after this reconciliation.

## WP04-A a limited provider advance conversion can reuse native contracts

`accounting_customer_advances.py` captures an explicitly reviewed untaxed advance (provider receivable / customer advance liability). Already-recognized tax deliberately fails closed. `test_mz2_customer_advances.py::test_advance_prevents_later_sale_and_unconfirmed_or_early_refund` demonstrates the current conversion gap.

Reuse `accounting_customer_native.post_customer_journal`, verified capture evidence, `accounting_recognition_evidence`, `accounting_sales_tax_service`, and the existing bank-transfer conversion economics. Do not call the bank-specific function for BNPL or remove the generic existing-journal guard. `accounting_settlement_service` remains the clearing writer. Conversion must not debit provider receivable again. Restrict first slice to full, uncancelled, unrefunded, reviewed untaxed advances; already-taxed/partial/overpayment cases remain fail-closed pending their contract.

## WP04-B supplier reversal

`accounting_ledger_v2.reverse_journal_v2` supplies append-only reversal/idempotency; `accounting.journals.reverse` exists. C2 CONTRACT.md explicitly requires allocation/payment reconciliation first. Existing native invoice integrity rejects unregistered reversals. Reuse native payment/allocation verification for a read-only eligibility preview first. Define receipt/service/cost consequences before enabling any positive reversal; unpaid alone is insufficient. Do not weaken the current 409.

## WP05 read-only reports

The current cutover plan requires income statement and assets=liabilities+equity. Historical PR1128 SHA `8d7307a71fcdae5975d2696636d9b88335564cdc` contains useful pure income aggregation, but also an old accounts reader and hardcoded COGS classification. Reuse calculation through the current canonical `_classified_scope` only. No historical balance reader or identity guesses.

For period bank reconciliation reuse original statement bytes/hash/row identity from `accounting_daily_movements` and `accounting_bank_statement_proof`, plus native account statements. Unmatched movements remain explicit, not automatic adjustments. Formal cashflow operating/investing/financing classification and statutory tax filing have no complete reviewed contract in the inspected source; retain pending scope.

## WP06 fresh security investigation

PR1247 remains unchanged. On 2026-10-04 official registry/advisory evidence showed:

- braces latest3.0.3; GHSA-vfj7-8cjw-p6xm affects <=3.0.3, patched versions **None**.
- Tailwind3 latest3.4.19; Tailwind latest4.3.3.
- micromatch4.0.8 depends on braces^3.0.3; fast-glob3.3.3 depends on micromatch^4.0.8.
- Locked paths: Tailwind→chokidar3.6→braces; Tailwind→fast-glob→micromatch→braces; Tailwind→micromatch→braces directly.
- A parent/lockfile-only safe fix is not currently demonstrated. Forcing chokidar4/5 changes glob/API behavior and leaves the other paths. Moving Tailwind out of audited dependencies is not remediation.
- Tailwind4 officially requires Firefox128+, Chrome111+, Safari16.4+. Firefox121 remains the owner's explicit contract. No supported121 compatibility strategy was established. Current Vite target does not itself prove CSS support.

References: https://github.com/advisories/GHSA-vfj7-8cjw-p6xm ; https://registry.npmjs.org/braces ; https://registry.npmjs.org/tailwindcss ; https://registry.npmjs.org/micromatch ; https://registry.npmjs.org/fast-glob ; https://tailwindcss.com/docs/compatibility ; https://tailwindcss.com/docs/upgrade-guide ; https://github.com/paulmillr/chokidar#upgrading

Production's inherited BUILD37 exception is not a fix. This package adds, extends or copies no exception and modifies no dependency, CSS, PostCSS, browser setting or Security gate.
