# Driver collection review in the employee app and Mezan

## Authorization and source

- User requested approval/rejection of POS and customer bank-transfer receipts from the employee app and visibility in driver accounts/settlements.
- Production publication and any release reservation remain on explicit user hold. No prepare, prepublish, lease, shared `/app` change, merge, publication, live approval/rejection, backfill, or opening-balance creation is authorized by this checkpoint.
- Main repository base: `8947eb0b4ad5b0370efecdaadc044268693122da` on `hotfix/prod-snap-meta-final`.
- Task branch: `codex/driver-collection-review-app-20261107`.
- Employee app base: Android75 `b9fc2972a4582c93021bcb8d50f6dda8488fd45a`, repository `AMASI-SA/amasi-mobile`, branch `codex/android73-monitoring-selection-20261106`. Main/mobile default branch is not the source of Android75.
- Separate cost/shipping Draft PR1781 and all other release work remain untouched.

## Checkpoint: WIP, not ready to merge or publish

The web account page now embeds the existing receipt-review flow independently of the settlement summary. Both POS and bank-transfer receipts appear, scoped to the selected driver, with an explicit failed-load state. Missing linked-ledger fields are no longer rendered as zero custody. The read-only settlement summary groups noncash receipt counts and amounts by pending, approved, rejected, and unknown status without changing cash custody or creating financial entries. Rejection requires a nonblank reason. The UI shows the actual status and rejection reason of legacy settlement requests.

Customer payment at delivery and a later cash remittance by the driver are different operations. The legacy remittance route does not prove a bank arrival. This checkpoint does not enable that route as a financial bank confirmation and does not add a second settlement writer.

Pending parallel work: employee-app pinned review context, visibility, exact-payload retry, and focused tests; consistent server-derived app authorization at deep review writers; read-only eligible imported bank-movement choices. Those changes are not part of this first checkpoint.

## Verification completed

- `git diff --check`: passed for the current working changes before checkpoint.
- Python AST parse of the changed summary module and test: passed.
- `PYTHONPATH=backend /workspace/scratch/17a9cc329ec0/mezan-review-venv/bin/python -B -m pytest --noconftest -q backend/tests/test_store_delivery_collection_review_summary.py backend/tests/test_store_delivery_operational_v2_contract.py --tb=short`: **18 passed**, exit 0.
- Web behavior tests added for visibility despite summary failure, driver-switch races, receipt-load errors, unavailable ledger semantics, and rejection reasons. Execution pending dependency installation; no frontend pass is claimed.

No production or real financial data changed. No APK was built or distributed. No release intent was frozen and no release slot was reserved.

## Next safe action

Finish the bounded app/backend compatibility changes; execute focused web/app/backend tests; independently review the combined diff. Persist the verified result in draft PRs and update the continuation ledger. Keep publication on hold until explicitly released by the user.
