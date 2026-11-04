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

The combined checkpoint `e0c2116a6095ce2051038195445782b07275162a` ran CI `41861464314` with 2306 passing backend cases and 10 failures. The new real-Mongo native bank-routing and provider settlement-authority regressions passed. All operational frontend tests passed. Seven failures identified an obsolete preview CLI dependency on the removed provider-fee table and old row identity. Three courier cases retained the previous hold policy expectations. The follow-up updates the CLI to the consolidated net provider contract and tests the inclusive operational-start behavior.

Independent fee review additionally covered fully evidenced settlement without configured estimates, cumulative foreign settlement rounding, and a full refund after an already settled capture. A refund overlapping a partial payout retains an incomplete net when actual fee-credit attribution is unavailable.

A prepublication readback identified that the saved provider payouts use the incoming `collection` kind with complete opening allocations. The overlap checks now accept that existing writer contract with zero collection fees, alongside valid settlements. Target-order, pooled, partial and excess allocations still require reconciliation. Synthetic native-currency, SAR-converted, page, capture and split-wallet cases cover the real source shape. The focused follow-up passed 247 cases, with three Mongo cases assigned to CI.

The unconfirmed collection table now omits claims whose matching saved page or booked split evidence proves complete payment, and rows with an already proved zero future amount. Partial, held or mismatched evidence remains visible.

The governed Node v22.23.2 and Yarn v1.22.22 toolchain is installed. The shared deployment guard was checked inactive, and the current production health reports the earlier reviewed performance source `00374911cee75a11f5b5bbc56952df2ae0852a75`. The production branch remains the reviewed base `84bd03dcd585f57b2b08bf93c8ac0fd8beb58463`.

Run the corrected checkpoint through the full operational CI, then bootstrap the governed build, freeze its intent-only commit and rehearse the actual Emergent adapter before the already authorized publication. Production has not changed in this task.

## Verified combined source and wallet follow-up

The checkpoint `e6e067b830b8908dde442a5ee6bad8fae9c9589c`, tree `329ee71b123cbcef9d7c064225b8753d5afd4ce2`, passed 2356 backend tests and 16 shipping tests plus 18 subtests in both GitHub and the local isolated Mongo 8.0.12 replica set. Adding `--nounixsocket` resolved the local Mongo startup limitation. The local source remained clean before and after validation.

GitHub job logs verify 372 frontend tests across 34 suites, 156 backend readiness tests, all 57 frontend release contracts and a reproducible governed A/B build. Stock eligibility passed 2400 tests plus seven subtests. Workflow status metadata is delayed and still reports queued jobs after their completed logs. The Node 24 dispatcher smoke test hit its 20-second Yarn process timeout and remains a release-readiness item for the next checkpoint.

The owner clarified that unused customer wallet balances are an obligation to customers. Salla's native wallet report provides the current remaining balance in the verified store currency. Redeemed wallet tender amounts already describe order settlement and do not post new bank cash. They must not be treated as the remaining wallet balance or deducted again from an already-current closing balance.

The wallet follow-up adds an owner-scoped, audited closing-balance snapshot with source reference and capture time. A later snapshot replaces the earlier liability. The report shows a dedicated customer-wallet payable and the dated evidence. This source is refreshed from a Salla wallet report, and an operational worker refresh alone does not advance its source date. No aggregate wallet-read route is being inferred for the existing Merchant OAuth integration.

The owner UI collects the remaining SAR balance, report timestamp in Riyadh and report reference. It retains the same request identifier on an unchanged retry. The customer-payment heading now describes amounts settled for orders, and the wallet line describes the credit redeemed. Focused frontend validation passed 102 tests including snapshot session binding, retries, response-scope mismatch and late responses after an owner change.

Publication remains pending this wallet follow-up. No release intent has been frozen and no lease has been opened for this task. Complete the wallet backend and HTTP regressions, verify the exact final source, then create the intent-only commit and perform the governed deployment rehearsal and authorized publication.

The completed wallet backend passed 84 focused pure, financial-position and provider regressions. Two real-Mongo HTTP cases passed for owner access, store/currency/session binding, idempotency and replacement. Default wallet projection observes the operational report cutoff, so a newer saved statement becomes reportable when the worker advances that cutoff. Original order currency does not establish a separate wallet-account currency. The store-currency closing remains the liability authority. The frontend payable/source-date regression passed alongside all 23 financial-position tests.
