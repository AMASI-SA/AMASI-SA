# Track H — Phase 2 differential and implementation boundary

Status: MZ2_ACCOUNTING_UI_PHASE2_DIFFERENTIAL_READY (source/presentation readiness; not financial activation).

Fresh Production HEAD `5a7b44b71c6c9974aba358493b3267a47d6e6314`, tree `87f9a44af5dc7d00fbc62417fb17e2f6273d8d93`. Isolated branch `codex/mz2-accounting-ui-phase2`.
Original audit: Issue #1006 comment 5912052425, preserved verbatim in PHASE1-SOURCE.md. The 125 individual rows and original classifications are retained in DIFFERENTIAL.json.

## What changed since Phase 1

Production includes the first-run owner setup gate. Source diff from Phase 1's `18432683a549bf50a2c994fcb45113115f231cfb` confirms only owner permissions/first-run routing changed in the accounting frontend/core contract. #1209, #1212, #1213, #1214, #1215 and #1216 were freshly read as open/unmerged. Their code is NOT imported and their capabilities are NOT promoted to Production support. New supplier operational screens do not establish native supplier accounting.

## Live read-only inspection, 2026-09-30

All nine accounting navigation destinations were opened in the authenticated production browser. No forms were submitted and no financial controls clicked. This is visual runtime evidence, not a claim that the deployed bytes equal GitHub HEAD.

| Destination | Observed state |
| --- | --- |
| Primary Accounting entry / opening-balances | First-run redirects to saved setup sessions; opening remains separate. No session loaded/edited. |
| home | Setup/preview state, missing approved cutover evidence, balances unavailable; recent settlement evidence visible. |
| financial-accounts | Canonical directory empty; existing opening UI and writer-transition boundary visible. |
| settlements | Register and draft files visible; old bank choices still present in binding context. |
| shipping-cod | Rates/candidate/settlement sections visible; no approved rates or current candidates. |
| inventory-purchases | Partial-workflow links only; no accounting valuation. |
| financial-movements | Bank evidence/forms visible, no stored movements; context still exposes old bank identity. |
| payroll-obligations | Employee cards show zero balances despite unapproved cutover; do not reproduce this misleading presentation. Track B owns this file. |
| journals-reports | Financial position, trial balance and journals each return not_ready / cutover_not_approved. No zero balance is inferred. |

There are no separate Ads, Tax or Expenses destinations in the current nine-page Accounting navigation. Existing operational pages are not alternative accounting sources. The new domain report selectors will expose only available ledger records and explicitly name missing contracts.

## Twelve requested areas

| Area | Safe Phase 2 presentation | Dependency / unavailable capability |
| --- | --- | --- |
| Home | Existing home + shared twelve-area directory and availability labels | No invented KPIs or growth |
| Daily movements | Existing evidence rows, status, search, loading/error | A bank identity; C supplier payment; no new writes |
| Settlements | Existing register, filters, evidence/detail, unified amounts/status | A canonical bank picker; existing workflow preserved |
| Shipping/COD | Existing candidate display/filter, clear bounded scope | P02 remains locked; no aggregate receivable from candidates |
| Suppliers | Ledger account statement only when report available | #1215/#1216 payable/advance allocation and native invoice producer absent |
| Inventory accounting | Explicit valuation gap, operational/financial separation | Stage 10 #1214 untouched; quantity × cost prohibited |
| Employees/payroll | Ledger statement only; existing payroll file untouched | #1209 canonical employees, salary history, monthly paid contract |
| Advertising | Native ledger account records for prepaid/payable separately | Provider attribution, daily spend, funding settlement not inferred from ad ID |
| Expenses/obligations | Ledger entries and explicit subaccounts only | Recurring operational schedule is not MZ2 liability; forecasting gap |
| Tax | Existing tax ledger entries; Sales/Input VAT remain separate | Filing periods/evidence register absent; no tax computation |
| Journals | Existing entries, debit/credit columns, effective date, group drill-down | No manual journal, general attachment or approval endpoint invented |
| Reports | Existing server reports; scoped filters and local pagination | Financial-position still reads accounts for classification server-side: backend dependency, no new client fallback |

## Shared architecture and acceptance criteria

Use existing AccountingShared/AccountingWorkspace and existing service adapters. Add a presentation-only AccountingUI module with PageHeader, MoneyDisplay, SummaryCards, Filters, EntityPicker, StatusBadge, EvidenceBadge, BalanceBreakdown, JournalTable, AuditTimeline, EmptyState and ErrorState. Preserve original top-level routes, first-run, permissions and write-control. Do not change AccountingPayroll, AccountingSettlements, onboarding files, backend or release files owned by parallel tracks.

Report domains use only the existing report GET contract, sending `as_of` only. Local filters clearly apply to loaded records, not the store. Server-returned balances are never recomputed; debit/credit legs remain separate. Missing/null/invalid amounts display unavailable, never zero. Detail panels use existing records, support Escape/focus return, and fit mobile width. Empty, loading, forbidden/error and ledger-unavailable states remain distinct. Unsupported items show an explanation, not a fake button or fallback request.

Implement only the ready presentation seams. READ_ONLY is a capability restriction, not write permission. NEEDS_ADAPTER and BLOCKED_BY_BACKEND remain explicitly deferred. Existing writers and confirmation workflows are retained; Track H introduces no financial action and no backend workaround.

Verification: focused behavior tests for zero/null, report readiness, stale requests, filters, exact API params, debit/credit/date, modal keyboard and no mutation; current accounting regression suites; source Vite build; actual desktop/mobile browser captures using clearly labelled synthetic local fixtures. No live records in committed screenshots. CI and exact HEAD/TREE recorded at delivery.

Production financial writes = 0. Merge = NO. Deploy = NO.
