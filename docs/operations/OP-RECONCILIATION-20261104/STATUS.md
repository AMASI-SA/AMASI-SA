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

## Combined implementation checkpoint

- The payment table now aggregates order count, gross payments, configured fees, refunds and the remaining net balance. The separate fee table is removed. Gross allocation authority remains the default for financial mutations, with an explicit net mode for report presentation.
- Post-start payment claims can reconcile against a uniquely identified opening whose payout allocations are complete. Target-order conflicts and unallocated payouts retain review requirements.
- Driver and courier fee discovery uses delivery time at or after the operational start. Previously computed report time no longer places an otherwise eligible delivery on hold. Native fee and payout overlap checks protect first recognition.
- Native bank transfers use saved owner bank-routing aliases and the exact current paid bank leg. Customer wallet contributions remain separate from the bank credit.
- Coherent customer bank and COD balances are readable while fulfillment acknowledgement is pending. Owner, order identity, exact source version and the report cutoff are checked.
- Focused local checks passed for 130 frontend cases, 123 provider-fee cases, 279 delivery/source cases and 80 bank/catalog/source cases. Real Mongo regression execution is pending the combined CI run.
- An independent review found the default provider report could reduce the gross amount available to settlement allocation. Net reporting is now opt-in and two real-Mongo regression cases cover explicit fee settlement and automatic gross allocation.
- An independent review found a future customer source version could enter an earlier report. An explicit report-time bound and a regression case now cover that path.
- The earlier checkpoint `f3557945a1f9865ec0ad49fb16e68986df5ba532` passed CI run `41857831575` with 2238 backend tests, 16 shipping tests plus 18 subtests, and 372 frontend tests across 34 suites.
- Local Mongo startup returns `open Operation not permitted`. GitHub CI supplies the isolated Mongo 8.0.12 replica set for integration validation.

## Next action

Run the combined checkpoint through the full existing operational CI, correct failures, review the exact candidate and prepare the governed release. Production has not changed in this task.
