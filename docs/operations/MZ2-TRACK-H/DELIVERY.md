# Track H — Accounting UI Phase 2 review

This delivery implements the safe presentation slice from the differential audit. It does not claim that all 125 capabilities, or all twelve domains' native workflows, are implemented. Backend-dependent workflows remain deferred.

## Identity and audit

- Branch: `codex/mz2-accounting-ui-phase2`.
- Fresh Production HEAD: `5a7b44b71c6c9974aba358493b3267a47d6e6314`.
- Fresh Production TREE: `87f9a44af5dc7d00fbc62417fb17e2f6273d8d93`.
- The same Production identity was confirmed by a second fetch before delivery.
- Final implementation HEAD/TREE, Draft PR and CI are recorded in Issue #1006 to avoid a self-referential commit identity.
- Phase 1 source: [Issue comment 5912052425](https://github.com/AMASI-SA/AMASI-SA/issues/1006#issuecomment-5912052425), preserved in `PHASE1-SOURCE.md`.
- Differential checkpoint: [Issue comment 5918522233](https://github.com/AMASI-SA/AMASI-SA/issues/1006#issuecomment-5918522233), posted before implementation.
- All 125 rows retained in `DIFFERENTIAL.json`: 25 READY_FOR_UI, 22 NEEDS_ADAPTER, 23 READ_ONLY, 55 BLOCKED_BY_BACKEND.
- Nine live Production destinations inspected read-only; observations and twelve-domain coverage are in `ARCHITECTURE.md`. Live runtime inspection does not attest deployed Git identity.

## Implemented presentation

| Page / component | Delivered behavior |
| --- | --- |
| Shared workspace | Arabic RTL header, consistent SAR formatting, missing values distinct from real zero, reusable status/evidence/error/loading/empty components |
| Home | Existing dashboard retained; permission-aware directory for twelve accounting areas with capability limits |
| Daily movements | Search/status filters over the existing 200 loaded rows, local pagination, bounded count labels, loading/error and stale-response handling |
| Settlement register | Shared amounts/status, bounded scan explanation, loading/error/empty states and stale list/detail suppression; existing binding/review/post workflow retained |
| Shipping / COD | Search over existing bounded candidates/bank evidence, error/retry, missing shipping cost and COD custody remain unavailable; no alternative amount source |
| Inventory / purchases | Explicit BLOCKED_BY_BACKEND / not_ready valuation and supplier breakdown; outstanding and advance shown separately as unavailable; operational links labelled separately |
| Reports | Server financial-position cards, trial balance, journals, exact account/source/domain selectors and local pagination; no computed balances |
| Journal detail | All loaded legs for the selected group retained even when the list is filtered; debit/credit columns, effective date and evidence references; native dialog with Escape and focus return |
| Domain statements | Native V2 ledger records only for supplier, employee, courier, driver, ads, tax, expenses, liabilities and assets; missing domain contracts disclosed |

Existing form actions, permission checks and financial confirmations are preserved. No new financial writer is introduced. The shared report endpoint contract is unchanged: only `as_of` is sent. New domain statement views require `ledger_backend: v2`; unavailable or failing reads never trigger another source. Print uses the browser's existing print/PDF operation.

## Backend dependencies and deliberate limits

- Financial identity (#1212): daily bank/supplier context still depends on old identity collections on the server. No new identity picker or source fallback is built. Existing canonical-directory migration is not copied.
- Payroll (#1209): current salary, salary history, monthly paid/owed, advance and custody require the native employee contract. The owned payroll file is untouched; its existing zero presentation remains a Track B dependency, not proof of readiness.
- Supplier (#1215/#1216): invoice producer, payable/advance allocation, payment history and canonical supplier identity remain external dependencies. Ledger rows are not an invoice workflow.
- Inventory Stage 10 (#1214): owned files unchanged. No product/component accounting valuation or quantity × cost workaround.
- Shipping: P02 locked states and financial semantics unchanged. Candidates are not receivable/payable balances. `shipping_cost_source` is only the existing review evidence; it never becomes an approved cost.
- Ads: provider attribution, daily spend, wallet funding/settlement absent from this report contract. No Snap/Meta/TikTok/Google identity is inferred.
- Tax: no filing-period or tax-evidence register contract. No tax computation.
- Expenses/obligations: no recurring liability schedule, accrual/prepayment allocation or deposit forecast is invented.
- Reports: existing financial-position server classification still reads `accounts`; recorded as a backend dependency. No client access to accounts, general_ledger, counterparties, settings identity or legacy APIs is added. Existing server ledger selection is untouched.
- Reports are bounded to 10,000 legs; daily movement list to 200; settlement register to 300 of a 2,000-document scan; shipping candidate arrays to their existing bounded response. Local search does not claim whole-store coverage.
- No manual journal writer, Excel export, extra attachment endpoint, or expanded confirmation workflow is invented for deferred capabilities.

## Verification

Node 22.23.2, repository Yarn frozen lockfile install, unchanged package/lockfiles. The initial Windows-only static test failure was traced to a literal LF assertion against an unchanged CRLF checkout of `accountingModule.js`; tests passed using its LF-equivalent working copy, then the original checkout was restored. There is no service or test relaxation in the patch.

- **36 suites / 213 tests PASS**, including existing accounting, navigation, onboarding and service suites. New behavior tests verify missing/zero amounts, separate advance/payable, report provenance, no fallback on failures, precise API params, local pagination, grouped details, focus return, and shipping custody never substituted from `amount`.
- **Vite frontend source build PASS**. Existing CSS import-order, chunk-size and ineffective dynamic-import warnings remain; unrelated bundling work is outside this patch. No release packaging/prepare/deploy was run.
- `git diff --check` PASS.
- Browser review: six actual component screens at **1440×1000 and 390×844**, no page-width overflow; journal search/drilldown/Escape/focus return; report not_ready/error/empty/loading states. Browser errors: none in the reviewed local session. See `browser-results.json` and `screenshots/`.
- Screenshots contain explicit **synthetic review data only**, never Production financial/employee data. They are viewport captures; the browser's full-page stitch produced duplicate segments and was replaced.
- The isolated local harness imports the actual components and rejects all writes and unknown reads. It is not an end-to-end backend execution test. Reproduction instructions are in `scripts/testing/mz2_track_h/README.md`.
- Independent CI workflow added without editing the workflow shared by Tracks A/B. CI result and links are recorded in Issue #1006 after the PR opens.

## Changed-file boundaries

Eight existing presentation files: AccountingShared, AccountingDailyWorkspace, AccountingDailyMovements, AccountingSettlementRegister, AccountingShippingCod, AccountingWorkflowPages, AccountingReports and AccountingWorkspace.

New shared files: AccountingUI, accountingUI.css, AccountingDirectory and AccountingPhase2.test. Other additions are the independent frontend CI workflow, local synthetic review harness and audit/test/screenshot evidence in this directory.

No overlap with the fetched changed-file lists of #1209/#1212/#1213/#1214/#1215/#1216. No backend, ledger core, Opening Post, write-control, cutover, Release Guard, P01/P02/G47 semantics or package dependency changes. No parallel branch code was copied.

Production writes = **0**. Merge = **NO**. Deploy = **NO**.
