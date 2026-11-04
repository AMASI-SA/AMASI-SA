# Operational report reconciliation

## Scope

The owner requested a single payment-platform table with order count, customer payments, commission, refunds and the net balance after recorded settlements. Commission uses the saved legacy Mezan tariffs. Delivered driver assignments after the operational start accrue the configured delivery fee. Shipping balances and pagination are included.

The owner also requested USD advertising balances valued in SAR using saved account settings, with bank commission and VAT on that commission shown above advertising balances.

All report tables use 13 rows per page. The unconfirmed platform collection table uses the separately requested 10 rows. Full totals, exports and printed reports retain all rows.

## Working checkpoint

- Repository `AMASI-SA/AMASI-SA`
- Task branch `codex/operational-reports-reconciliation-20261104`
- Reviewed starting commit `84bd03dcd585f57b2b08bf93c8ac0fd8beb58463`
- USD advertising valuation uses saved account settings, converts the remaining native debt and prepaid credit, and applies bank commission plus 15% VAT on the remaining commission. Existing paid principal and paid bank fees are accounted for before valuation.
- Read-only report tables include the USD conversion breakdown above advertising balances. Native statements retain original currency amounts.
- Pagination and full print/export coverage are implemented. Frontend regression execution is pending dependency installation. Payment-platform consolidation and delivery corrections are in progress.
- Focused verification passed with exit 0 and 84 tests using `pytest --noconftest -q backend/tests/test_operational_balance_ad_sar_report.py backend/tests/test_operational_balance_ad_funding.py backend/tests/test_operational_balance_sources.py`.
- Draft PR #1748 carries the implementation checkpoint.
- Production has not changed in this task.
- The shared deployment workspace has not been modified.
- Issue #1006 has reached its comment limit. The task PR and this status document carry continuation evidence.

## Next action

Finish the independently owned source, delivery and presentation changes. Run focused tests, inspect the combined diff, then preserve a verified source checkpoint before release validation.
