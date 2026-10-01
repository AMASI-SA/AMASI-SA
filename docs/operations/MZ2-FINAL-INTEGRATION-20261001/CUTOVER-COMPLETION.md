# Existing-path MZ2 cutover integration

Scope starts at `0a9f1ac30d2b1a3e8aee21aab1da0ae364a74697` (tree `c8421a4cfc434ca747727d4673f25766d4ba2fef`). Latest user authority allows completing missing native producers/adapters for existing operations. New business features remain excluded. This is a task-branch checkpoint, not release approval.

## Writers and wiring

- Salla/provider sale and order recognition, refund entitlement/payment and provider settlement now call the existing sealed native core through `accounting_recognition_native`. Original tax, dates, fees, partial-payment limits and event identity are preserved; native source journals are verified on replay and reversed sources fail closed.
- Bank-transfer receipts, delivery conversion and customer advances use `accounting_customer_native` with existing evidence and bank identities. No premature sales tax is added to advance capture.
- Track B payroll accrual, salary/advance/custody movements and daily expenses use `accounting_employee_outgoing_native`. Existing proration and distinct payable/advance/custody balances remain unchanged.
- Daily supplier payment binds the canonical C identity and calls delivered C1 settlement in the same transaction, with exact movement consumption and payable-only policy. Delivered C2 invoice behavior is retained.
- Driver bank review resolves preserved original bank statement bytes/row/hash through `accounting_bank_statement_proof`, then calls existing Track F payment writer. COD principal and delivery fee remain separate; no netting.
- Advertising funding/payable payment uses delivered Track E posting plans, FX/original-unit contract and sealed core, with distinct original principal/fee evidence. Explicit foreign-wallet zero confirmation requires approved native zero opening and source evidence; it does not fabricate an exchange rate.
- Native reports resolve the existing dynamic refund/advance/expense identities and exact Track E confirmed financial binding. Onboarding uses that same binding and requires only the financial accounts actually used by the approved funding mode.
- Operational settlement audit now writes `mz2_settlement_audit`; journal audit remains the core's immutable native audit. Actual HTTP post failure at operational audit insertion rolls back the journal, draft and bank receipt.

## Verification and corrected failures

Tests use unique disposable databases on task-owned loopback Mongo replica/standalone servers. Production was never a test target. The native core remains the only journal storage writer.

| Failure or requirement | Correction / evidence |
|---|---|
| Existing operations hit Legacy writer 423 after cutover | Native adapters added; existing recognition/refund/payroll/expense/customer tests now exercise sealed native opening and native balances. 423 remains enforced for Legacy posting. |
| Shipping test expected COD150 closed by bank130+fee20 | Previously corrected to Track F separate movements; shipping regression and Track F CI remain mandatory. |
| Bank/COD and settlement unit tests patched removed Legacy seam | Native test seam and stored owner identity; amounts, cutoff recheck and no-netting assertions retained. |
| Legacy opening test expected activation | Now proves native gate rejects Legacy opening; separate sealed native test proves balance/reportability/idempotency. Full native opening HTTP lifecycle remains covered separately. |
| Historical tests read quarantined Legacy reports | Migrated to authoritative native reports and real append-only reversals; original-period balances and permissions preserved. |
| Expense/advertising journals unclassified in reports | Existing category/binding contracts connected; real cross-domain reports show balanced totals. Invalid identities still block all totals. |
| Refund period journal omitted native reversal | Confirmed real-Mongo before: period liability−115 while ledger0. Verified original-leg linkage now includes reversal at its own effective date; original month remains unchanged. |
| Provider post route wrote Legacy operational audit | Actual HTTP before:3 Legacy audit inserts. After:0 Legacy IO; single native post audit, replay has no extra effect, database-level audit failure rolls back all financial effects. |

Fresh root broad Accounting/G47 run: **597 passed, 14 old-fixture failures, 304 subtests**. All 14 failures were corrected and covered by the subsequent **55 passed +29 subtests** cross-domain run. This is not misreported as a single all-green broad run. Adjacent native audit regression:57 passed; root direct audit tests:4 passed. Ads/bank evidence:137 passed; zero-opening guards:25 passed; affected UI:18 passed. New ad reports:5 passed; onboarding identity regressions:91 passed. Exact logs and hashes: [CUTOVER-EVIDENCE.json](CUTOVER-EVIDENCE.json).

Whole frontend: **1263 passed /21 failed**, 226 passed /9 failed suites. All nine failures reproduce at the baseline with identical normalized runtime diagnostics under the same Node22/Yarn1 dependencies. They remain unwaived; [baseline analysis](evidence/cutover/FRONTEND-BASELINE-COMPARISON.md) distinguishes stale contracts, missing test dependency and unresolved behavior.

## Remaining gates

[REMAINING-BLOCKERS.md](REMAINING-BLOCKERS.md) records every original blocker and its classification. POS processor-success ingestion is the only proved absent operational source requiring new scope; independent cash/bank accounting succeeds. POS stays unavailable.

The exact source checkpoint is `b24c20c0c33974d7bcb49ca441de8a267e4a685b`, tree `2a3c79efc2456ec1a1126f81f4799421301784a9`. [Complete CI](CUTOVER-CI-FINAL.md): **35 successful / 1 failed / 0 pending across36 workflows**. All Accounting jobs and their steps, Shipping/Track F, G47, Ads and A+B connected browser acceptance passed. Intermediate CI failures exposed isolated fixture-import paths, an outdated unittest runner for pytest tests and missing existing shipping runtime dependencies; the final source checkpoint contains their fixes. [CI history](CUTOVER-CI-HISTORY.json) retains failed/cancelled attempts without counting them as passes.

The sole CI failure is Release v5 candidate ancestry/intent validation. It and inherited whole-frontend failures remain unwaived release blockers. Complete live business UAT and production Smoke B were not performed under the explicit no-activation/no-production-write restriction. Documentation-only descendants preserve these application/test/workflow sources; their exact HEAD/TREE and own CI completion are recorded in Issue #1006.

[Changed files](CUTOVER-CHANGED-FILES.tsv) lists all changes since the user's baseline. [STATUS.json](STATUS.json) separates current results from superseded wiring-only findings. Task-owned loopback Mongo fixtures were gracefully stopped after verification.

Production writes=0; merge to production=NO; deploy=NO; production Opening Post=NO; activation=NO; write-control unchanged. All local opening/reversal/posting activity described here belongs solely to disposable test fixtures.
