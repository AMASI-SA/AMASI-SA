# R7 — verified Accounting V2 transport contract, NOT full feature acceptance

Task MZ2-SPECIAL-ORDERS-001, 28 September 2026.
**IN_PROGRESS_NOT_READY_TO_DEPLOY / WAITING_FOR_VERIFIED_ACCOUNTING_DEPLOYMENT.**

## Owner release sequence

The owner's latest instruction is binding: prepare compatibility now, but do not deploy special orders until Mezan Accounting has been deployed. Verify the actual published runtime and then revalidate this integration against that version. An Accounting PR, merge or passing test does not itself prove publication, and publication alone does not grant special-order deployment or financial activation permission. No automatic deployment watcher/action was created.

## Exact tested source

- Task implementation introduced: `84f76545d71e781befea83feae03799b98094e94`.
- Final tested task checkout: `622f965a08cb5d20aa0e46421f517c13b315b3bf`.
- Tested task tree: `4c51ad8a88fb71219b4e25e74435eced93d2b3c3`.
- Difference between the two: test workflow prerequisites/environment only; transport/runtime code unchanged.
- Separate Accounting reference: PR1180/B7 `121fbcf7d7c3f1dc9317e3edf108f512cbcb37e3`.
- Accounting reference tree: `2a6a6a611c2222595212dac9317aeed0f1963c5c`.
- CI asserts all tracked files in the separate Accounting checkout remain unchanged. Codex's remote branch, source/intent pair and review scope were not changed.
- A subsequent documentation checkpoint records this evidence; it is not a separately tested runtime revision.

## Observed result and exact counting

Workflow: Mezan Special Orders Accounting Target Contract.
Direct push run **36467170597**, job **109080043654**, conclusion **success**.

1. Unchanged Accounting reference regression: **44 tests passed + 7 subtests passed**. Pytest's JUnit `tests=51` includes those seven subtests; it is NOT 51 separate test functions. The 44 testcase nodes comprise 31 ledger cases, 6 write-control cases and 7 fail-closed cases.
2. New transport tests: **48 passed**, comprising **30 pure payload cases + 18 actual-Mongo owner/V2 integration cases**.
3. Both XML reports: **0 failures, 0 errors, 0 skips**.
4. No-skips/identity and unchanged-Accounting checks passed. Dedicated database container cleanup completed successfully.

Workflow: https://github.com/AMASI-SA/AMASI-SA/actions/runs/36467170597
Artifact: https://github.com/AMASI-SA/AMASI-SA/actions/runs/36467170597/artifacts/10990515437

Do not add the historical R6.1 counts to imply these are all new tests or that all existing workflows were rerun. The initial progress summary's 51 reference results is precisely 44 tests plus 7 subtests.

## Independently downloaded artifact

Artifact ID **10990515437**, archive **3514 bytes**.
GitHub archive SHA-256 matched the locally recomputed value:
`3ec04fb6fb2851d68dcb14cf48c4116e147534d1e7086300b1d55bf7bd349b3f`.

| Retained file | SHA-256 | Checked content |
| --- | --- | --- |
| mz2-reference.xml | d5b9f838f7f06f1843d2b4cdaa08aa93459ac2701244cf11c99335e21915aa9c | JUnit51, 44 testcase nodes, log44+7subtests, zero failures/errors/skips |
| mz2-target-contract.xml | bf76980d7f99a077acdaba6d0d0fd78132d8860363b86c8bbd4c2f9a5e23571d | 48 testcase nodes, zero failures/errors/skips |
| mz2-target-identity.json | 73ff66fc5671ed7340046d1571cc750916b9fb4d74ede8f5e64f1090a5741515 | Exact checkout/trees above, source hashes, unchanged Accounting and no deployment/full-migration acceptance |

Tested task source SHA-256:
- mz2_v2_port.py: `13b14e5c45114316681487d01dddf0395fdbe120098569554fab3bf567468f70`.
- tests_mz2_target/test_v2_payload.py: `60dedf7c62551d814c688f3e6c06c84b618b1c03ff033e3cef2a72ee23c57ca8`.
- tests_mz2_target/test_v2_owner_integration.py: `caae25b9635af1bfa720e2059fc8f57381d881c7121d36df5e74c20e885a345c`.

## What these tests establish

The unbound new port joins the actual Accounting `SessionDatabase`/`atomic_owner` and public V2 journal APIs in the separate pinned checkout. On synthetic loopback replica-set data it posts and verifies V2 entries, computes an exact741.40 bank effect, retries without a second group, rejects changed content under the same event key, and serializes five concurrent retries into one group. No legacy general-ledger rows are created by those transport cases.

Missing global control, missing pause field or paused state rejects before the business callback. Legacy/transition-blocked modes cannot fall back; verified active opening and cutover date remain required. Fresh disabled/deleted/unprivileged/foreign-owner actors are rejected. A plain database without the required owner session is rejected. Injected failure after posting aborts the synthetic business marker AND V2 journal; a later correct retry succeeds.

Thirty pure tests cover exact integer-minor formatting, invalid money/types, purposes, stable identities/order, defensive copies, balanced/unique legs, timestamps and evidence/policy digest formats. A digest format alone is NOT proof of bank evidence, actual account ownership or approved tax policy.

## Test environment and corrected failed attempt

CPython3.11.16, dedicated Mongo replica set from the pinned image in the workflow. The baseline and target URIs are explicit loopback addresses; there is no application Mongo fallback. The runner asserts `/app/backend/.env` does not exist before loading the unchanged baseline conftest. Test data and accounting-control/opening states are synthetic, not merchant activation.

Initial direct run36466605860/job109078139165 failed during unchanged Accounting conftest import because our focused test environment omitted openpyxl. Adapter integration was not executed in that run. The fix only completes test prerequisites using exact Accounting repository pins, adds existing test-only packages and supplies the dedicated baseline URI; no Accounting source, test assertion, production requirement, runtime logic or gate was altered. The final run above executes all required steps.

## Limits / still-open implementation

`mz2_v2_port.py` is NOT registered or called by existing special-order runtime routes. Old `ledger_adapter.py` and native supplier/inventory/delivery/settlement code still need the legacy post/readback path migrated to V2, local admission joined in the same global-first transaction, and actual bank/cost/report business evidence tested. The test marker is synthetic, not a complete supplier invoice, receipt intake or live courier action.

The current supplier fingerprint mismatch against newer Production remains open. Six shared Accounting files require cumulative review; no source guard is weakened here. Sidecar persistence, real report consumers, web/both Android clients, all-worker control coverage, closed-period business integration and a compatible Accounting-preserving rollback release/drill remain unaccepted. This result is transport compatibility against a candidate, not whole-PR compatibility, proof of Accounting publication, or approval to release.

Latest read-back of Accounting1180 remained Draft/Open/Unmerged at B7, and Production Git remained `a7bcb1626e2ad4defba2260d4a113417f91f3120`. No runtime/merchant database was probed. No Merge, Deploy, Preview/Production mutation, real journal, unpause/transition/opening action, /app use, release lease/intent change, Android publish or operational restore/rollback occurred.
