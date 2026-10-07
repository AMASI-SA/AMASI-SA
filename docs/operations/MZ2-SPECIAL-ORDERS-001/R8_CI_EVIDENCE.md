# R8 exact-source CI evidence — native bank collection, NOT whole-feature acceptance

Task MZ2-SPECIAL-ORDERS-001. 2026-10-07.
**IN_PROGRESS_NOT_READY_TO_DEPLOY. Actual Accounting publication, final business
migration and rollback/release authorization remain unverified/unaccepted.**

## Exact tested source and separate current Accounting

- Task source: `566b051d87744b33e2994c1df0daf922e929ec62`.
- Task tree: `e4c8f7fa50a346ee4c19b5bb41b7c6f1ceed1ad5`.
- Native reference: `a7977f4cfc1ef0a721fc25d783661332f32fe3b6`.
- Native tree: `81cecfd86018310f795d39d0f8c9aae527b2396f`.
- Direct push run: **37636940113**, job **112845528808**, completed **success**.
- Every step, including unchanged-source validation, V2 access-guard check and
  isolated database cleanup, completed successfully. No temporary source patch
  was applied while testing. Package overlay bytes equal tracked task source.
- CI asserts no tracked native or task files changed. Only the feature package
  is put on the overlay path; the old application's backend is not imported.
  Importlib tests verify actual paths for owner/ledger/identity/balance modules.
- Subsequent documentation-only commits do not constitute new runtime tests.

Workflow: https://github.com/AMASI-SA/AMASI-SA/actions/runs/37636940113
Artifact: https://github.com/AMASI-SA/AMASI-SA/actions/runs/37636940113/artifacts/11490911109

## Observed result — exact counting

- Feature candidate: **106 passed**, 0 failures/errors/skips. This includes the
  existing48R7transport cases rerun against current native code plus58new bank/
  HTTP/contract cases. It is not106new cases and not the full application suite.
- Unchanged current native baseline: **49 test functions +7 subtests passed**.
  JUnit `tests=56` includes those subtests; XML has49testcase nodes. No failures,
  errors or skips. These are selected ledger/write-control tests, not all Codex tests.
- Existing own-branch Core workflow37636940053/job112845529062 also completed
  success. Its separate test counts were not independently downloaded here and
  are not added to the above or presented as latest Accounting acceptance.

## Artifact independently downloaded and inspected

Artifact11490911109, ZIP4830bytes. SHA256 verified against GitHub's digest:
`864323ea993dbcdf612b91b1501824e562ea7b580eaa91aa44012e56c1a7de53`.

| File | SHA256 | Independent counters |
| --- | --- | --- |
| r8-native-baseline.xml | d49a7479407e90359f2c0e53ebfac13d58b92f779b9d441f633d8d9c93ca3a17 |56including subtests,49nodes,0failure/error/skip |
| r8-native-candidate.xml |4760ae068d425ecfec52f653cad2c7f92e142783fa499d4d8e5db1cd729cba82 |106nodes,0failure/error/skip |
| r8-native-identity.json |ffce95cd30aa000826587bb27add712a62f04caf2176e34e09725e327ed690e7 |Exact source/trees, scope, no native mutation |

Exact source hashes match the locally tested source:
- v2_owner.py:861ed1620597c9b6186cfa5c3c96be50b56d70be1462987f47e693ba1ccc88cb
- v2_bank_receipts.py:86207fe6ec83bb5d4fcf6964e3e1daabb2298e2065562d65499d94cdd61e3f0b
- v2_bank_routes.py:0225b69496ebabb81fea9360e1b26306a0e6e37aeb3ffce982bb08251a85513d
- tests_mz2_current/test_v2_bank_business.py:2e9b2fd0465509716c601f13aba06e28e8348a7ba0c88151025b4dd7197b716f
- unchanged mz2_v2_port.py:13b14e5c45114316681487d01dddf0395fdbe120098569554fab3bf567468f70

## What passed and what remains

The actual endpoint/service resolves a saved owner, enters the current public
Accounting owner, applies local admission in the same Mongo session, verifies a
pending private receipt against an exact native incoming bank movement and native
bank account, posts public V2 bank/receivable legs, and atomically saves the bank
classification plus local order/payment/event/binding/outbox. Repeated/concurrent
calls produce one effect. Injected post-write failure rolls the synthetic business
and journal back together. Missing/paused/revoked/stale/inactive/mismatched source
and closed-period cases reject without partial payment. A malformed receipt or
corrupted history cannot be accepted merely by replaying an old success.

A real inert PNG is structurally inspected as test evidence; no real customer
receipt, provider/bank API or merchant database is involved. Native command
monitoring asserts no Legacy ledger/account access in the tested business path.

**The approved native receivable is a test prerequisite, not a completed
production agreement/advance producer.** New handling is bounded to SAR collection
of that already sealed debt. Payments before the debt, missing native identities,
other currencies and incompatible prior financial history fail closed. This does
not approve tax/revenue semantics or all special-order accounting paths.

The full PR still has23overlapping paths and9actual merge conflicts against the
reference. No current-source repair was overwritten and no conflict was forced.
Supplier/inventory/COD/shipping/settlement/refund/report migration, stored Salla
balance sidecar, real operational UI and both Android clients, global worker
inventory, full business UAT and a compatible backup/restore/rollback target/drill
remain open. Existing workflow/ledger callers are unchanged; the new router is not
registered in server.py. A generic transport/API success does not waive these holds.

No merge into Production, deploy, live financial/provider/customer write, global
control/opening change, /app, Codex source/intent/lease change, APK/OTA or operational
restore/rollback occurred. Continue from current task HEAD and preserve Accounting-
first release sequence. Full earlier scope and corrected local test-environment
failures are retained in R8_NATIVE_BANK_V2.md; R7 history remains byte-preserved.
