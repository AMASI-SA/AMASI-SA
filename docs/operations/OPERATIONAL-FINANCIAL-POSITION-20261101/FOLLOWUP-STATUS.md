# Payroll cutover-day owner interface — WIP, not deployed

Repository: AMASI-SA/AMASI-SA.
Branch: fix/operational-financial-followup-20261101. Draft PR #1677 targets the parent task, not production.
Parent: Draft PR #1675 at a8ffd5a81637bed8c4462f7bb4c2b059782cd520.
Resumed exact follow-up head: 8f838fcaa9ce424b2d4af364e87864667f7e716f.
Tested application checkpoint: ef2701ffa7da9a159284792bce1869a69551e67f.
The later evidence-only PR head is the current handoff checkpoint; no application/test changes follow that tested source.
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

## Completed exact-source candidate acceptance

Application source: **ef2701ffa7da9a159284792bce1869a69551e67f**; parent 8f838fcaa9ce424b2d4af364e87864667f7e716f; complete Git tree **1ce4f00874223ec9e820d8d42055864a2266d60a**. The tree was independently rebuilt from the full upstream manifest plus ten local changed-file blobs and matched GitHub exactly before the guarded fast-forward. No force or parent-task rewrite was used.

Workflow **41323321167** completed successfully for both jobs:
- Backend job **124897957226**: **963 primary tests plus 18 subtests**, zero failures, errors or skips. All five real-Mongo payroll acceptance cases executed. The disposable localhost database was cleaned up successfully.
- Frontend job **124897957054**: **275 tests in 22 suites**, zero failed, pending or skipped cases. Includes all **29** new payroll component/navigation cases and **3** API adapter cases alongside existing operational/supplier/navigation tests. Default CRA mock-reset behavior was preserved.
- Backend artifact **13090825765**, SHA256 `5723474a570849cded57579a2f319eaaa33739f1305ee071ed68b71d9df27575`, was downloaded and checked. Source SHA matches the application checkpoint. JUnit has 963 primary testcase elements; its aggregate 981 includes the 18 subtests. Do not double-count them or add overlapping local results.
- Frontend artifact **13090207040**, SHA256 `09072f53f92815a6d314d829edb2190b97556eace19e2a8cd4a2cc80d2a8657d`, was independently downloaded, hash-checked and parsed. Both new suites were present and all assertions passed.
- Source read-back from run **41323321133**, artifact **13089734957**, ZIP SHA256 `cbebdfc70c2d56e3758f335ebc3643695988bb787cc920111d7ae303fe2a5a2d`: all **4,554** included files matched their Git blob hashes, the complete manifest matched the source tree, and all **ten** changed files matched the tested local bytes.
- Evidence: https://github.com/AMASI-SA/AMASI-SA/actions/runs/41323321167

This evidence-only update changes documentation, not the tested application, tests or workflow. These results validate the selected operational backend and frontend suites, not repository-wide security, a governed production build, browser visual inspection, print/export or live activation. Those acceptance gates remain open.

## Remaining gates

Resume from the current guarded follow-up head and verify this tested source remains unchanged. Complete browser/visual acceptance of the owner interface, then complete the operational driver bank/POS approval writer and report linkage, pre-start COD forecast and profit-period evidence. Verify the cost prerequisite's live v5 identity before isolated integration/conflict resolution and production release gates. #1676's observed merge at ffa145f22dbf4167649736336083d0d16f9fed27 is NOT publication proof. #1675 previously had conflicts with production; no rebase/integration was attempted here.

Actual owner opening-coverage review and activation remain separate from implementation. Unsupported financial totals stay unavailable.

Production changed: NO. No live payroll approval, database/ledger/opening writes, payment, /app change, parent-branch rewrite, release intent, lease, merge or deployment. Keep #1677 Draft and unmerged. Issue #1006 is full; this document and #1677 are the continuation authority.
