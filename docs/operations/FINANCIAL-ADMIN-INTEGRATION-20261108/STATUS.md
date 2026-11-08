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


## Scoped account continuation — 2026-11-08, publication blocked by another lease

Remote e801271489c1330c2558a4b41728dc9d5bf07e81/tree16cb11c62812217c39a2d2b6548b6598ac3e58aa freshly matched local3320e754. Production branch remains46968debe82ff9aa5791aad7cb0da81b24297572. GitHub CI on e801:23workflows success including OperationalBalanceWeb and OperationalStockEligibility; only ReleaseReadiness fails. Its source transition gate correctly rejects integration history containing prior intent updates. Preserve this WIP PR1806; create a new clean source branch based on latest production with the exact reviewed final tree, then new build and intent-onlyB. Do not weaken the gate or rewrite old checkpoints. No finalA/B frozen.

Closed assigned account/whole-company report boundary; see ACCOUNT-SCOPE-VERIFICATION.md.108isolated replica tests pass22.91s. Frontend broad selection43suites493tests passes19.623s; subsequent scoped-reader home/link change63focused tests pass3.858s. Two initial frontend command attempts failed before collection because the governed wrapper sets repository cwd; corrected via yarn --cwd frontend, no test bypass. Full final backend replica selection running with corrected replicaSet URI; results pending.

Fresh shared guard reports active:true, actor codex-supplier-receiving-1809, prepared2026-11-08T13:47:54.676049+00:00, source d1c2a3268e81ef6305cf853a66e7c7e1e855082f, release rg5-be0277a9f9c55b6060330447f97aef2b000da6d786f9366ca7365906d9ce6450. No shared edits, rehearsal, own lease, merge or publish while this lease is active. Emergent shows another deployment finished Nov8 17:01Riyadh; this is NOT our release or proof of our changes. Continue isolated tests/checkpoint/source preparation. Never close the other lease. Production changed by this task:no.


## Computed coverage and tariff-versus-settlement disclosure

Scoped-source full replica rerun:3376passed/4failed/220.12s. The four remaining tests expected staff access to consolidated reports; corrected to assert scoped403 plus positive owner path. Focused real-MongoHTTP18passed3.59s. No permissions relaxed, tests disabled, skips or waived failures.

Dependency banners now derive from computed customer+provider collection coverage and profit components for every currency, with ready/incomplete/unavailable distinction. A late receipt-effect conflict also invalidates forecast monetary position, without erasing the independently proven forecast change. New9tests and related72passed; root21dependency/route tests passed. Raw/prefixed/split provider order diagnostics deduplicate one underlying order;10coverage tests pass, issues retained.

Provider table now separates tariff commission/net of paid amounts from actual settlement fees, known partial actual fee subtotal, and comparable variance. No tariff becomes zero merely because an order is fully settled. No missing statement becomes zero actual fee. Variance requires exact fully-settled same paid-order scope without unmatched refunds, outgoing/extra/opening allocations or unprovedFX. Existing financial principal/net/fee replacement arithmetic unchanged.14newtests plus related155passed. Generic screen/export/print share these columns; live parity still unverified. Latest live17:06baseline in LIVE-BASELINE.md.

Original three task records now point here, retaining original history. New tests added to operationalCI presence checks. Final integrated backend/frontend rerun pending this checkpoint. Shared active lease remains other task's; pending status command waits on release operation lock. Do not interrupt its release or perform rehearsal. Production changed by this task:no.


## Final integration verification continuation

Checkpoint cdce0000b7fa34d0310ab23de9fca0bccbd816e7 matched local887e06b0 tree; production remains46968debe82ff9aa5791aad7cb0da81b24297572. Integrated isolated Mongo8.0.12 replica run completed: **3404passed,15warnings,222.42s,exit0**, no failures/skips. Runner covers operational balance suites, supplier cutover, open/bundle costs, noncash review, real-Mongo driver approval, fee binding/boundaries, same-period expenses/returns, computed dependencies, tariff-versus-statement and courier released-client compatibility. Frontend43suites494passed14.924s; added provider tariff/actual/unknown-variance screen/print/Excel snapshot parity case, focused32passed4.411s. Shipping SSOT/calculator16tests plus18subtests pass0.50s. These are isolated tests, not live completed balances.

The other release owner closed its lease; shared guard was observed active:false after its release operation lock cleared. This task did not close/abort that lease and has made no shared workspace writes. Recheck immediately before any rehearsal/shared action. No own sourceA/build/B frozen, merge, publication, config write, financial backfill or APK.

Final audit identified known payment/refund/delivery diagnostic codes still falling back to all balance tables. Bounded correction is being verified: independent suppliers/payroll must not be blocked by a reviewed payment/shipping source failure, while possible bank/cash/custody effects remain blocked. Unknown diagnostic or unverified source phase remains all-table fail-closed. Next preserve corrected verified tree, create clean release source branch parentlatestproduction, then fresh V5 governed build/intent-onlyB/headCI/clean-clone gates. PR1806 remains recoverable WIP history and must not be merged directly.

Bounded diagnostic correction implemented: exact reviewed source markers required; historical capture now retains bank/cash/custody risks, unknown markers/codes fail closed. Root combined coverage/computed/fee comparison76passed1.48s, exit0. Final web selection43suites495tests passed15.44s, exit0, including screen/print/Excel fee parity. Final full backend regression will be repeated on the clean source checkpoint; prior3404pass precedes this final diagnostic dependency change.


## Frozen release A/B — PR1813

Release branch codex/financial-admin-release-20261108: source A44839cef58b0da156d5d632b0a0e56cd1dca1386, intent-only B8f63cc25da2bdd996c90edeb47a6f4a850fd6519, J46968debe82ff9aa5791aad7cb0da81b24297572. Btreef21c444ed63da23faa41469261204dfa463e078d. Runtime rg5-047c1ced18a0abb439ddb58e325cf4ae8579849438c206d50e0455fe22c002fa; intentSHA256df9ce6604639986cc746d7189188b2ed11a80801472ba09832cfc7fc854e97a3. New clean ancestry preserves previous source/intent history in WIP1806; do not merge WIP. No rewrite/squash/rebase of frozenA/B.

Fresh exact-A3448backendPASS/15warnings/226.35s isolated Mongo8.0.12 replica. Final43frontend suites495testsPASS15.44s. Independent119financialtestsPASS1.92s. Governed Node22.23.2/Yarn1.22.22 two clean builds match, artifact8ad1b758279216af99d162c6f4fb02dfc73a4e7f5748638606ea53f555aeb03a/proofcb066616bb0e25fca8ed1bf57aa3ff395d59b75f800cc88fa3ac22dc569d0ed3. Candidate bootstrap flag required for new sources; freeze correctly rejected bytecode generated by test subprocesses until tests completed and generated files were quarantined outside local source. No ignored files used as deployment input, no gate weakened.

Fresh A performance100receipts/5000history: bank7.9906→1.4629s/POS7.0592→0.0808s;200full snapshots exactly equal with fixed clock. Audit hook only, not full HTTP throughput.

B CI running; clean local clone adapter rehearsal started. Fresh shared guardinactive, sharedHEADJ trackedclean/5untracked preserved. Live prior source d1c2a3268e81ef6305cf853a66e7c7e1e855082f/release rg5-be0277a9f9c55b6060330447f97aef2b000da6d786f9366ca7365906d9ce6450 has verified identity/hash/frontend flags true. This task has not published. No ownlease, merge, production/config/money write, Tabby scope save, backfill orAPK.

Next: finish exactB checks and local/CIcleanclone package+runtime acceptance; refreshproduction and sharedguard; only then reviewedtree merge,sharedmaterialization,prepare/prepublish/onepublish/3officialverify. Then live read-only before/after all tables and reviewed Tabby scope application; external evidence gaps remain explicit. PR1813 is current release evidence, Issue1006 updated. This doc-only continuation commit stays on WIP1806 and never modifies frozenA/B.


## Compatible successor frozen A/B — PR1817

Final compatible pair: source A8408b7df4b6ceb75e9a08565f3574023f99cc8a4, intent-only B06b4eb7727c6e63ee2c4a78aa0c2242fb3041b9d, J22634586abdbdf55a5d41726acce4280674bde61. Btree7662a3aafdc60e41ef5785685d7294480422337b; runtime rg5-659dcf7fdb737c8ea67a88787f512de7a67fe25d283c59c7cc523040b1b43e73; intentSHA25694817ad4877f03e369a08b85543789404ea4001ec2639d44aff2351351b2e6be. A/B frozen; no squash/rebase or edits.

Preserves all10newer campaign source/spend paths from1812/1816 and99reviewed financial paths without overlap. Implements financial administration in existingWeb, assigned-account authorization, daily movements, per-table evidence coverage, same-period profit, dated Tabby existing-contract scope, separate tariff/actual settlement fees. Emkan refund and Aramex prepaid idempotency preserved. Courier18/19API; no mobile344 merge orAPK.

Fresh successor3523backend testsPASS241.31s/15warnings/no skips on isolatedMongo8.0.12, including newest campaign tests.44frontend suites511testsPASS25.842s. Previous reviewed audit-hook100receipts/5000history bank7.9906→1.4629s/POS7.0592→0.0808s,200full snapshots equal; optimized financial source unchanged.

Official source release-readiness42496904750 passed two clean builds and produced candidate artifact13623957039. Downloaded ZIP digest68c85b491a39f3e90f10e5500ea75bb000e283e957c77d34fa971a2ca7325281 verified; official load_candidate_release_intent validated exactA/J before trackingB. FinalB CI and host20/24gitless adapter/HTTP gates pending. Old pair inPR1813 passed all24applicable workflows and independent deployment-environment rehearsal but was not merged/published because production advanced; oldA/B remain intact.

Shared guard status is blocked waiting on another release operation lock (confirmed kernel locks_lock_inode_wait). No interruption/abort/override. Existing deployment finished18:46Riyadh belongs to other task. No ownlease,shared/app source edits,merge,publish,liveconfigsave,financialbackfill orAPK. Next finishBchecks and waitfreshinactiveguard; then reviewcurrentproduction,mergeexacttree,materialize,prepare/prepublish/onepublish/3officialverify and livealltable acceptance. Source gaps remain unknown until proven; Tabby scope not yet savedlive. FulltaskSTATUS/LIVE-BASELINE retained.

Read-only process diagnosis confirms the lock holder is running production_release_guard.py verify; our status waits on locks_lock_inode_wait. No process interrupted and no lease bypass. Frozen source records remain unchanged; this continuation lives only on WIP branch.


## Final B gates passed — 2026-11-08

All 24 applicable workflows on B06b4eb7727c6e63ee2c4a78aa0c2242fb3041b9d passed; unrelated TikTok candidate skipped by condition. ReleaseReadiness42497307440 includes successful actual gitless clean-clone adapter/package/HTTP JSON tests on host20 job128782924138 and host24 job128782924141. Other owner verification completed naturally; shared status active:false, HEAD22634586abdbdf55a5d41726acce4280674bde61 tracked clean with5untracked preserved. Fresh guard inactive before starting independent deployment-environment worktree rehearsal /tmp/financial-admin-1817-B-20261108. Rehearsal in progress, no ownlease/merge/publish/sharedsourceedit/configwrite/backfill/APK. Next inspect rehearsal exit and package/identity, refresh production/guard, merge expectedB and follow V5 authorized publication. FrozenA/B unchanged.
