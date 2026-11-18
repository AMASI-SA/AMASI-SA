# Courier contact, assignment and noncash completion

Task ID: courier-recipient-contact-20261119. Status: verified draft-review checkpoint. The user explicitly authorized committing/pushing all three task branches and opening Draft PRs on 2026-11-19. Production changed: no.

## Source checkpoints

| Component | Repository | Task branch | Exact starting HEAD |
| --- | --- | --- | --- |
| Backend | AMASI-SA/AMASI-SA | codex/courier-recipient-contact-20261119 | d3632a33843e076dd7824933528dcc7d897bc32a |
| Courier Android | AMASI-SA/amasi-mobile | codex/courier-recipient-contact-20261119 | d8a396dabafd25c8d85ea1834e4fe08ce31a2086 |
| Staff Android | AMASI-SA/amasi-mobile | codex/courier-assignment-errors-20261119 | ca45802d25d38f97a5e0ebfce139206718bf6044 |

These SHAs identify the bases. The resulting exact checkpoint commit SHAs and related PR URLs are recorded in the Draft PR descriptions. Source-bound CI remains to be assessed. No merge, release rehearsal, lease, APK build, production publication or live assignment/financial write is authorized by this checkpoint scope. The user revoked the earlier supplied AGENTS.md instructions for this task. Issue #1006 was previously inspected and has reached its 2,500-comment limit; this status and the Draft PRs retain the continuation evidence.

## Implemented behavior

- Driver contacts resolve the Salla recipient, including legacy response aliases. A recipient with no mobile never falls back to the purchaser's mobile. Canonical purchaser records remain unchanged.
- Direct transfer of an in-transit assignment restores the new driver's waiting-pickup workflow stage. Safe revocation also recognizes the narrowly identified stage drift left by older direct transfers.
- Explicit operator revocation can release a stale local assignment after a proven carrier replacement. The replacement carrier's delivery stage, label and history remain intact. Identity, pickup inconsistency, instruction and financial-evidence checks still apply inside the existing transaction.
- A proven change from an immutable assignment carrier removes current work from the old driver's reads and blocks status writes. Historical delivery and financial records remain. Sparse legacy assignments without a carrier snapshot cannot establish this change and retain existing behavior.
- Handover label-recovery rejection returns the existing domain message. Staff Android presents it, known blockers and an unknown blocker code instead of discarding the reason. Acceptance rules remain enforced.
- An identical completed bank-transfer or POS delivery retry reads the saved result without another Salla request, earning, collection or financial write. The original receipt, delivery proof, method, bank, amount, earning and order projection must agree. Changed payloads, incomplete proof binding and disabled actors are rejected.
- Courier Android supports a saved receipt image alongside camera capture, reports picker/upload failures at their actual stage, preserves PNG metadata for opaque Android URIs and explains rejected image formats/sizes.

Changed backend files are the five existing store-delivery production modules, new `store_delivery_noncash_retry.py`, four affected test modules including new `test_store_delivery_bank_transfer_completion.py`, and the focused Store Delivery workflow. Mobile changed-file details are in the task documents in the corresponding branches.

## Verification

All fixtures are synthetic. Transaction/HTTP acceptance tests use a task-owned disposable localhost Mongo replica set, never a production database. Salla status delivery and financial observation are replaced only at their external boundary in the new upload/completion test.

- Demonstrated prior failures: three transfer/replacement-stage cases, two current-carrier cases, one discarded recovery message, two staff rejection messages, two real-Mongo noncash identical retries, and five receipt UI/transport cases. The initial mock-Mongo completion failure was a test-double CAS limitation; it was replaced with actual Mongo before diagnosing production behavior.
- Fresh backend regression: all `backend/tests/test_store_delivery_*.py`, `test_courier_label_recovery.py` and `test_recipient_delivery_projection.py`; **343 passed**, exit 0, six existing deprecation warnings. Command: `python -m pytest --noconftest -p no:cacheprovider -q <the listed files> --tb=short`, using the task Python runtime and `MZ2_TEST_MONGO_URI` configured for the disposable replica set.
- Additional bank/POS approval regression: `test_operational_driver_review_mongo.py` and `test_mz2_driver_payment_review.py`; **65 passed**, exit 0. These exercise real approval atomicity, exact-once handling, bank evidence and fee preservation; no approval source code changed.
- Courier Android: all `delivery-app/tests/*.test.cjs`; **68 passed**, exit 0. `tsc --noEmit` passed, exit 0.
- Staff Android: scanner callback suite **15 passed**; courier management contract passed; full TypeScript passed; scoped ESLint exit 0 with one inherited array-type warning. A frozen independent frontend dependency installation was used after the shared dependency tree lacked required Expo modules. All 555 tracked frontend source files were hash-matched to the verified snapshot.
- Changed Python parsed successfully; all three working-tree diffs pass `git diff --check`.

## Limits and next safe action

The selected orders' Salla carrier/status observations were read only. Their Mezan assignment/workflow documents could not be inspected because Computer Use stopped after it could not establish the browser URL. No live assignment was revoked. The exact saved blocker for the photographed completed order remains unverified. Device reproduction of the courier's bank-transfer complaint is also unavailable; the retry and receipt defects above are demonstrated source defects, not a claim about the specific device failure.

The cancelled printing bypass remains reverted. Unrelated worktrees and source changes were preserved. Release v5 inputs and intent remain untouched.

Durable recovery artifacts: `D:/codex-evidence/courier-recipient-contact-20261119/`. The directory contains a binary patch and manifest for each of the three components, including new files and this status. Do not rely on ignored runtime files or an unpushed checkout alone.

The production branch advanced after the tested candidate's starting HEAD. Preserve this verified checkpoint; it is not evidence of successful integration with that newer base.

Next safe action: inspect the three Draft PRs, their exact head commits and source-bound CI. Reconcile an advanced base before claiming merge readiness. Merge, production changes, release builds and application installation require separate authorization.
