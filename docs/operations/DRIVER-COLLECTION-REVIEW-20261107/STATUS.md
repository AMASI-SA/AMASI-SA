# Driver collection review in the employee app and Mezan

## Authorization, repositories and hold

User requested owner/accountant approval or rejection of POS and order bank-transfer receipts from the employee app, visibility in the driver account/settlement table, and rejection reasons visible to the driver. The user subsequently clarified that the transfer may originate from the customer or from the driver after accepting customer cash: the driver selects bank transfer when submitting delivery and attaches the receipt in either case.

- Main base: `8947eb0b4ad5b0370efecdaadc044268693122da`, `hotfix/prod-snap-meta-final`.
- Main task branch: `codex/driver-collection-review-app-20261107`, Draft PR1794. Initial durable web/summary checkpoint: `00907a1a3e863a17e3833caba3c8f68f15b5737e`; the final code checkpoint is `3113c5920384e0c01360526026403b12fccfad7e`. This final evidence-only branch is `codex/driver-collection-review-evidence-20261107`; it changes this status document only and does not move either reviewed code branch.
- Companion employee app: AMASI-SA/amasi-mobile Draft PR344, branch `codex/driver-collection-review-app-20261107`, source `137b95fcc621b00381a21b8885e67e4379cb8d3b`, based on Android75 `b9fc2972a4582c93021bcb8d50f6dda8488fd45a`.
- User explicitly said an important release has priority and not to reserve publication. The hold remains: no prepare, prepublish, lease, shared `/app` mutation, production merge, publication, APK distribution or OTA update.
- No real receipt was approved/rejected, no bank or POS balance was changed, and no opening balance/backfill was created. Separate cost/shipping Draft PR1781 and all unrelated work remain untouched.

## Completed behavior

### App and review access

The existing employee app path is side menu → إدارة الموصلين → select driver → حساب الموصل → إيصالات الشبكة والتحويل بانتظار المراجعة. The existing Mezan page is `/bank-transfer-review?workspace=store-delivery`.

The companion app now sends the pinned source, session scope and review revision for both approval and rejection. It supports an imported bank movement ID even when suggestions are empty, requires an explicit matching POS amount, and requires a rejection reason. Review loading/render/errors no longer depend on the unrelated performance or settlement reports. It guards duplicate taps, late driver/login responses and exact-payload replay after an uncertain result. No unsupported request_id or general API-client permission change was introduced.

### Web driver accounts and totals

The driver account page embeds the existing review flow, filtered to the selected driver and independently loaded. Failed loading is not presented as an empty completed queue. Known cash custody and driver fees use their existing operational-subledger fields instead of displaying unavailable linked-ledger fields as zero. The summary groups noncash receipt counts/amounts by pending, approved, rejected and unknown status; these are receipt totals, not current bank/POS balances or cash custody. Settlement request status and the driver's rejection reason are explicit.

### Live app authority and bank proof

A shared read-only authorization helper reloads the real actor, current owner linkage and current app page/action grant using a server-derived authenticated client marker. App grants do not become browser permissions, explicit denials win, disabled/unlinked users are rejected, and store_driver identities cannot review their own receipts. Transactional authorization is checked against the snapshot and current external state. Native accounting/P02 gates remain required.

Operational options offer up to 100 bounded bank-statement candidates. Each passes the existing original-file-byte proof validation, same bank/amount/date checks and unused-proof checks. A partial-list marker leaves manual imported-ID entry available. Selection is read-only; the existing approval writer revalidates and consumes proof atomically. The financial effects and idempotency key were not redesigned.

## Clarified transfer rule — 2026-11-07

The sender ambiguity is resolved. When first submitting the delivered order, the driver selects `bank_transfer`, selects the store's receiving bank and attaches the receipt. The customer may have transferred directly, or the driver may have accepted cash and transferred the corresponding payment from their own account. Both use the same order-specific review. Neither the submission contract nor the operational/native bank-evidence adapter requires the sender to match the customer, so this clarification needs no financial-code change or mandatory sender field. The CI-verified main and mobile code heads above remain unchanged.

The transfer stays unconfirmed and linked to the order/driver until review. Pending or rejected evidence does not create bank cash or physical driver cash custody in the operational path. Rejection retains the delivered order, records the reason and leaves payment unconfirmed. Approval checks the receiving bank, exact amount, original statement evidence and unused reference, then records the bank arrival once. No separate bank movement should be entered merely because the driver initiated the transfer.

A delivery that was already committed as cash cannot be silently submitted again as bank transfer: the public status endpoint rejects that transition and preserves the existing cash collection. The existing legacy cash-remittance route still does not prove bank arrival or close the independent operational financial balance; that separate bridge is not needed for the newly clarified first-delivery flow and remains outside this change.

Driver-side source was inspected at mobile `137b95fcc621b00381a21b8885e67e4379cb8d3b` (`delivery-app/App.tsx`, `delivery-app/src/api.ts`) and main `3113c5920384e0c01360526026403b12fccfad7e` (`frontend/src/pages/AmasiDeliveryApp.jsx`). Both offer bank transfer without a sender restriction. The native driver app displays the review result/reason but currently captures receipts with the camera and has no replacement-receipt button. The web driver page has the replacement-receipt flow. No gallery picker, native resubmission, device acceptance or driver-app release is claimed by this clarification.

## Verification

All tests used synthetic data or a disposable local Mongo replica set; none contacted production financial APIs.

| Verification | Result |
| --- | --- |
| App permission/HTTP, bank-proof and existing noncash tests | 79 passed |
| Existing operational transaction acceptance | 14 passed on Mongo |
| New employee approval/revocation acceptance | 6 passed on Mongo |
| Existing native payment review and POS manual review | 73 passed on Mongo |
| New receipt summary and existing operational-v2 contract | 18 passed |
| Total unique backend tests across these sets | 190 passed |
| Web settlement, payment-review and operational-review suites | 39 passed across 3 suites |
| Employee app existing courier contract + new focused suite | Contract passed + 22 checks passed |
| Existing app scanner fallback regression | 13 passed |
| Main source CI on 3113c5920384e0c01360526026403b12fccfad7e | All 15 triggered workflows succeeded |
| Main full operational CI suites | 3166 backend tests; 449 frontend tests across 38 suites passed |
| App source CI on 137b95fcc621b00381a21b8885e67e4379cb8d3b | All 4 applicable source workflows succeeded; release bundle skipped |
| Independent main/mobile source review | No blocking finding |
| Diff whitespace check and changed TS/TSX parse | Passed |

Root repeated the 97 backend read/permission/summary tests, all 39 web tests, and the app courier command. Independent review repeated the web, permission/proof, summary and app suites. App checks include actual TSX execution through the existing synthetic UI harness; they do not prove device layout or Android UAT.

The six new real-Mongo cases verify bank/POS approval once under the actual employee identity, current and legacy employee links, refusal from browser context, and full rollback when the app grant or legacy linkage is revoked during the transaction. Existing tests verify rejection has no financial effect, proof reuse is blocked, and POS remains a receivable until a separate bank settlement.

### Reproducible commands

Backend test groups (with `PYTHONPATH=backend`, `python -B -m pytest --noconftest -q --tb=short`):

- `backend/tests/test_operational_balance_driver_review_app_permissions.py`
- `backend/tests/test_store_delivery_noncash_review.py`
- `backend/tests/test_store_delivery_collection_review_summary.py`
- `backend/tests/test_store_delivery_operational_v2_contract.py`

Real-Mongo groups, using the repository test URI contract and a disposable replica set:

- `backend/tests/test_operational_balance_driver_review_app_mongo.py`
- `backend/tests/test_operational_driver_review_mongo.py`
- `backend/tests/test_mz2_driver_payment_review.py`
- `backend/tests/test_mz2_driver_pos_manual_review.py`

The isolated runner `/workspace/scratch/17a9cc329ec0/mezan-driver-review-mongo-20261107/run_tests.py` starts and stops localhost Mongo within one execution namespace. MongoDB8.0.12 archive was verified against its official SHA256 `7665edf8ec6f0da2515c49f784da22619f686fffed990a7696c5365ee9d334b3`. URI: `mongodb://127.0.0.1:27315/?replicaSet=mezanDriverReviewTest`. The final log confirms clean shutdown, exit0.

Web: Jest27.5.1 with React19.2.8 and Babel7.29.7 matching the lockfile, running `StoreDeliverySettlements.test.jsx`, `StoreDeliveryPaymentReview.test.jsx`, and `operational/DriverPaymentReview.test.jsx`. Dependencies were isolated; no manifest/lockfile changed. Local full frozen Yarn installation stalled in fetching and was stopped (exit130); the isolated scoped runner succeeded. The complete source CI subsequently installed dependencies and passed its normal tests/build checks. No V5 governed release build or intent is claimed.

App: `npm run verify:courier-management` with TypeScript5.9.3. The new suite is included in the existing CI-facing package script. Full repository TypeScript, on-demand historical baseline, permission/session, scanner and general mobile CI passed on `137b95fcc621b00381a21b8885e67e4379cb8d3b`. The first app source exposed one result-union typing issue and two older test fixture import dependencies; the final source corrects them without changing runtime financial semantics or weakening baseline/assertions. No APK was built, installed or distributed.

## Handoff and next safe action

### Clarification verification

Root rechecked the remote task heads and both open Draft PRs before this evidence update. Root then repeated the following against the same source using the disposable local Mongo runner:

```bash
/workspace/scratch/17a9cc329ec0/mezan-review-venv/bin/python \
  /workspace/scratch/17a9cc329ec0/mezan-driver-review-mongo-20261107/run_tests.py \
  backend/tests/test_operational_driver_review_mongo.py \
  backend/tests/test_operational_balance_driver_review_app_mongo.py \
  backend/tests/test_store_delivery_noncash_review.py
```

Result: **80 passed, exit 0**, including real transaction approval/retry/rollback and rejection. Root also independently exercised four synthetic original-XLSX proofs using the existing `seed` fixture: customer and driver-own-account descriptions in English and Arabic. For each, the original statement bytes and all matching hashes were regenerated, the exact bank movement remained eligible, pending review was retained, operational state was unchanged and cash custody remained zero. **All four passed, exit 0.** These are new executions of existing source, not additional source changes or a claim of live acceptance.

Independent read-only review repeated domain, noncash, summary and operational-report suites (**93 passed**) and exercised the public HTTP route for an already delivered cash order: retrying it as bank transfer returned 409, retained the original cash collection and created no review. Root's 80-case run overlaps the earlier verification counts; do not add them as unique coverage. The clarification updates this evidence document and the owned PR/continuation metadata only, including repairing the prior PR evidence links. No source branch, financial record, app package or production state changes.

Both repositories are draft-only and source CI is complete on the exact code heads above. Main workflow evidence: https://github.com/AMASI-SA/AMASI-SA/actions/runs/42384116304 (operational backend/frontend), with all15 PR workflows successful. App workflow evidence: https://github.com/AMASI-SA/amasi-mobile/actions/runs/42384667915 (full mobile), plus42384668092 (courier/TypeScript/permissions),42384668270 (session/browser) and42384668081 (barcode), all successful. The release-bundle workflow42384668061 was skipped; no APK/release activation is claimed. Final results are recorded on both PRs and in the bounded owned continuation entry. The remaining safe state is to wait for the existing user publication hold to be explicitly lifted, not to reserve a slot. Issue1006 is at its 2500-comment limit; its body links this task and the separate preserved PR1781. Do not edit unrelated entries or bypass publication controls.

After the user explicitly releases the publication hold, a later release must first reconcile current production/mobile bases, follow the reviewed V5 release process and accepted app release process, then perform authorized live/device acceptance. No release slot is reserved by this work.
