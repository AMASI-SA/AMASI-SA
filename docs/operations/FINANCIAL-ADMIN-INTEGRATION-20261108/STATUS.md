# Financial administration integration — WIP, not released

Owner authorizes completion, integration and governed V5 publication. This supersedes the old task publication holds, not another task's active lease. No courier APK is requested or required. Do not merge mobile PR344 wholesale.

## Verified starting point

Fresh clone of production branch: `0241e162bfe0d7d10faa85f3f4c023929a96ea2c` (PR1798). Isolated task branch: `codex/financial-admin-integration-20261108`. Original PR1794/db1082796205e018d2915d6ab0f3b979484b6663, PR1781/e33bf4dffd3a7075b0c7aca0ceaf14dede19a170, PR1801/30b2e76f64fc748aaa10a24551fb46dd9cc49f87 and mobile344/5fe6844bb3a601e97dfa84a3b6e92af47818b221 were freshly verified open/draft/unmerged. Read AGENTS, task status files, Issue1006 body and relevant PR comments. Issue comment retrieval failed transport; issue has2500comments, use bounded owned body entry.

The six source commits from the three server PRs applied without conflict to latest production in isolated checkout. Original branches and frozen intents remain unchanged. This is a candidate integration, not combined acceptance. Existing production intent inherited unchanged is NOT a valid intent for this new source.

## Live read-only baseline

Authenticated financial-position UI read on 2026-11-08 Riyadh: saved calculation01:40, rendered report01:43; selected SAR; operational start2026-11-01 00:30. All major actual tables still display incomplete except customer wallet statement table. Provider summaries: Salla585 paid/remaining evidence,20missing collection times,25review; Tabby155/7missing times/148fee matching/8review; Tamara228/6missing times/9review. These overlapping diagnostic counts are NOT unique affected orders. No all-table monetary impact established. Aramex absent from posted company/detail selectors; live paid/cash effect remains unverified. Profit remains unavailable. Salary detail is112employee-days Nov2–8, requiring explicit comparison with opening inclusion policy, not silent backfill.

No money, settings, approval/rejection, opening, backfill or provider refresh was performed. Existing approved Emkan/Aramex settings have not yet been freshly reread in this continuation; do not reapprove them.

## New candidate work

- Cash-only audit after-balance projection avoids rebuilding full financial report; transfer/refund arithmetic compared against existing report. Debt/POS counterparties retain existing path.
- Conservative table-specific source dependencies and diagnostics; unknown failures still block all; prepaid failures affect cash and shipping. Provider calculation readiness distinct from settlement evidence. No new monetary values.
- Daily financial movements page in existing operational WEB administration, independent of receipt page, with Riyadh ranges,10rows,cursor and historical unavailable states. No delivery app edits or new application.

## Fresh verification and limits

Performance worker:100newreceipts/5000historical movements,3runs baseline6.538319/6.342531/6.346509s; candidate1.098389/1.132492/1.158673s. All100intermediate balances equal; about82%median reduction. Isolated Mongo8.0.12 daily/approval/permission and new projection tests52passed; owned process stopped. Root independently reran new projection suite5passed,exit0. Initial incorrect harness23passed/4environmentfailures retained (missing test import path and Mongo URI), corrected without skips or waived gates. Debt-heavy performance remains unverified.

Web worker actual React9tests passed,exit0. Initial harness cwd/dependency failures corrected; spreadsheet export irrelevant to page mocked, actual dates/money formatting exercised. No build/device/browser acceptance. Coverage tests and final result recorded in continuation PR.

## Open work — mandatory before release

1. Daily backend currently disallows non-owner browser staff even with scoped read permission; implement correct existing web authority and scope checks. Add per-currency totals before pagination, after account/date filtering. Handle unavailable native rows safely. Tests must verify account confidentiality,scope revocation,counts and totals.
2. Finish current-store Tabby approved policy inspection and suitable selector; current PR1801 still overly conditional. Verify Emkan bank refunds and original capture/revenue idempotency; fresh Aramex policy/evidence and real debit acceptance.
3. Complete real same-period profit collector/calculation and COD forecast coverage. They remain unavailable; do not claim implemented from new diagnostics. Payroll Nov1 requires opening-exclusion impact review under existing approved policy, not direct posting.
4. Complete test suites and independent integrated review; scope issue mapping is candidate until checked against all source paths. Screen/export/print parity and unique live affected cases still open.
5. Verify current mobile release/source compatibility; no courier APK. Existing administrative web is correct implementation target in this checkpoint; existing employee app optional portion not yet evaluated.
6. Fresh production fetch, guarded V5 sourceA/build/new intent-onlyB/final-headCI/clean-clone gates/merge/publish and3officialverify remain undone.

## Access / release status

Emergent normal management is authenticated. Its VS Code terminal opened but password submission returned `Incorrect password`; manual authentication handoff is pending. `/app` does not exist in local runtime. Therefore shared guard status has NOT been executed and no active/inactive lease claim is made. No shared `/app` edit or release rehearsal, own lease,merge,publish or APK occurred. Git clone/fetch works; direct push lacks GitHub credentials, so checkpoint uses authorized GitHub Git-object connector, not credential discovery.

Next safe action: complete terminal login; run `cd /app && python scripts/production_release_guard.py status` before any shared workspace action, respect active lease. Continue isolated missing backend/admin/calculation work from this remote checkpoint. Production changed by this task: **no**.


## Resume checkpoint — 2026-11-08 (not release A)

Latest production source branch46968debe82ff9aa5791aad7cb0da81b24297572 merged cleanly into candidate, preserving newer supplier-receiving and sold-profit UI work. Shared terminal login succeeded; guard status observed active:false before any shared edit. Shared HEAD46968debe82ff9aa5791aad7cb0da81b24297572, no writes by this task. Live health source f191c71e4001b1c3d1e524d848bee5cb39a904e4/release rg5-d3fa4b46369478a0b4c9aaffa882fd59a240ab7adfc5f15792fb96a40cae635a, identity/hashes/frontend verified true. This is existing deployment evidence, not this task's publication or official three-probe acceptance.

Fresh authenticated report Nov8 16:31Riyadh, startNov1 00:30, SAR: known bank56,254.49/cash244.29, supplier payable133,235.20. Incomplete retained. Provider diagnostics Salla619/20undated/25review; Tabby165/7undated/158fees/8review; Tamara240/6undated/10review;13unknown review. Overlapping counters, not unique affected count. Financial effects have not been backfilled or patched.

Live tariff read confirms existing Emkan6.99%+1.50/15%VAT/Nov1, unchanged. Tabby has two matching detailed tariffs plus longer distinct tariffs and no generic capture policy; saved detailed VAT15% HALF_EVEN on rounded component, components4.99%+2%+1SAR. New candidate explicit merchant-plan-availability scope pins existing tariff hashes, effective dates and evidence without duplicating/reapproving rates or assigning plans to orders. Scope configuration NOT saved in production. Root52tests incl1000arithmetic comparisons pass; setup/API end-to-end persistence and reviewed live application still required.

Daily browser read/write separated through canonical accounting permissions and assigned-account checks; currency-separated totals before pagination.118tests pass on isolated Mongo8.0.12, no failures/skips,24.30s; see DAILY-VERIFICATION.md. Admin web9suites109tests pass. New Emkan/Aramex release7 plus existing regressions116pass real isolated Mongo8.0.12: partial/full/retry/Salla reduced value, original platform fees retained,34.50prepaid at correct boundary once. These are synthetic acceptance, not current live balance proof.

COD full-scope evidence and same-period profit collectors are WIP.202coverage/regression tests pass; one Mongo integration not yet run in that set. Sales/payroll/COGS/shipping/provider components present, additional ads/recurring pending. Catalog estimated COGS must not certify actual profit; exact source and VAT policy still under review. No forced completeness or unknown zeros authorized.

Remaining gates: finish profit sources/historical costs and tests, plan scope persistent authorization tests/live reviewed config, accurate unique case evidence and payroll opening-day review, courier contract compatibility, integrated performance, final build/CI/V5A/newB/clean-clone and authorized publication/official3checks. Production changed by this task:no. No own lease, merge, APK, financial write or backfill.


## Reviewed follow-up WIP (before final broad rerun)

Saved checkpoint e58a8a5719950497df50f843818d08f402f8c079 preserves period collectors, expense sources, courier compatibility and profit screen/print/export evidence. New subsequent candidate work closes legacy movement and receipt/options/review assigned-account bypasses (123isolated replica tests); whole-company report/account/audit scope is still being closed. Read permission does not authorize writing/review. No guard is bypassed to keep old permission-free tests passing; authorized test actors now have explicit grants and bank assignments.

Actual driver review audit performance100/5000: bank7.961→1.194s,POS7.478→0.076s, all200snapshots equal aside from timestamp/seal.96real-Mongo tests passed; independent9tests+250deterministic mixed debt states matched fullreport. This is audit-hook latency, not full HTTP throughput. Salary frozen-day conflict guards, bounded payroll workload, proven COD zero fee, and stale sale exclusion checks89tests pass. Root already-net Salla compensation helper12tests passes; full/partial net sale uses current explicit VAT once, no bank/provider write. Independent returns+profit25pass.

Tabby scope UI now on existing owner fee page: explicit contract selection/date/evidence/version, no auto-selected plan, no tariff duplication;43frontend/APItests pass. No scope/config saved live.

First broad local replica run:3320passed,29failed,2errors,212.47s. Most fixture errors lacked replicaSet query marker despite replica running; harness corrected, no tests disabled. Four old permission expectations updated to explicit grants/denial. Two perpetual-incomplete COD assertions updated to proven77total; preview+prestart39pass. Full rerun pending latest fixes. Earlier CIe58:21workflows success,3failure including operational suites and inherited stale intent; failures not waived. Final sourceA/build/newB still not frozen.

Read-only shared environment attempt had no active operational state; it does not match live owner UI. STOPPED this database path, no alternate credential/dataset discovery or write. Live accepted evidence remains owner browser report, not preview DB. Report Excel download tool failed, no file attached or parity claim from it. Runtime identity already recorded remains existing production, unchanged by task.
