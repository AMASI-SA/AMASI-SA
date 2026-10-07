# R8 native bank collection — bounded integration, NOT release acceptance

Date: 2026-10-07. Task MZ2-SPECIAL-ORDERS-001 / Draft PR1171.
**IN_PROGRESS_NOT_READY_TO_DEPLOY. Accounting must deploy first and actual runtime
identity must be verified. No automatic special-order deployment/activation.**

## Preserved inputs and source reconciliation

Previous complete R7 task source:3b2e4bb2441198f94a82052c354eb1cffb081837.
Current native Accounting Git reference:a7977f4cfc1ef0a721fc25d783661332f32fe3b6,
tree81cecfd86018310f795d39d0f8c9aae527b2396f. This is not runtime evidence.
Read-only source-audit commit43b5312e3646d9e463776d237b20a072e31984d2,
tree1ba5461b9c59cf640cba39ee207c9679d5390070; run37631768127.
Artifact11486631394,21077785bytes, SHA256
2a5ba3f2b92a13fa3f853ea762a0e8081f4656b25b5eb867b11f3466277dacc2.
Downloaded archive and5674source member hashes independently verified.

`git merge-tree --write-tree` was a conflict audit, not a worktree/ref merge.
23shared paths include native review, shipping, delivery, inventory/preparation,
requirements and frontend. Nine have actual content conflicts:
- backend/fulfillment_v2_routes.py
- backend/order_engine/shipping_label_service.py
- backend/order_review_routes.py
- backend/store_delivery_driver_app_routes.py
- backend/store_delivery_payment_evidence_routes.py
- backend/store_delivery_payment_review_routes.py
- backend/store_delivery_settlement_routes.py
- backend/supplier_receiving_routes.py
- frontend/src/components/fulfillment/CompletedFulfillmentOrders.jsx

These are NOT resolved by R8. No newer source is overwritten. New work is isolated
to feature-owned files, with current native modules imported unchanged in tests.
The original Accounting1180 candidate is historical, not a forced current target.

## Implemented bounded business path

`v2_owner.py` joins the real public Accounting atomic_owner first, then admits the
unchanged local epoch/scopes in the SAME Mongo session. Missing/paused global or
local controls, wrong writer mode, unverified opening/safe_active, stale epoch,
revoked/foreign actor and local-first nesting fail closed. A new wrapper rejects
accidental raw Legacy or V2-ledger access by local callbacks. This is an application
protocol, not database-user permissions against privileged raw Mongo code.

`v2_bank_receipts.py` implements approval of an existing pending receipt against
one exact unclassified incoming `mz2_daily_movements` row and the actual
`mz2_financial_accounts` bank. A sealed, previously approved V2 receivable bound
to the order, its source digest, policy digest and exact native typed asset identity
is mandatory. No default identity, artificial movement, Salla order or source
refresh is fabricated. It posts bank debit/receivable credit via public V2 APIs,
checks read-back, classifies the native movement and commits the local receipt,
payment, event, binding, revision and outbox atomically. Replays are independently
verified, not trusted from a prior response. Receipt/binding/native-claim history
and duplicate payment observations are checked. No Legacy bank/ledger reads occur
in the monitored tests. The local collection wrapper cannot bypass public V2 APIs.

`v2_bank_routes.py` supplies an unmounted default-OFF HTTP factory. The authenticated
owner is resolved from saved user identity; body fields are only revision/epoch,
receipt and movement IDs, and explicit confirmation. Neither amount nor account,
owner or posting legs can be injected. Public responses do not echo exception text.

## Explicit missing prerequisites and limits

- This is SAR collection of an ALREADY POSTED receivable, not a complete agreement,
  advance, revenue/tax or compensation-expense producer. Tests install that sealed
  prerequisite explicitly with synthetic data. A policy digest is not new approval.
- Bank movements preceding the receivable are held for the real advance workflow;
  the adapter does not backdate recognition. Non-SAR bank handling remains closed.
- Supplier/inventory/COD/carrier/driver settlement/refund paths are NOT migrated by
  this checkpoint. Old FinancialService and ledger_adapter remain unchanged and
  unaccepted for final V2 activation. Mixed existing costs or other payment kinds
  are held for migration rather than silently interpreted.
- No server.py registration or composition into the old live router occurs. Evidence
  fixtures use a real structurally valid PNG; this is not a real bank statement or
  complete upload/UI/fulfillment flow. External provider calls are absent.
- Full source reconciliation, stored Salla sidecar, reports, UI/two Android clients,
  all-writer coverage, complete business UAT and operational rollback remain open.

## Observed local verification

Current native reference, unchanged source:
`pytest --import-mode=importlib tests/test_accounting_ledger_v2.py tests/test_mz2_write_control.py`
Result:49tests+7subtestsPASS. The JUnit count includes subtests; do not call them
additional independent functions.

Feature-only package overlay plus native reference, no old application modules:
`pytest --import-mode=importlib mezan_special_orders/tests_mz2_target mezan_special_orders/tests_mz2_current`
Result:106PASS=48repeatedR7transport+58newbank/HTTP/contracts. Zero failures/skips in
both final local commands. New file compilation and source blob matching passed.
Tests use a task-owned loopback Mongo replica set and disposable synthetic DBs.
LocalPython3.13.5,Motor3.3.1,PyMongo4.6.3,Pydantic2.13.4,FastAPI0.128.2,pytest9.0.2.
These local versions are not claimed identical to production; new CI uses the
current reference's exact dependency pins and a complete repository checkout.

Corrected local harness attempts, not hidden passes:
1. Full task backend on PYTHONPATH let Pytest prepend old employee modules:44PASS/
   4FAIL. Use importlib mode and a package-only overlay; R7then48PASS. An explicit
   test asserts every key native module came from the current reference.
2. The read-only source archive omitted backend/accounting_currency_codes.json:
   initial bank25PASS/14FAIL. Exact Gitblob de4b7329c8af5bb330832d36187e04437e9d43e8
   was retrieved and verified. Missing localbcrypt then caused the same14failures;
   pinned offline wheel prerequisites were verified, not mocked. Then39PASS.
3. Four integrity negative tests initially failed because history corruption was
   not rejected. The checks were implemented; all four passed, then43totalPASS.
4. HTTP/source-date/currency tests increased the new suite to58PASS.
5. One baseline static test initially failed due the partial source archive missing
   scripts/check_accounting_ledger_v2_access.py. Its exact Gitblob
   a4ad0aeb7ece0e30dc83745f8c85ce575857f6ca was fetched, then49+7PASS. No native
   source, native assertion, public-API guard or production requirement was changed.

Independent exact-commit CI has been added but was not observed when this source
checkpoint was written. Record its actual result separately; do not infer PASS.

## Rollback/release boundary

No task operation changes Production, Codex branches/intent, release lease,
Accounting activation/pause/opening, real merchant/provider/financial records,
Android distribution or /app. Existing controls stay unchanged/off. A code rollback
must preserve the published Accounting and special-order history. The new test's
transaction rollback is not a host rollback or backup/restore drill. Compatible
recovery release, actual publication proof and separate release approval remain
mandatory. Preserve R7 records under history and continue from current remote HEAD.
