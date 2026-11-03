# Supplier invoice comparison continuation — not deployed

Repository AMASI-SA/AMASI-SA, integration/operational-balances-unified-20261102, Draft PR1687. This additive checkpoint starts from f25db073815693f72f9fc963c8950c2da99cb4cd. Latest production read remains 3d8720860fb524d729f6c83a8c56c5b4de3a2afb. Preserve STATUS.md and PAYMENT-COMPATIBILITY.md and all original task branches.

## Remote checkpoint scope

Adds only a pure read-only comparison module, its 46 synthetic tests, and this record. No existing application source, engine, collection allowlist, route, workflow, dependency, release intent or original branch is replaced. The new module is NOT connected to a live endpoint or background worker and performs no I/O. It does not activate supplier recognition or correct balances.

The comparison takes owner-scoped saved/proposed snapshots and original native invoice headers. It compares approved invoices within the saved operational cutoff window, inclusive of the cutoff instant, by stable invoice/piece/component identities. It shows current and proposed gross/confirmed/allocated/outstanding/overpayment amounts and the exact difference. Original headers and exact component coverage must agree; missing or contradictory evidence withholds the aggregate instead of inventing zero. Legacy metadata can be recovered only through the same obligation/receipt identity, not guessed prefixes. Opening/movement changes and rewrites of confirmed facts are rejected. Unrelated financial changes are disclosed. Shipping-only warnings do not themselves invalidate complete supplier invoices.

All outputs retain posting_allowed=false, financial_correction_approved=false and opening_coverage_verified=false. An arithmetically complete comparison is not owner approval or proof that a new amount was excluded from the opening balance.

Exact reviewed blobs: module 1ce00d37850ca1358634b7b92a0a59cfb11ff25a; tests 550d9da1ec547cb9ae0fcdd09856f57219013779. Local pure test result: 46 passed, zero failures/errors/skips. Fresh CI for this new head remains required. Earlier successful f25db073 checks are not acceptance of this addition.

## Separate local-only supplier correction

Resumed the conversation attachment mezan_unified_payment_review_20261102.zip, validated all 71 file hashes and clean-base patch application on the archived production source. This local combined snapshot includes older payment normalization and is NOT asserted identical to the newer f25db073 integration branch. The current remote payment fixes are preserved; no local old file was copied over them.

A new regression reproduced an estimate-residue defect: a fully invoiced native piece with estimated cost 50 and actual cost 40 still retained expected cost 10. Seven initial focused cases failed, across SAR/QAR/KWD/AED customer currencies, repeated reconciliation, returns and above-estimate actual cost. The local fix marks only fully validated native invoice components as fully invoiced and stores that provenance on obligations, never changing the frozen receipt fact payload. Such pieces no longer retain a residual estimate; another uninvoiced piece and legacy partial receipts retain their legitimate estimate. A final invoice above the old estimate is not itself missing evidence. Ten focused cases pass after correction. One old test assertion was changed from residual 10 to 0 because it encoded the reproduced defect; evidence guards were not removed.

This local change affects operational_balance_sources.py and operational_balance_engine.py plus its supplier tests. Those changes are NOT part of this remote commit. They are preserved in the conversation's supplier review package for later hunk-level integration. No restricted COD archive content was loaded, retried or uploaded.

## Local evidence and limits

Separate full source-adapter/reducer calls on the archived production and local candidate, with a deny-write synthetic DB, reproduce a mixed old/new-order invoice: gross 80, recorded 40, settled 25, current remaining 15; proposed gross 80, same settled 25, proposed remaining 55; missing amount 40. Repeat candidate reconciliation adds nothing. These are synthetic numbers, not merchant balances or a live before/after reconciliation.

Selected local battery: 464 passed, zero failures/errors/skips in the explicitly unit/source/mock selection. It includes 46 comparison tests and 10 new final-cost cases; counts overlap earlier runs and are not additive. It also covers supplier cutoff/account view, source/engine/open-cost/report, cutoff-day salary, delivery evidence, component fees and the pure post-settlement guard.

An expanded attempt had 464 passed and one real-Mongo test fail at connection setup: 127.0.0.1:27305 connection refused. The native rollback test remains unchanged and was not disabled. The later 464-case selection explicitly names the pure guard case and does not claim real-Mongo acceptance. A local Mongo binary could not be retrieved. Preserve the failed log and XML. No browser, frontend build, locked full CI or real transaction acceptance was established by these local results.

## Remaining and next safe action

Read the newest PR1687 head before changing files. Verify the two additive blobs and exact-head CI; wire a reviewed owner-scoped consistent-snapshot preview only after integration design, not a write action. Combine the local supplier correction and preserved #1675/#1677 work with the newer native-payment source by reviewed hunks and run combined regression/real-Mongo/UI/security/build gates. Do not claim the local saved candidate already contains every newer remote change.

Live Mezan invoice headers, recorded components, opening coverage, source snapshot and missing amounts still require read-only comparison. Any financial correction requires the owner's explicit review of that difference. The post-settlement provider blocker, actual payment evidence, product SAR/status rules, customers/loans/exchanges, warning causes, final forecast/profit and unified release gates remain in scope. Preserve provider_postsettlement_refund_review_required and posting_allowed=false.

No /app access or release lease change. No source refresh on production, financial correction, opening reset, historical replay, policy or payroll activation, bank/provider call, production merge or publication. One combined final release only; this checkpoint is not permission to publish separately.
