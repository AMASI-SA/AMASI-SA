# Driver collection review in the employee app and Mezan

## Authorization, repositories and hold

User requested owner/accountant approval or rejection of POS and customer bank-transfer receipts from the employee app, visibility in the driver account/settlement table, and rejection reasons visible to the driver.

- Main base: `8947eb0b4ad5b0370efecdaadc044268693122da`, `hotfix/prod-snap-meta-final`.
- Main task branch: `codex/driver-collection-review-app-20261107`, Draft PR1794. Initial durable web/summary checkpoint: `00907a1a3e863a17e3833caba3c8f68f15b5737e`; this document accompanies the combined source checkpoint built on it. The PR and Issue1006 carry its exact final SHA.
- Companion employee app: AMASI-SA/amasi-mobile Draft PR344, branch `codex/driver-collection-review-app-20261107`, source `ebd88b6d040d6a926ad502a4507b9aec565c6ff0`, based on Android75 `b9fc2972a4582c93021bcb8d50f6dda8488fd45a`.
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

## Important remaining distinction

A customer paying the store by bank transfer at delivery is covered by this review flow. A driver collecting cash and later remitting it to the store is a separate cash-custody settlement. The existing legacy settlement route only records the driver subledger; its confirmation does not prove bank arrival. This patch does not enable that legacy route as a bank-credit writer or silently close cash custody. The UI states the limitation and keeps unavailable financial settlement controls disabled. A governed cash-remittance review/financial bridge would be a separate implementation.

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

Web: Jest27.5.1 with React19.2.8 and Babel7.29.7 matching the lockfile, running `StoreDeliverySettlements.test.jsx`, `StoreDeliveryPaymentReview.test.jsx`, and `operational/DriverPaymentReview.test.jsx`. Dependencies were isolated; no manifest/lockfile changed. Full frozen Yarn installation stalled in fetching, so no full governed build is claimed.

App: `npm run verify:courier-management` with TypeScript5.9.3. The new suite is included in the existing CI-facing package script. Full repository source CI/typecheck is pending at this checkpoint; no APK was built, installed or distributed.

## Handoff and next safe action

Both repositories are draft-only. Check source CI on the exact PR heads and resolve any concrete failure without merging or publishing. Record CI results on the PRs and update the bounded owned continuation entry. Issue1006 is at its 2500-comment limit; its body links this task and the separate preserved PR1781. Do not edit unrelated entries or bypass publication controls.

After the user explicitly releases the publication hold, a later release must first reconcile current production/mobile bases, follow the reviewed V5 release process and accepted app release process, then perform authorized live/device acceptance. No release slot is reserved by this work.
