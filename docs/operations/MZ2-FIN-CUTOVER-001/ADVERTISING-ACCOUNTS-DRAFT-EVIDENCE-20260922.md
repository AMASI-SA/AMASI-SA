# Advertising accounts — isolated Draft evidence, 2026-09-22

Task: `MZ2-FIN-CUTOVER-001`, advertising accounts/debts/daily close.
Owner-approved design: [ADVERTISING-ACCOUNTS-DEBTS-AND-DAILY-CLOSE.md](ADVERTISING-ACCOUNTS-DEBTS-AND-DAILY-CLOSE.md).
Deliverable: [Draft PR #1132](https://github.com/AMASI-SA/AMASI-SA/pull/1132).

## Source and dependency boundary

Branch: `chatgpt/mz2-advertising-accounts-debts-20260922`.
Independent base: `hotfix/prod-snap-meta-final` at `6365a042dfcb125e81e5e198ea1ff1537373ce51`.
**Not stacked.** No trusted implementation Head exists for the sibling financial-account/opening-balances task in its [blocker record](https://github.com/AMASI-SA/AMASI-SA/issues/1006#issuecomment-5780414108). Its unmerged #1124 dependency is not copied or used as a substitute sibling Head.

The present commit adds the final eight backend hardening cases to the prior pushed checkpoint `927ed7e33e2421347fe29c0465d9af43f57b8720`. That prior checkpoint contains the unchanged frontend files/workflow already exercised in CI. The exact containing/final Head and its latest CI state are recorded in PR #1132 and the final Issue #1006 handoff, rather than embedding a self-referential commit SHA here.

Remote GitHub source was read via the authorized connector. Local execution used a separate sparse source directory `/mnt/data/mz2_ad_contract`, not a clone or a shared `/app` worktree. The remote branch/commits are real. No claim is made that this local directory is a git worktree or that another agent's uncommitted work was inspected. #1130, its branch and shipping files were not edited.

## Implemented and intentionally not integrated

The new Python file is a pure executable specification of validation/calculation/preview contracts. Imports are restricted by a test to Python standard-library modules. It has no API router, DB client, GL writer, scheduler, source network access or financial persistence. The React page is a real isolated component with searchable accounts, nine summary cards, thirteen columns and eight interactive permission-aware tabs, but it is **not imported into the live application router**. No default/demo accounts are injected into its application code.

Shared financial-account/wallet/payable references are opaque references, not competing financial models. The current integration's linked accounts, source completeness proof, cost-settings FX schema, permissions, audit persistence and financial catalogs have not been adapted. Proof-carrying inputs in these contracts are future trusted server projections, never browser authorization. All fixture accounts, invoices, amounts, source references and payments in the executed suites are synthetic only.

No endpoint, stored draft, scheduled close, actual payment, journal posting, opening balance or real debt is created by these tests or this deliverable. Core period-close/write-control/owner transactions are deliberately not recreated. The future integration must revalidate the inputs in the existing core, not treat these previews as executable journal payloads.

## Fresh local tests on the final source blobs

Environment: Python 3.13.5 and Node 22.16.0 for pure local contract tests only. The local Node version is not represented as the governed release toolchain. Actual PR frontend CI uses the existing governed Node 22.23.2 / Yarn 1.22.22 versions and locked repository dependencies.

```sh
PYTHONPATH=backend python -m unittest discover -s backend/tests \
  -p 'test_accounting_advertising_contract.py' -v
# exit 0; Ran 58 tests; OK

node --test frontend/src/pages/accounting/advertisingAccountsDraftContract.node.test.mjs
# exit 0; tests 17; pass 17; fail 0; skipped 0
```

**75 distinct local tests PASS**. Reruns are not counted as extra tests. Earlier backend checkpoint had 50 tests; the initial rounding-residual failure was corrected before that checkpoint passed. The final 58-case source also rejects negative/two-sided/non-cent SAR preview legs, malformed paid carrying positions, nonboolean review markers, invoice-accrual daily posting adjustments, spoofed economic keys and postpaid payment plans in other payment modes. Invoice billing currency may differ from ad-account spend currency while retaining its own FX snapshot.

Both JSX files passed TypeScript `transpileModule` syntax diagnostics locally. This is syntax-only evidence, not a local React render, typecheck, full build or browser acceptance.

## Executed PR CI snapshot on the unchanged frontend checkpoint

At inspected checkpoint `927ed7e33e2421347fe29c0465d9af43f57b8720`:

- [MZ2 Advertising Isolated Contracts #1](https://github.com/AMASI-SA/AMASI-SA/actions/runs/35760605173): **success**. Both the pure backend job and frontend job completed. The frontend job ran the 17 Node contract tests, installed locked dependencies, and successfully completed `yarn test --watchAll=false --runInBand src/pages/accounting/AdvertisingAccountsDraft.test.jsx` containing 18 actual React DOM tests. No DOM tests are skipped in that file. Backend at this earlier checkpoint contains 50 tests, not the final 58.
- [MZ2 Accounting Module #275](https://github.com/AMASI-SA/AMASI-SA/actions/runs/35760604772): **success** on that checkpoint, not a complete repository regression claim.
- [Mezan Release Readiness #2157](https://github.com/AMASI-SA/AMASI-SA/actions/runs/35760604747): **success** on that checkpoint. A readiness build is not a deployment.
- [Security Gate #1810](https://github.com/AMASI-SA/AMASI-SA/actions/runs/35760604778): **success** on that checkpoint.
- [CodeQL #1788](https://github.com/AMASI-SA/AMASI-SA/actions/runs/35760604798): **in progress at this snapshot**; no PASS inferred.

The final backend-hardening commit triggers fresh CI. **Do not transfer the above earlier-Head results to the final Head.** Read its own completed check runs in PR #1132 and the final handoff before acceptance. CI may update after this evidence snapshot without a source change.

## Tested-byte identities

Git blob hashes of the local tested/uploaded task code:

| File | Git blob SHA |
|---|---|
| backend/accounting_advertising_contract.py | `63b0fd3411725122e97e63e0809093eb6b416927` |
| backend/tests/test_accounting_advertising_contract.py | `1015884e403152cc47f133bb9263e383de9a041c` |
| frontend/src/pages/accounting/AdvertisingAccountsDraft.jsx | `e2483f3255062904fbbe43b400bd5e7cd818a401` |
| frontend/src/pages/accounting/AdvertisingAccountsDraft.test.jsx | `f8069a18337e47b7857d71dd2a7dcc294fc0ec2b` |
| frontend/src/pages/accounting/advertisingAccountsDraftContract.js | `185cafb047b082c4c2ed49ecaa109b1476071d5b` |
| frontend/src/pages/accounting/advertisingAccountsDraftContract.node.test.mjs | `2d1cb81c4db3c03970233ccffed3da1feb9cbb56` |
| .github/workflows/mz2-advertising-contract.yml | `2a0f124b8b46119a036d555516001d9697e8e788` |

## Requested acceptance groups — evidence levels

| Group | Completed evidence | Still blocked / not proved |
|---|---|---|
| 1 | Synthetic linked-registry-only selection and UI with no free-ID creation | Actual four-provider adapters and source registry |
| 2 | Duplicate owner/provider/external identity and duplicate link rejection | Persistent unique index and race handling |
| 3 | Three separate funding modes and explicit single accrual source | Sibling funding catalog validation |
| 4 | Riyadh local 01:00 boundary tests | Running durable scheduler |
| 5 | LA winter/summer, 23/25-hour day and repeated 01:00 tests | Durable duplicate-job/catch-up storage |
| 6 | Missing/invalid timezone rejected; UI no fallback | Actual linked-source timezone ingestion |
| 7 | Missing money and failed/incomplete sync rejected; known zero distinguished | Provider completeness/interval adapter |
| 8 | Detached immutable nested FX snapshot and historical revision reuse | Actual cost-settings adapter |
| 9 | Source-provided SAR never converted twice | End-to-end provider source normalization |
| 10 | Daily wallet excludes bank fee; actual fee separate in payment/debit previews | Shared GL mapping/evidence consumption |
| 11 | Deterministic invoice key and duplicate allocation rejection | Persistent invoice intake uniqueness |
| 12 | Exclusive accrual and invoice reconciliation avoid full expense duplication | Durable source allocation/recognition guards |
| 13 | Partial-payment residuals | Stored matched installment lifecycle |
| 14 | Sequential installment caps, final rounding residue | Concurrent different-payment transaction cap |
| 15 | Actual bank/cash reference, movement and proof retained/required | Matching verified real core evidence |
| 16 | 10,000 vs 10,050 yields 50-only approved adjustment preview | Approved stored difference draft via shared core |
| 17 | Posted source immutable; separate late-adjustment preview with accounted baseline | Durable revision/idempotency and audit transaction |
| 18 | Nine permission contracts, default-deny tabs, no posting grant | Actual HTTP endpoint authorization (no endpoints mounted) |
| 19 | Same-revision conflict/stale checks and concurrent deterministic key calculation | **Not** DB idempotency/concurrency acceptance |
| 20 | Static stdlib/no-legacy import boundary and no network/data adapters | Later integration regression across actual readers |
| 21 | 58 local backend + 17 local Node; earlier-Head 18 React DOM CI | Real Mongo, full integrated HTTP/browser acceptance |
| 22 | Earlier-Head Readiness/Security/MZ2 success; dedicated final-Head CI requested | Read final Head checks; CodeQL snapshot not yet completed |

## Remaining work / next safe action

Obtain the sibling financial-accounts/opening-balances actual implementation PR, branch and full Head; compare its contracts and reservations against this Draft. Do not merge #1124 or stack on it as an invented sibling dependency. Keep all runtime integration stopped until the real dependency is reviewed. Then explicitly coordinate shared router/permission/adapter/core changes, use existing owner/period/write-control/atomic primitives and run actual HTTP/Real-Mongo/browser acceptance with isolated synthetic databases. Durable uniqueness/payment-race checks are mandatory before any future posting discussion.

Task delivery remains **DRAFT_ISOLATED_SLICE / SHARED_INTEGRATION_BLOCKED**, not complete operational advertising accounting. P01 remains IN_PROGRESS. No phase STATUS file was changed and no P02/P03 gate was opened or completed.

**No Merge. No Deploy. No Preview/Production change. No real financial write. No journal posting. No real balance or debt entry.**
