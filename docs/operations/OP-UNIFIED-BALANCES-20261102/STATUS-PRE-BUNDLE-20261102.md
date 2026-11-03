# OP-UNIFIED-BALANCES-20261102 — combined acceptance and remaining gates

Status: DRAFT / combined application slice verified, business completion and publication blocked. This record does not authorize financial activation.

## Authority and current source

The owner requests one final combined release, preserving all work and subsequent production updates. Code publication does not authorize opening resets, historical transaction replay, real refunds, undisclosed financial corrections or automatic payroll/provider-policy activation. The original task branches remain unchanged.

- Repository: AMASI-SA/AMASI-SA. Integration branch: integration/operational-balances-unified-20261102, Draft PR1687.
- Latest production incorporated: **70576e2a06237eb193b1c927f11ced92e2229589**, source release #1688, following the earlier customer compensation release #1686 at 3d8720860fb524d729f6c83a8c56c5b4de3a2afb. Never reset production to an older base.
- Combined application before the latest production merge: **c5c21261a8e3fe1850e5de923f6858efef87eb2e**, tree **e66a06703df71e8e1ccda5f16b606555cfb47c2e**.
- Current tested integration merge: **5b16ea6b84f0926b34f578ca30737d6173417156**, tree **9cf1b9bf88fc1236e7a10a749474d981b4ab0ac2**. Its two parents are c5c21261 and production70576e2a. This documentation-only checkpoint follows that merge; no application, test, workflow or release-intent code changes accompany it.
- The integration tree equals GitHub's previously tested synthetic merge dd14a87aefe2c07cbc52fb422a7d8f2f0c8aadf1. Equality was checked using full tree IDs and both parents, not inferred from an empty three-dot comparison. Default-checkout workflows on c5 had already consumed that newer production merge, whereas Operational Balance Web explicitly checked out c5 itself. Keep those source identities distinct.

Preserved saved work: #1675/#1677 (follow-up df17ef3ff21e2515ed24ec61d5d8019cf9cb2fe5), provider #1685 (88c7872072e6e6a149a92153a732ae4c0551ae21), latest native payment normalization f25db073815693f72f9fc963c8950c2da99cb4cd, supplier comparator b96627156b522637edd2b6dbf0498a386b1abfff, and published #1686 compensation. Product #1678 remains d5b301506912a481196af295b0812f3759d7a27f with only a review workflow, not its requested runtime fixes. Child1689 is donor/overlap review, not a separate release.

The restricted local COD forecast archive is NOT included, loaded, retried or uploaded. Preserve its earlier restriction; no alternate endpoint, encoding or execution route may bypass it. Previously permitted delivery/COD-custody code is a different source slice.

## What is integrated, without live activation

1. Supplier invoice approval after the saved cutoff no longer depends on customer-order creation age. Stable invoice/piece/component identities retain saved payments, returns, openings and immutable receipt facts. Fully invoiced native components replace estimates: estimate50/actual40 leaves no expected10. Genuine uninvoiced pieces and legacy partial receipts retain their estimates. Supplier account view and pure before/after comparison are included; comparison remains non-posting and is not yet a live consistent-snapshot reconciliation endpoint.
2. Delivery earned after cutoff has an independent old-order evidence path without replaying the old sale/provider capture. Earned fee, driver cash custody, bank receipts and POS receivables remain separate. Existing atomic noncash review, exact retry and report linkage are included. Latest #1688 current-courier status/visibility code is preserved without replacing financial files.
3. Payroll starts on the saved Riyadh cutoff calendar day itself with a full daily share. Owner preview/approval UI and opening-coverage confirmation are included. No live payroll activation or missing-day posting has been executed.
4. Provider component fees from #1685 remain opt-in. Native aliases, scoped missing-evidence diagnostics and explicit plan propagation are included. Actual capture status/time is not invented from order creation or fulfillment. Missing authoritative real capture/plan ingestion remains a blocker.
5. Published #1686 compensation, original-platform restriction, independent manually chosen bank source and no automatic refund on an order-value edit are preserved. Already-reduced170/150 orders remain readable. A new provider refund still needs a valid original capture basis.
6. A narrowly validated repeated-refund correction preserves an unchanged capture through generic order cancellation only when current dated capture, saved same-order/provider refund snapshot and immutable capture/refund facts agree. Changed totals, references, dates, plans, parties, explicit voids, unknown refunds and missing facts remain blocked. This does not implement post-settlement fee credits.
7. Supplier/payroll/financial report/driver review UI contracts are combined. Native POS identity labels are retained. Approval requires explicit fresh financial phase, review revision and actor scope. Native payload compatibility, duplicate clicks, ambiguous retry, stale responses and invalid destinations remain tested.

## Exact-source combined acceptance

### Latest incorporated production: source5b16ea6b

Operational Balance Web run **41463322712** completed success. Its backend and frontend explicitly check out the PR head.

- Backend official artifact **13148934083**, ZIP SHA256 **9bc72d291a2fcb7b0e46ac33bb864180c4dd0e19a82211ec1ed2cbfa3af6a010**. Independently downloaded, hash-checked and XML parsed: **1363 testcase elements, zero failures/errors/skips**. Embedded source SHA5b16ea6b and tree9cf1b9bf match exactly. The workflow starts isolated Mongo8.0.12; no production credentials or databases are involved.
- Frontend official artifact **13150472605**, ZIP SHA256 **9f1825307d812f418930d55977fb9aa5836df3d711d318fc0901d77199d27a92**. Independently downloaded, hash-checked and JSON parsed: **300 tests /25 suites passed**, zero failed/pending/todo. This is the operational selection, not every test in the repository.
- Real-Mongo subsets include five payroll cases, thirteen noncash review cases and two provider refund integration cases. Supplier cutoff30, final-cost10 and capture-proof32 are included subsets, never additional to1363.
- Fresh returned workflow list for5b has **22 successes and one failure**. Security, Accounting, Store Delivery, Stock Eligibility, Supplier Payments, CodeQL and other returned workflows succeeded. Mezan Release Readiness **41463322572 failed** at commit classification: the merge imports both newer production source and its inherited release intent, so its HEAD^ diff is not an intent-only release commit. Log: `intent commit modifies governed source`. No readiness success is claimed for that merge.

This merge is a source integration checkpoint, not the new final intent-only B. Do not remove its newer production source, rewrite history, edit the inherited intent to manufacture success, weaken the classifier or reuse that intent for publication. This meaningful source-status checkpoint records the evidence and changes documentation only; its fresh source/build checks must be read separately. The final A/B release contract remains a future gate after the business blockers below are closed.

### Earlier acceptance and repaired failures retained

- a7fc467c8a129107d8efb9a4fc64833e827f0b74 initially failed combined operational CI: second provider refund after cancellation, obsolete supplier residual-estimate assertion, absent existing email-validator test dependency, and leaking UI mocks. These original failures are preserved in COMBINED-REGRESSION-20261102.md. Tests were not skipped or financial guards weakened.
- Repair712db7533edb3606b6b02279e27219e3153cab3e passed1363 backend/300 frontend in run41458241266, but broader native-review UI fixtures still failed and one Store Delivery attempt stopped at Docker Hub502 before backend tests. Those were not accepted as passing.
- Native compatibility c5c21261 passed exact-head operational run41460760040: backend artifact13148996998 SHA256 fbb6c9abdabb2a7b7c174432df70cc7debd105db7ee4045294aca147f5d97353; frontend artifact13148473305 SHA256 dedc063c7ef1bb9aad1b8ec9d180999ac7f3c5e7b3367d20791985e97565b297. Counts1363/300 independently parsed. Its23 returned workflows succeeded, with the default-checkout/source distinction stated above.
- Local483 selected Python3.13 unit/source/mock tests passed before remote acceptance, including32 new capture-proof boundaries. These overlap CI and are not additive. Local Mongo and frontend execution were unavailable; real acceptance comes from the explicit isolated CI results, not a mocked replacement.

## Complete business scope and activation inventory

| Scope / references | Implemented and tested in combined slice | Still required / actual activation |
|---|---|---|
| Supplier invoices #1675/#1677/#1687 | Independent approval cutoff, full-component final cost, preserved payments/facts, account/report and pure comparison; collected in real combined CI | Read actual owner-scoped saved/proposed invoice snapshots and opening coverage. Explain each missing or partial amount and obtain explicit approval before any new financial recognition. Not activated. |
| Products #1678 requirements | Existing cost-exposure and supplier final-price correction preserved | Strict exact awaiting-review/in-progress eligibility at piece level and SAR estimate basis are NOT fixed. A24-case synthetic source/reducer/preview diagnosis still reproduces completed/reviewed inclusion and foreign customer currency being assigned to SAR product cost. Old foreign-payment orders are suppressed by historical currency gating. No live correction. |
| Shipping/drivers/COD #1677/#1688 | Old-order postcutoff delivery, actual executor proof, bank/POS/cash separation, latest current-status driver behavior; real transaction and UI regression coverage | Complete current-carrier reassignment/full coverage and actual money matching. Expected COD source archive restriction unresolved. Do not interpret uncollected COD as cash. No new activation or movement. |
| Tamara/Tabby and other providers #1685 | Opt-in component/VAT model, native method routing, explicit plan propagation, repeated-refund proof and read-only statement arithmetic | Actual dated capture/plan/void/refund pipeline, approved per-plan merchant terms, post-settlement actual fee/tax credits and partial/multi-order allocation/bank links. Keep provider_postsettlement_refund_review_required and posting_allowed=false. No policy activation. |
| Customers/external persons/loans #1686 and existing flows | Published compensation/source restrictions retained, combined return and native-review tests pass | Explicitly validate all four loan/debt/cash directions, debt creation vs repayment, receive-in/pay-from labels and before/after balances; full exchanges/returns/shipping cross-path dedup evidence. Not considered complete merely because inherited tests pass. |
| Payroll/openings #1677 | Inclusive cutoff day, exact daily share and owner preview/approval; five actual Mongo cases and UI suites pass | Review saved opening coverage and read-only missing amounts before owner activation. No day-one backfill performed. |
| Warnings #1676/#1687 | Aggregation preserved, source-specific payment reason/reference/action present; missing data not zero | Resolve actual Salla-source, carrier/rate, product, provider fee, advertising start-day/FX and salary-contract evidence. A shipping-only gap must not erase complete supplier/product evidence. No blanket readiness or hidden warnings. |
| Financial report #1675/#1677 | Combined tables, supplier/delivery/account linkage, exact amounts and testable print/Excel code | Full common-period expected position/profit evidence and browser/mobile/actual print/download completion remain unproven. Unsupported totals stay unavailable. |
| Unified release #1687 | Latest production70576e2a preserved with two-parent integration and exact-source operational acceptance | Fresh current-head source/readiness/security checks, close business blockers and approved activation boundary, remove temporary export workflow, freeze newA/build/newintent-onlyB, check inactive lease, one publish, three live probes and active=false. Not published. |

## Production evidence and operational boundaries

Earlier owner-supplied #1686 verification: source d0532a057a33776ebe1b40f0d7cbf87a39bf7e4d, deployment3d872086, release rg5-e8d8970b2ab29f4358b856dad1facc6e35e464d1091ad800c0e8df13d844a396, verified=true/checks=3.

Newer #1688 record reports source b6c6e42fce74d8900526a81e1d5a88be3cc297c4, intent98630af107939536ea630f51ef1558cf69fc1361, deployment70576e2a, release rg5-cc743775a823588ac1de627e70855d9c29682ad31d1d97d2dce3d084dfce8f81, successful publication/three live checks and active=false. This is the other task's recorded verification, not an assistant-executed host probe or verification of #1687. Do not repeat its publish. Read actual guard status again before any later /app or release work.

Deploying changed readers could recognize previously omitted approved supplier/delivery events during normal refresh. Therefore publication cannot serve as silent financial backfill: complete read-only differences, opening review and an explicitly approved activation boundary before final deployment.

Production changed BY THIS TASK: **NO**. No original task branch changed. No live database, opening, payroll/policy activation, refund, bank/provider API, stock, /app, release lease or publication action. This turn changed only the isolated integration source/tests/docs and incorporated a separately reviewed production update into that branch.

## Next safe action

Read current PR1687 head and this record. Check the documentation-only source checkpoint's fresh CI without pretending the earlier merge classification succeeded. Close the proven product SAR/exact-status defect with focused per-piece tests, then complete actual payment evidence and post-settlement reconciliation, customers/loans and warning coverage. Maintain supplier/delivery/payroll behavior and all immutable monetary boundaries. Obtain actual saved-state read-only comparisons before financial activation. Full release A/B and one publication remain gated.

Preserve detailed history in COMBINED-REGRESSION-20261102.md, COMBINED-NATIVE-UI-20261102.md, PAYMENT-COMPATIBILITY.md and SUPPLIER-RECONCILIATION.md. Do not overwrite newer source with a local older snapshot. Local source files used for focused tests were based on c5; incoming production70576e2a was integrated remotely with reviewed tree equality and current CI, not falsely claimed present in that old local checkout.
