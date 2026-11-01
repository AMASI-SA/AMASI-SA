# Operational driver noncash review — verified candidate, not deployed

Repository: AMASI-SA/AMASI-SA. Branch: fix/operational-financial-followup-20261101. Draft PR #1677 targets the parent task branch, not production. Parent task #1675 at a8ffd5a81637bed8c4462f7bb4c2b059782cd520 is unchanged.

## Recoverable source identities

- Previous payroll/UI evidence head: 9646a14f710f13aaae91b09e98c0db05d3c379b4, tree 4f964978948c7a7d000aaeb5f72deabf7b8172e9.
- Noncash application checkpoint: fbedc0341caa101cb8094b0a06d23d056cb0157e, tree 9607c3253f0fb66703610b3c4dfbf34ecac03716.
- Tested follow-up source: **8322d15443dc2bebf50710a808f16adae84c9ce6**, tree **96e554132dcfebd5b19d864159134e97cac72bbd**. Its only changes after fbedc034 are two relative test imports; application code is identical.
- This record is an evidence-only follow-up. No application, test, workflow or dependency code changes accompany it. The current PR head identifies this documentation checkpoint without embedding its own future SHA.

## Owner decisions retained

Payroll begins on the operational cutover's Riyadh calendar date itself, inclusive, with one full daily share. It does not begin on month day one or the following day. Daily share is monthly salary divided by the actual number of calendar days, with existing employment eligibility, salary-effective dates, leave and cumulative rounding. Existing tenants require reviewed owner activation; no live activation has been performed.

Supplier invoices approved after startup and actual delivery fees earned after startup do not depend on the customer's order creation date. Driver cash and carrier COD remain receivables from their actual collectors. Merchant bank/POS receipts are separate from driver cash custody and from delivery fees. None of these source changes authorizes rewriting opening balances or replaying old sales/provider captures.

## Implemented noncash path

The existing driver payment-review endpoint and page dispatch by explicit financial phase. Operational approval uses a separate real snapshot/majority Mongo transaction, the owner coordination fence and the operational aggregate revision. A narrow capability wrapper rejects unscoped reads and undeclared writes. There is no standalone/mock transaction fallback, native journal capability or expansion of operational_atomic profiles.

Approval pins actor scope, financial source and review revision. It requires a unique owner-scoped canonical order, actual delivered assignment and collection, post-start event times, positive SAR amount, bound receipt bytes/hash and exact destination evidence. Contradictory, stale, foreign, already-consumed or native-posted evidence is rejected. A completed exact retry validates its retained monetary effect and cannot repost money, including after native activation; its coordination fence may still advance.

Bank transfer approval creates one bank-owned incoming operational collection and atomically consumes the proved imported statement row. Original XLSX bytes/hash/row/reference/bank/amount/direction must agree. POS approval creates one confirmed receivable for the selected documented POS identity, not bank cash. The existing operational movement/allocation path subsequently settles that exact receivable. Neither approval changes the delivery fee, supplier cost, sales, tax, opening balances or driver cash custody. Rejection has no monetary effect. A resubmitted review revision does not inherit a previous decision.

The UI explicitly loads approval options and shows the financial phase. It locks duplicate clicks, retains the exact request after an ambiguous transport/5xx result, clears invalid authorization and suppresses stale responses. The financial report validates sealed approval facts against actual saved movements/obligations before linking the receiving account. Displayed collection is not added again to totals. Account names are wired into screen, print and Excel. Missing fee snapshots remain explicit; conflicting financial effects withhold net totals.

The previous report implementation is preserved byte-for-byte as operational_financial_position_core.py (blob 56879afb81952ea1aa8e0362cfba6923926c0b0e), with an ordinary imported composition wrapper. No runtime source substitution or application monkey-patching is used.

## Exact-source verification completed

### Combined operational candidate

Run **41328442621**, source 8322d15443dc2bebf50710a808f16adae84c9ce6, both jobs completed success:

- Backend job **124913221468**: **977 primary tests and 18 subtests passed**, zero failed, error or skipped testcase elements. Includes all five existing real-Mongo payroll acceptance cases and 14 new report-effect checks. JUnit reports 995 including subtests; do not count that as 995 primary tests.
- Frontend job **124913221189**: **289 tests / 23 suites passed**, no failed or pending tests/suites. Includes all 14 new driver payment-review component cases and prior payroll, operational, supplier and report tests.
- Backend artifact **13091959324**, ZIP SHA256 **173713cc1c9827f45deea1fca5ae8d2a8695435428aed56e667197f24009519b**: independently downloaded, hash-checked, source-SHA checked and XML parsed.
- Frontend artifact **13092730594**, ZIP SHA256 **ab7fde7e8523fe5b8e97aa375a5379f195b9125bfb11c71ef623c9ba4b879414**: independently downloaded, hash-checked and JSON parsed.
- The combined candidate backend command does NOT include the new noncash Mongo module by its filename glob. Its transaction acceptance is established by the separate Store Delivery run below, not this count.

Evidence: https://github.com/AMASI-SA/AMASI-SA/actions/runs/41328442621

### Store Delivery transaction and native-regression acceptance

Run **41328447646**, both backend and frontend-build jobs completed success:

- Backend job **124913237007**: **185 tests passed**, no skips/failures/errors; six deprecation warnings, not failures. Existing Store Delivery tests include 53 new source/capability cases and all 13 new parameterized real-Mongo approval cases through the payment-evidence module's explicit imports.
- Real-Mongo cases cover simultaneous identical bank/POS approvals, preservation of the old order's fee and openings, exact retry after phase transition, full rollback after a late write failure, phase/scope/revision/authorization/cutoff failures, partial POS settlement, actual HTTP dispatch/retry and rejection without money.
- Mongo 8.0.12 ran on localhost in the existing CI's disposable replica set. Tests use unique synthetic databases and drop them in finally blocks. No production credentials, financial data or databases were used. Concurrent worker refresh/native activation races beyond the actual tests are not claimed proven by this run.
- The Actions checkout was synthetic PR merge **2abd72ad77f69ba22bf0223a5feb42510b58cbe3**. Its Git tree was independently fetched and equals **96e554132dcfebd5b19d864159134e97cac72bbd**, exactly the tested source tree. It is not a production merge.
- Frontend-build job **124913236680** completed its existing build and driver cash-confirmation/retry tests. This is CI build evidence, not a governed production release or browser acceptance.

Evidence: https://github.com/AMASI-SA/AMASI-SA/actions/runs/41328447646

The first Store Delivery attempt on fbedc034 failed before collection because the imported test module name was not package-relative. Commit 8322d154 corrects only those two test imports. All 81 cases in that module collected locally; 68 non-transactional cases passed locally with the 13 Mongo cases intentionally deselected there, then all cases ran in the successful real-Mongo CI. The failed run was 41328119996; it is not hidden or counted as acceptance.

### Source read-back and local checks

Source-review run **41328442505**, artifact **13092571034**, ZIP SHA256 **53320c0920994d0a7e675f52bd78fe15a5c84ef400004d9c79a3cc4a07ed2643** was independently downloaded and checked. Its recorded source SHA matches 8322d154. All **4,563** included repository files match their Git blob hashes, all **15** changed files match the local reviewed bytes, and the complete manifest reconstructs the exact source tree above. Local snapshot Git HEAD is not the remote source SHA.

Selected local regression: **418 passed**, zero skips/failures/errors, with isolated dependencies and mock reads where appropriate. AST/whitespace checks passed. Local counts overlap CI and are not additive. No CI workflow, application dependency, production environment or permission was changed in this noncash application slice.

## Remaining limitations and release gates

1. Date-only imported bank rows on or before the cutover day remain blocked for opening-coverage reconciliation. A later approval timestamp alone cannot prove that the bank receipt was excluded from opening cash. This restriction does not suppress earned delivery fees. No guessed timestamp or manual opening override was added.
2. Existing historical approved noncash records are not silently replayed. Current report-wide completeness remains conservative; a verified row/account link is not proof of full historical coverage.
3. Complete pre-start expected-COD and common-period profit evidence remain unfinished. Unsupported totals remain unavailable, not zero.
4. Browser visual, print/export interaction and end-to-end production build/security acceptance remain separate. Rendering/export code and unit tests are not proof of actual browser download/print completion.
5. Live v5 publication of the cost prerequisite has not been freshly established in this slice. PR1676's earlier merge alone is not publication proof. Parent1675 must be integrated with the exact published cost/security changes in isolation and re-tested before any release.
6. Actual owner payroll opening-coverage review/activation remains separate. No real payroll or receipt approval was executed.

Production changed: **NO**. Live balances, salaries, openings, ledger, /app, release intent, leases, parent branch and deployment: **untouched**. Keep PR1677 Draft and unmerged. Passing these selected tests is not acceptance of the entire financial-report project.

Next safe action: read current PR1677 head and this record; preserve the exact verified source while completing the unfinished COD/financial coverage and visual checks. Verify the prerequisite's live release identity before production integration; re-run combined tests/security/build on any integrated source change. Issue1006 is full; this record and the draft carry continuity.
