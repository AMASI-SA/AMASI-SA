# Combined native payment-review compatibility — not deployed

Parent source: 712db7533edb3606b6b02279e27219e3153cab3e, tree c3fd4da9bd5116bbf2a0f43d460a7fb4ea6f41e3. This is a narrow follow-up to COMBINED-REGRESSION-20261102.md; the complete STATUS.md scope and all financial/release restrictions remain.

## Exact-parent acceptance verified, not final project acceptance

Operational Balance Web run 41458241266 completed successfully on exact source 712db753. Backend job125320346385 executed real isolated Mongo8.0.12 and the combined selected contracts. Official artifact13148072361 ZIP SHA256 b47a253b1b3da20f1ac889aa6c16d054c90ad8fdb77e8b711faccb98844f5e36 was independently downloaded, hash-checked and parsed: 1363 testcase elements, zero failures/errors/skips; embedded source SHA and tree match. It includes five real-Mongo payroll cases, thirteen real-Mongo driver review cases, two provider refund integration cases, thirty supplier cutoff cases, ten final-cost cases and thirty-two new capture-proof boundary cases. These suite counts are subsets, not additional tests.

Frontend job125320346532 passed. Official artifact13147897269 ZIP SHA256 29355679a7181fa10480dcfc726bedf94eccb75a223fbd44812f20e5e1545965 independently checked and parsed: 300 tests /25 suites, zero failed/pending/todo. This slice does not include every application frontend suite.

The broader Security Gate run41458241326 exposed four failures in legacy StoreDeliveryPaymentReview.test.jsx; 2874 other tests and340 other suites passed. Frontend audit/build steps after the failed test were skipped, not accepted. Accounting frontend was also unsuccessful. Store Delivery run41458241212 backend stopped before tests because Docker Hub returned502 downloading mongo:8.0.12; frontend build/tests succeeded. An infrastructure failure is not passing backend acceptance. Stock Eligibility, CodeQL and Release Readiness succeeded but do not supersede those failures or establish deployment.

## Reviewed compatibility correction

The combined page now requires explicitly loaded approval options with financial phase, review revision and actor scope. Legacy native-accounting UI fixtures did not provide or load that contract, so approvals/rejections remained intentionally disabled. Update the fixtures to exercise the actual native option contract and assert all three pinned fields on outgoing requests. The backend still strips that envelope before the existing native payload fingerprint; it is not changed here.

Restore the native documented POS identity label (entity type/id/subaccount) alongside its name. Operational destination options without those native fact fields do not get a guessed label. No destination eligibility, amount, receipt, permission, retry or financial writer rule is weakened.

All existing malformed-amount and missing-receipt tests now load valid context before testing their intended rejection, avoiding vacuous success due merely to missing context. Keep rejection cancellation and exact payload assertions. Add a native regression for approval blocked before option load and after a failed fresh read. No assertions are removed and no tests are skipped.

## Verification and limits for this follow-up

Local source diff/whitespace and Git blob equality checks passed. Frontend dependencies are not installed locally; execution is required in existing exact-head CI. Prior 1363/300 counts apply to the parent source only, not automatically to this UI change. Re-read full Security, Accounting, Store Delivery, Operational and CodeQL checks on the new head and inspect any failures. No dependency/workflow change is included in this follow-up.

## Still blocked / activation boundary

Keep provider_postsettlement_refund_review_required and statement posting_allowed=false. Actual post-settlement fee/tax-credit allocation, partial/multi-order settlement linking, real captured/plan evidence, approved policies and opening basis, strict per-piece SAR/status/current-carrier requirements, customers/external loans/exchanges, genuine warning remediation, full forecast/profit, browser/export and live read-only differences remain in STATUS.md. This correction does not complete or activate those functions.

No live database, balance, opening, refund, payroll/policy activation, original-branch change, production merge, /app, lease, new A/B release intent or publication. Restricted local COD archive is untouched. Preserve one final combined release after every required acceptance and approved financial boundary.
