# Payroll cutover-day owner interface — WIP, not deployed

Repository: AMASI-SA/AMASI-SA.
Branch: fix/operational-financial-followup-20261101. Draft PR #1677 targets the parent task, not production.
Parent: Draft PR #1675 at a8ffd5a81637bed8c4462f7bb4c2b059782cd520.
Resumed exact follow-up head: 8f838fcaa9ce424b2d4af364e87864667f7e716f.
The PR head is the current checkpoint; it is not embedded self-referentially here.
Historical cutover implementation and evidence are preserved in this document at 8f838fc; previous payroll evidence at d3f5543f4b7280bd3a223743b4c9e539d86eaa19.

## Owner decision

Payroll starts on the operational cutover day itself, inclusive, using the Riyadh business date of started_at. NOT the first day of the month and NOT the following day. An eligible employee earns a full daily share even if startup is after midnight. Daily share = monthly salary / actual calendar month days. Hire date, unpaid leave, effective salary revisions and cumulative rounding remain authoritative.

Example: SAR 3,000/month with 15 November startup earns SAR 100 that day and SAR 1,600 through 30 November. Days 1-14 are excluded. December's divisor is 31.

The prior owner/opening review remains necessary to prevent duplicate opening-covered payroll. Existing tenants without an approved policy are not silently migrated. No live salary policy has been approved in this task.

Parent #1675 contains old-order post-start delivery recognition and new supplier-invoice recognition independent of customer order age. Those changes remain inherited, not replaced.

## New interface source

- Owner/browser-only GET /operational-balances/salary-start/context returns the exact cutoff, validated current policy state and actor scope. It reads no salary source collections, calculates no obligation, and makes no writes. Invalid policy, inactive operational phase or active native accounting fails closed.
- Outer operational context advertises can_manage_salary_start only for an active owner/browser surface. The existing page gains a **بداية الرواتب** tab; employee-app and staff surfaces do not expose it. Server authorization remains independent from the UI flag.
- SalaryStart.jsx uses existing RTL operational styles. There is no editable date or month-first inference. Opening/reloading the panel performs GET only. Explicit preview shows missing days and exact decimal amounts separately by currency, not total employee balances.
- Approval requires the owner to check that opening balances exclude payroll from cutoff onward. Double-clicks are locked. Ambiguous transport/5xx outcomes retain the exact request ID and body; read-only reload resolves an already approved policy without another approval. Definitive rejection clears preview and confirmation. Owner/scope/cutoff/phase changes suppress stale monetary responses.
- Read-only context, API adapter, component and parent-navigation tests added. Existing salary calculations, policy writer, openings, payments and bank balances are unchanged by this UI slice.
- Dedicated contents-read-only candidate workflow now tests frontend operational/supplier/navigation pages as well as the existing real-Mongo backend suite. No production environment, secrets, release build, branch writer, lease or deployment is added.

## Source and local verification

Source archive: run 41318197187, artifact 13088301879, ZIP SHA256 fd05160248a63fbf273ce375d07acab3e023386b35cd514c6e9bd0232a93dffe. All 4,550 included repository files matched their Git blob hashes. Local workspace is an isolated snapshot, not /app; its local bootstrap Git SHA is not the remote source SHA.

Fresh selected local backend result: **339 passed**, zero failures/errors/skips. This includes 13 new read-only context/HTTP cases plus existing payroll, supplier, delivery and report regressions. Earlier 77 targeted tests overlap this count and must not be added to it. Python 3.13.5; isolated dependencies; JUnit retained locally as local.xml. AST and whitespace checks passed.

The cutover correction's previous dedicated workflow run 41318197479, job 124882627914 was read back completed/success, including all five real-Mongo payroll cases and database cleanup. That validates 8f838fc, not this new UI checkpoint. The older 931-test count belongs to 752c69b1, not either changed source.

Fresh full backend and frontend candidate CI are pending this checkpoint. No browser/visual, repository-wide security, production build, print/export or live activation acceptance is claimed.

## Remaining gates

Read back exact new candidate CI; resolve failures; record source identity and verified results. Then complete the operational driver bank/POS approval writer and report linkage, pre-start COD forecast and profit-period evidence. Verify the cost prerequisite's live v5 identity before isolated integration/conflict resolution and production release gates. #1676's observed merge at ffa145f22dbf4167649736336083d0d16f9fed27 is NOT publication proof. #1675 previously had conflicts with production; no rebase/integration was attempted here.

Actual owner opening-coverage review and activation remain separate from implementation. Unsupported financial totals stay unavailable.

Production changed: NO. No live payroll approval, database/ledger/opening writes, payment, /app change, parent-branch rewrite, release intent, lease, merge or deployment. Keep #1677 Draft and unmerged. Issue #1006 is full; this document and #1677 are the continuation authority.
