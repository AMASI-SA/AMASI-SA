# Combined regression remediation — Draft, not deployed

Source under review: a7fc467c8a129107d8efb9a4fc64833e827f0b74, tree 00a46c19d7f4080de669b119d836ff16cf0129eb. Parent for this repair is be9146baf9b9c67f6aba723c5c2b9aba3a8153ce (source-review pin only). All earlier STATUS.md scope and release/activation limitations remain.

## Failures actually observed

Run41451573726 executed the combined backend against isolated real Mongo: 1328 passed, three failures, no claimed acceptance. Failures were a second evidenced provider refund after generic order cancellation, an old supplier assertion expecting a residual estimate on fully invoiced pieces, and missing email-validator in the expanded test environment. Frontend artifact13144009919 (SHA256 1e29d3ecb24e71b09ec18b00dadeeef1761978acc5d5e981e021a42a958ee689) records 264 passed and 36 failed in four suites, with mocks retaining prior calls across cases. Stock artifact13144847275 (SHA256 29afd6ed00da2ed231d7ddebddd94c7f90ecb276acbce5927265f1a9e804c433) has the same refund and supplier assertion failures. Security frontend and accounting frontend also failed; their new exact-head acceptance remains necessary. Do not hide these original results.

## Minimal repairs

- Pure operational_refund_capture_basis.py restores only an unchanged capture: current dated payment proof, prior executed same-order/provider refund, saved snapshot, and immutable capture/refund facts must all match. Only generic cancelled order disposition is ignored. Changed totals, dates, references, plans, parties, explicit voids, unknown native refunds and missing facts remain blocked. No inferred capture date, reduced-order basis reconstruction, actual refund or settlement posting.
- Customer order_view calls that validator only when ordinary normalization has no snapshot. Existing #1686 compensation/original-platform rules remain. Existing real-Mongo second-refund regression is preserved, not changed to accept failure.
- Supplier tax test now expects zero remaining estimate for both fully invoiced native pieces; net40/tax6/gross46 per piece and total confirmed92 remain asserted. Genuine partially received piece tests remain unchanged.
- Four frontend suites reset their own mocks before every case. No runtime UI behavior, assertions, test selection, or failure/skip guard is weakened.
- Expanded operational CI installs email-validator==2.3.0, already pinned in application requirements, for real driver-route imports. No runtime dependency or financial permission is changed.

## Verification performed locally

Exact source archive artifact13147030687 SHA256 d9d286f4494ec1a7918b63a5938e38fc6ae562ece964e7ccebb470fa6cbc9fd4 checked; archive SHA, source SHA/root tree and all4769 extracted file Git hashes match. The full tree manifest contains4968 paths; omitted archive paths are retained when creating the remote tree. Source archive is review material, not a release artifact.

Selected local Python3.13 regression: 483 passed, zero failures/errors/skips, including32 new capture-proof boundaries. Synthetic/read-only fake-source and pure tests only, with isolated hash-verified offline dependencies. No local Mongo or frontend dependency installation was available. Real-Mongo, frontend, security, stock/accounting and all full combined acceptance must be re-read on the new exact head. Local counts overlap earlier runs and must not be added together.

## Scope not closed

Post-settlement actual fee/tax credits and partial/multi-order allocation remain incomplete: keep provider_postsettlement_refund_review_required and statement posting_allowed=false. Actual capture/plan ingestion, merchant policy and opening evidence, product SAR/status/current carrier, customers/loans/exchanges, warning causes, full forecast/profit, browser/export and approved live differences remain in scope. Restricted COD archive is not loaded or uploaded. Nothing is activated by this checkpoint.

Production, original branches, /app, release lease, deployment intent, balances, openings, payroll activation, provider policies and all live financial data remain untouched. One final combined release only. Next: inspect exact new CI and artifacts; resolve any remaining failures, then continue STATUS.md inventory and approved read-only reconciliation before release preparation.
