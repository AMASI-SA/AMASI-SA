# Courier contact, assignment and noncash completion

Task ID: courier-recipient-contact-20261119. Status: unfinished draft investigation; see the continuation checkpoint below. The user explicitly authorized committing/pushing all three task branches and opening Draft PRs on 2026-11-19. Production changed by this task: no.

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

## Continuation checkpoint — 2026-11-19

The preceding sections record the original checkpoint, not the current validation result. The original backend draft remains recoverable at `c2d8b6ee857b4186f0eb215d6c9ae0fe9dbae8bf` in PR #1998. Integration was verified against `77d824a780df582b5c32f4dda87a959e35e1ed1e`, moved to `c3c1cd26d93ab24d6e08003278b9910526055cc7`, rebuilt from `27e29d3ab5c6c08548780e3dd1ac177021be65f1`, and finally integrated on production head `f91fc7bfbb60b47e5954f4178f9b7dea6eb7fa46`. This preserves the operational financial refresh, Ready fixes, compact balances/scoped statement review and bounded source lookups already published by other workstreams. No production deployment was performed by this task.

### Live diagnosis and its limits

Authenticated production reads established the owner scope before inspecting the reported orders. Direct Mongo access from the Emergent preview terminal timed out; the preview's default local database was positively identified as non-production and was not used as production evidence. The relevant documents were inspected through the existing owner-scoped production HTTP read routes instead. No connection string, credential, customer contact, bank account identifier, or raw customer document is stored in this public checkpoint.

- The two reported assignment refusals currently have no active assignment. Their workflows show completed assembly together with `carrier_label_ready=false` and stale `carrier_label_status=ready`. One completion outbox records `assembly_evidence_reapproval_required`; the other records `completion_source_refresh_pending` and its canonical source still carries `component_pending=true`.
- The read-only preparation-completion route confirms assembly and source completion for both cases. Its local-courier response deliberately does not issue a label, so `ready=false` from that route alone is not proof of the handover failure.
- A synthetic replay of the known first-case fields was accepted by the existing recovery path. Consequently the old outbox code and the missing-ready flag are observations, not a proven explanation of a new scan failure. The exact current scan response remains unobserved.
- The carrier-replacement case already has a recorded unassignment before this task's inspection and no current active assignment. This task did not perform that cancellation. Historical assignment and financial documents have not been read directly.
- For the WhatsApp case, the current order DTO contains a recipient whose name and phone differ from the purchaser. This supports the recipient-selection defect for that case; it does not claim a device test of the installed application.
- The financial-source contract and bank selection were cross-checked separately. Operational bank catalog identities and canonical financial-account identities are not interchangeable. A bank-list fallback alone would create a later validation/statement-matching failure and is not an accepted fix.

Automatic approval review rejected creation of a production handover diagnostic session because that operation persists state and the authorized live inspection was read-only. The session was not created. Do not retry the denied action through another endpoint or terminal. Explicit approval for that narrowly described diagnostic session and scan is still required before observing a new scan response. Confirmation of a handover/assignment is outside that diagnostic action.

### Completed CI and fixture corrections

- The two frozen-fee failures were contradictory synthetic carrier fixtures. Their current order carrier now matches the immutable assignment snapshot, while the driver's later profile carrier and fee are changed independently. The tests retain the original fee snapshot. Additional SMSA/iMile replacement cases require `409 shipment_shipping_company_changed`, no provider status request, and no financial writes. The production carrier guard was not weakened.
- The Store Delivery CI job now installs the production-pinned `PyMuPDF==1.28.2`, `python-multipart==0.0.31`, and `qrcode==8.2` required by the exercised paths.
- The J77 integration checkpoint `62af72a27333b4ac063a35a3be3f280eadc470ce` passed **462 backend tests** and **156 release-identity tests** locally. The prior J80 integration also passed its formal locked frontend build, verification, and candidate-intent freeze; that candidate became obsolete when production advanced and must not be reused.
- Courier Android PR #362 is saved at `20d3401c9d44ad7b2c16f517faa941d1f019bbb3`. Its exact-head tree passed **76 tests**, TypeScript, and Expo Doctor **18/18** in GitHub Actions run `44413385206`. The Android system photo picker no longer depends on a broad media-library permission gate; capture/picker/upload exceptions retain their correct stage. No APK was produced.
- Staff Android PR #363 is saved at `80d84a3b5afd51899574c153a520ffe2079dc0f0`. The relevant workflow now includes the actual PR base. GitHub Actions run `44413445590` passed the **15 scanner cases**, courier-management contract, TypeScript, and scoped ESLint on the exact head. The other three applicable checks also passed. Its source lineage remains separate from the courier app. No APK was produced.

### Newly reproduced concurrent-completion defect

On an isolated real Mongo replica set, overlapping noncash completion attempts can both send the provider status update. The late attempt then encounters a unique-key failure and the old compensation path deletes the successful attempt's earning, collection, and payment review by assignment ID. The assignment can remain delivered and its next retry fails with an idempotency conflict. The same branch affects prepaid delivery fees. This is a reproduced source defect, not proof that it caused the unidentified live bank-transfer attempt.

The correction is now integrated: an assignment-owned claim precedes each provider dispatch, the assigned shortcut preserves the pickup and delivered phases, and local evidence/projections commit in one transaction per phase. Frozen fees and amounts survive retries; the broad compensation deletes are removed. A concurrent request receives a retry-preserving 503. Ambiguous provider results are reconciled by GET rather than another blind status POST. Existing workflow identity is pinned and its projection must match at commit; explicitly absent legacy workflows remain supported. Bank/POS review stays pending the accountant. A safe owner/driver-bound DTO exposes the original payload and frozen amount after app restart while hiding the internal journal. Independent reviews found and verified corrections for the assigned-phase journal, workflow binding, and stale-home client recovery. A provider result that cannot be proved by GET remains pending; this is not an automatic resolution claim.

### Operational bank-transfer integration gap

A synthetic reproduction established a separate end-to-end gap: an active operational financial setup can have valid namespaced operational bank identities while the courier picker and the operational accountant-review path require canonical native bank accounts. The latter also requires statement evidence whose only existing importer runs through the paused native writer. Existing review tests seeded native bank identities and imported-statement rows directly, so they did not exercise this production-relevant setup through its public intake path.

The bounded correction keeps native bank identity and native write-control contracts unchanged. It adds phase-aware courier bank identity, preserves the selected full identity in pending evidence, and introduces original-XLSX statement evidence specifically for operational courier-payment review. The parser is reusable; the native importer, journal writer, activation path, opening balances, and automatic provider classification are not. Selection or upload must not mark a transfer paid. Accountant approval must still verify and consume one exact, owner-scoped statement movement with the existing operational financial transaction.

Sixteen real-Mongo HTTP acceptance cases now exercise the production-relevant setup shape with synthetic data: operational active, native writes paused, legacy/operational banks, and no canonical bank. They cover selection, distinct receipt/proof upload, delivery, pending review, original statement intake and matching, accountant approval, and retries. The bank and phase are checked inside the first claim before Salla dispatch. If they change during a successful provider request, the original delivery, fee and pending evidence are retained while accountant approval requires reconciliation. The bank/statement suites separately reject wrong owner, bank, currency, amount, date, reference, duplicate evidence and missing permissions. Both old and new statement namespaces share consumption claims. Independent review demonstrated a duplicate through XLSX metadata changes and row relocation; the correction additionally pins the full bank identity and original reference, or semantic row facts without file/row-position identity when no reference exists. Indistinguishable unreferenced rows require reconciliation. Historical source claims are also checked. Twelve regressions failed before that correction and then passed; two independent real-Mongo replays confirmed one approval followed by 409, one monetary movement, and no document change on the rejected attempt. The native pause remained enabled. The public canonical bank DTO is preserved; phase metadata stays internal to capture. Source-bound integration tests and CI remain required before readiness is claimed.


### Client and release-contract integration evidence

Courier Android PR #362 now has head `62d56dded3d1fee626c527598f800da9b5e8e386`. All **89 tests**, TypeScript and Expo Doctor **18/18** passed in GitHub Actions run `44425977900`. The tested PR merge tree `ad61432253909e8d2fe6e59b420e47b7561a10dd` equals the saved head tree. The signed-candidate job was skipped; there is no APK or device UAT claim.

The integrated Web interfaces passed **117/117** component tests in four suites, including operational bank statement intake/matching and explicit recovery of saved courier completion. Opening a screen or recovering saved state performs no POST. A fresh bank statement upload does not approve the payment. Independent source review of both UI patches found no remaining blocker in that scope.

The current integration also passed **156 release/deployment identity tests**. These are protocol regression tests, not a deployment or a substitute for the exact source commit's formal frontend build and frozen intent. The candidate must retain the production base's intent in source commit A and record only the newly generated intent in a separate B commit. No identity allowlist or gate is bypassed.

### Compatibility with the J27 scoped review workspace

J27 added a server-owned review callback with per-driver/account permissions. The integration preserves that callback and separately authorizes bank-statement rows with `bank.use` and `movements=all`. Synthetic HTTP reproductions showed both a false rejection of an authorized scoped employee and disclosure of bank candidates to a receipt-only reviewer before this integration correction. Receipt-only access now returns no bank rows; it does not inherit statement-upload permission.

The actual workspace consumer reads its paginated bank-movements route, so that route now includes the operational statement namespace alongside the existing native query. It preserves five-row pages, bounded reads, numeric legacy amounts, owner/bank scope and deterministic ordering. The workspace obtains statement rows only from its paginated movement endpoint, which checks the full movement permission as its last authorization step. Receipt options never embed the unused movement list; bank/POS choices are filtered using the final policy after catalog reads. Regressions demonstrate withdrawal of movement permission during the final reads. A further 201-row native statement case proved that embedding the legacy capped list in workspace options could incorrectly disable approval; separating the paginated consumer preserves approval and all 41 five-row pages while retaining the legacy embedded-list contract. The focused scoped/noncash/packed-state group passed **84/84**; independent source review closed its final observation. These tests are synthetic in-memory HTTP contracts, separate from the real-Mongo financial acceptance tests.


### Final local integration evidence

On the J27 integration, the combined courier, fee, bank evidence, native/operational accountant review and workspace suites passed **584/584 tests** on a disposable real MongoDB 7.0.14 replica set, with zero failures/skips and six existing deprecation warnings. The final read-only workspace volume/policy follow-up passed its focused **84/84** cases; it changes no financial writer. The integrated Web suites passed **117/117**, and release/deployment identity regressions passed **156/156**. Local Python is 3.12; GitHub uses the workflows' pinned runtimes and remains a separate required check. Exact remote source A, intent-only B, formal build and source-bound CI results are recorded in the new Draft PR after saving.

The unresolved live handover diagnostic remains unchanged: do not claim an exact current scan cause, alter assignments, or clear workflow links without the narrowly approved production session/scan. No production deployment, live assignment/payment mutation, native-accounting activation, APK build or installation occurred in this task.

### Production advanced to f91 during final packaging

The reviewed J27 package remains recoverable on `codex/courier-root-fixes-20261119`: source A `cded71c245fa411f9494dc0d0cb4230bbdf5c75d` and intent-only B `01d7d80255f8378ae99609f7ec2d7e7e5784cb26`. Independent review matched every source-manifest record and retained build artifact, the unchanged inherited intent, both clean builds, and the derived release identity. This package is a historical checkpoint and must not be reused for the newer production base.

The 35-file source patch has no file overlap with the 15-file f91 production delta. It applied cleanly; only this status continuation changes relative to the reviewed patch. Independent source review found no integration blocker: the new prepaid/deferred assignment lookup retains the existing raw-number, active-state and status predicates and does not change the immutable fee or payment-evidence writers. The final verification includes the combined courier/bank/review suite and the three new production index suites. Exact counts and the new immutable A/B pair are recorded in the final Draft PR. The frontend and release-control source are unchanged by the production delta; the new source nevertheless requires its own governed build and intent freeze.

### Owner-authorized scan of the two reported shipments

The owner authorized a single production diagnostic session and one scan of each reported order with an active driver bound to the current Riyadh carrier. The first scan was accepted. The second returned `assembly_canonical_status_blocked`. Both used the successful scan response envelope. The browser showed one accepted and one rejected row. The raw HTTP headers and access logs were not captured. No confirmation was submitted. The operator left the session screen. There is no separate close-without-confirm operation in this router.

Both Salla orders were completed with the same merchant carrier. Both Mezan order histories recorded completed assembly. The earlier owner reads found no active assignment and an unavailable local label in both workflows. The first had an older reapproval outbox error which did not block this scan. The second had outstanding canonical component reconciliation. Its refusal was reached inside label recovery, after the active assignment and workflow-link checks. The UI supplied no field-level difference or provider-refresh outcome for that attempt.

The owner subsequently clarified both eligibility rules. Initial assignment requires completed Salla status. Transfer requires completed or delivering. Both require the current Salla carrier to be a merchant courier. The operator or driver must be authorized for the merchant and a shipment can have only one active assignee. The assignment path no longer repairs or requires label, print, assembly, or component evidence. The selected driver does not need a matching city-carrier name. Label generation and physical delivery remain separate operations. A direct transfer records both sides in a single Mongo transaction.

Tests cover scanning with pending components and unavailable labels, missing workflow, changed Salla carrier or status at confirmation, transfer in transit, external carriers and duplicate assignment. The release-intent from the earlier checkpoint belongs to the earlier source. This is a draft-only change.
