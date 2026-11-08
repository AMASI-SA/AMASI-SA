# Financial live acceptance follow-up

Base: published PR1817 merge 6eed626bb8815c54db6d8cb46d45e97902e3db4f. Its source A8408b7df4b6ceb75e9a08565f3574023f99cc8a4 passed three official verification rounds (580 frontend HTTP checks each), runtime rg5-659dcf7fdb737c8ea67a88787f512de7a67fe25d283c59c7cc523040b1b43e73. Preview restored RUNNING; own lease closed. Do not rewrite that A/B pair.

## Observed failures and fixes

- Production UI attempted scope for the two approved short Tabby policies on 2026-11-01 and received provider_plan_scope_terms_differ. No scope saved; no tariff changed. Authenticated browser GET of the existing fee-policies endpoint proved all three component differences are `id` only; other differing top-level fields are plan_id, evidence, id and confirmed_at. Calculation fields, refund rules, VAT rounding and effective dates are identical. Compare arithmetic terms without component-local IDs, but retain the original immutable contract digest including IDs. Longer plans remain excluded unless explicitly approved; unknown order plans remain unknown.
- A real source-collector regression reproduces a prestart driver order with missing delivery time and no courier display name. Its delivery-phase marker was lost because display enrichment could not run, incorrectly blocking supplier/payroll/external tables. Preserve the exact resolver phase independently of display metadata. Coverage still allowlists exact known codes; unknown/owner/identity failures still fail closed. No guessed dates, new charge, backfill or opening modification.

## Verification

Both new behavioral tests failed on the published implementation and passed with the fixes. Full isolated Mongo 8.0.12 replica-set suite: 3526 passed, 15 warnings, 238.65s (runner run-finance-followup.py; no production database). An earlier three-file run had 121 passed and one environment failure because isolated Mongo had not been started; the full real-Mongo run resolves it without skipping the test. Additional two-variant 1000-amount arithmetic comparison (shared vs distinct component IDs) then passed: 15 focused non-HTTP tests. HTTP create/authorization/CAS/metadata-only tests passed in both ID variants during the full run. No frontend or courier API change in this follow-up.

## Open acceptance

Latest successfully rendered production financial snapshot 2026-11-08 19:47 Riyadh, report generated19:50. All groups remain incomplete. Tabby166 unique affected cases (159 fee-unmatched and7 undated), Salla25, Tamara9, unknown-provider12. Supplier/external coverage affected sources10199, employees10200; these are source diagnostics, not payable shipments or financial impact. Need next release plus one correct Tabby scope save, normal-worker recalculation, per-table evidence inspection and export/print parity. No claim all balances complete.

Current daily admin page read-only smoke: today3 entries, SAR975 incoming, Riyadh event timestamps, historical balance and recording time unavailable as explicitly labelled (not replaced by current balance). No real customer collection approved. PR344 remains unmerged; no courier APK required.

This checkpoint is development only. Fresh head CI, source A/build/intent-only B, clean-clone rehearsal, guard and publication still required. Shared /app changes require a fresh inactive guard; never close another conversation's lease.

## Lossless report transport

Synthetic profiling showed 12000 source diagnostics copied into table coverage132000 times, producing17,524,730 JSON bytes. Added opt-in `coverage_format=indexed`; the default expanded API is unchanged. The web client requests indexed coverage. Every full diagnostic is retained once in a catalog, with ordered per-table references preserving duplicates/counts/completeness and all rows, notes and amounts. Authentication and final scope recheck are unchanged. No courier endpoint changed. Exact reconstruction of the entire expanded report passed; indexed JSON6,677,755bytes (61.9%smaller), serialization0.0717→0.0203s, index construction0.3243s. This is synthetic transport evidence, not measured production latency.

13 authenticated financial-route tests passed, including complete reconstruction, unchanged source database, unauthorized401, invalid-format422 and unchanged default response. Relevant frontend28suites/375tests passed. Full final-source isolated replica-set suite is running again after these transport changes; not yet a final-head success claim. The full prior3526 result applies to the earlier two-fix source only.

## Integration candidate on latest production

Final pre-integration source suite:3528passed/15warnings/251.76s with isolated real Mongo, exit0. Latest production J835703d4b24c1c93da3fd91be74a21720da8f6cc includes PR1814 supplier draft membership/compatibility changes. Its supplier_receiving_routes.py and supplier financial integrity tests remain byte-for-byte unchanged. Integration is a clean merge of checkpoint34754aa4c3bd18104595bd21859ed3a47da07718 with J; no overlapping governed code paths. A premature local merge command while worktree creation was still running was rejected by Git's existing index lock; after creation completed, the new worktree was verified clean/no MERGE_HEAD and the merge was retried successfully. No lock deleted, no reset, no source loss.

Next: fresh combined integrated tests including supplier financial integrity, exact-head CI and new V5 candidate build. Do not reuse either PR1817 or PR1814 intent for this source. PR1819 remains the follow-up vehicle; production financial acceptance still open.


## Canonical release successor PR1820 — frozen pair

PR1819 is preserved WIP only. Its merge checkpoint334b4cdfde4c61f7f00fab679af37f8c5cbbb9ff carries second-parent intent history, so it cannot satisfy the strict V5 source-history rule. No rule was relaxed and no history rewritten. The same governed source is based directly on J in PR1820, branch codex/financial-live-release-20261108.

A=a14d6715512bc756728e0389436b15a98f138c10; Atree=ef109f315c48f61192069604b3a1ac5221596441; B=bfd111d4bdddc8e5f919d5c1b5489b4e6f8107cd; Btree=997d1c49ef850786aff04bb46705d58b636bcd0e; J=835703d4b24c1c93da3fd91be74a21720da8f6cc. Runtime=rg5-d59dc66bfbc9d2f2b6ea5b9d3978751dbc8035cc98f7b8497181a638560cb44c. Source candidate workflow42503127973 passed; two governed builds artifact13626636434 ZIPsha256ea36aa413404fff7d217d110aba114e3723026df34688c1f216af6d0d5427077 verified. Official load_candidate_release_intent passed exact A/J. Intent gitblob e93c31e25c179024a724f691a718c070e80e93db. No source edits after A; B changes only intent.

Integrated financial source3528PASS. Supplier47initial setup errors were due to absent dedicated BUILD20_INVOICE_TEST_MONGO_URL; rerun using isolated loopback Mongo8.0.12 replica set47PASS/94warnings/36.99s, no skips or source changes. Relevant frontend28suites375PASS. Full report lossless indexed transport reconstructs exact expanded result; no amount/completeness change.

Shared /app guard active for codex-supplier-draft-1814, prepared16:56:57.509741UTC. Respect lease; no /app mutation or follow-up publication. Next finish B CI and clean-clone rehearsal after a fresh permitted guard status, then reviewed merge/exact tree, prepare/prepublish/singlepublish/officialverify3. Production still financial-acceptance incomplete; Tabby scope has NOT been saved. Following release, save only the approved two contracts, verify normal worker snapshot, inspect actual coverage causes and export/print parity. No courier APK, real customer approvals or backfill.

## Final B verification continuation

Exact B readiness42503450869 passed all six executed jobs, including actual host Node20.20.2 and24.19.0 clean-clone adapter rehearsals without Git metadata, package proof and build-meta HTTP JSON check. Stock workflow42503450840 also completed successfully with real isolated Mongo and no skips. Remaining observed workflows were success; TikTok creator candidate was not applicable/skipped. Shared guard freshly remains active for codex-supplier-draft-1814; platform shows its Deployment finished20:08Riyadh. Do not terminate its verification, close its lease, mutate /app, or start another release rehearsal while active. Production branch fetched and remains J835703d4b24c1c93da3fd91be74a21720da8f6cc.

Read-only DOM baseline captured from existing19:47financial snapshot: banks56254.49, cash244.29, suppliers500receivable/136247.20payable, employees22838/22296.66, external13000/138693, ads21917.32/201506.19, couriers0/10614, drivers907.53/3940SAR. All these groups still incomplete; supplier/driver changes versus earlier snapshots are ordinary observed activity, not attributed to pending1820. Provider table1045paid-order count has unavailable financial totals. Warning counts do not establish monetary impact. A normal Excel button attempt failed at browser download handling without an application error; live Excel parity remains unverified, not a passing test. No funds changed.

## Owner decision and release resumption

Owner confirms daily financial movements should remain in Mezan. PR1823 was closed unmerged; preserve its branch but do not apply navigation removal. Frozen PR1820 A/B unchanged. Fresh guard inactive, /app tracked source clean atJ835703d4b24c1c93da3fd91be74a21720da8f6cc; five unrelated untracked entries preserved. Exact-B applicable CI allpassed. Official adapter build running in new isolated /tmp/financial-live-1820-rehearsal clone atB; log /tmp/financial-live-1820-rehearsal.log. No merge or publication yet. Inspect result before repeating; then package proof and normal V5 release. No source/financial/config changes.

## PR1820 merged; publication pending

Isolated official adapter rehearsal exited0, verify-packages exited0; clean B checkout and materialized source A/runtime match frozen pair. PR1820 merged normally at75b6b14762f1390d63973ef2103c0975a2056a49, expected B enforced. Preparing /app after fresh inactive guard and clean tracked worktree; five unrelated untracked entries preserved. New production HEAD must have exact Btree997d1c49ef850786aff04bb46705d58b636bcd0e and only intent diff after A. Publication still pending; next governed materialization, prepare/prepublish once and official verify3. Do not repeat merge or re-freeze A/B.


## Governed publication started — 2026-11-08 19:26 UTC

/app official adapter build exit0; package verification exit0. A subsequent guard status invocation regenerated untracked backend bytecode, correctly rejected by package verification; generated bytecode was preserved outside /app and all subsequent Python commands used PYTHONDONTWRITEBYTECODE=1. No tracked source changed and no guard bypass. Frozen A/B and merge M75b6b14762f1390d63973ef2103c0975a2056a49 preserved.

Own lease codex-financial-live-1820 prepared at2026-11-08T19:25:16.455543+00:00, prepublish ready_to_publish=true, exact A=a14d6715512bc756728e0389436b15a98f138c10 and runtime rg5-d59dc66bfbc9d2f2b6ea5b9d3978751dbc8035cc98f7b8497181a638560cb44c. One normal Publish changes submitted; UI Deployment Started Nov8 22:26Riyadh, event94ed2247-638f-5dfa-9cfd-257047a456f9. Do not duplicate publication. Explicit success and official3-round acceptance are pending. Preview backend stopped/frontend alreadyFATAL before build; restore after verification. All unrelated files retained.

Owner confirmed daily movements stays in Mezan web; PR1823 closed unmerged. No courier APK/UI changes. Tabby scope remains unsaved; save only the two existing equal short policies after new runtime is verified. No real receipt decisions, money transfers, tariff reapproval or financial backfill. All-table financial acceptance remains open.


## Published runtime and successful Tabby scope — 2026-11-08

Platform Deployment finished22:35Riyadh, version69cb828, event94ed2247-638f-5dfa-9cfd-257047a456f9. Authenticated normal health page confirms exact A=a14d6715512bc756728e0389436b15a98f138c10/runtime rg5-d59dc66bfbc9d2f2b6ea5b9d3978751dbc8035cc98f7b8497181a638560cb44c, boot2026-11-08T19:35:08.388814+00:00, backend/frontend/critical verification flags true. Official guard verify is RUNNING in Terminal2, output /tmp/financial-live-1820-verify.json; do not interrupt or duplicate, and do not close the active lease manually.

One owner-UI Tabby scope save SUCCEEDED after verified runtime read. Version6→7, scope db7b2b75-75de-45fe-b3d2-ab6d2cab2365, approved SAR/from2026-11-01/no end. Exactly policies4fca8032-06d2-4f6d-bd02-12a706aa24dd and fc2b2541-3796-4aec-b408-ac0020345786 linked. UI explicitly states tariffs/order plans unchanged. Longer plans excluded; no duplicate tariffs or legacy sync activated. DO NOT save this scope again.

Financial UI postpublication initially rendered engine snapshot22:30Riyadh (before new runtime/scope); this is NOT post-scope recalculation acceptance. A normal report refresh has been requested; wait for newer successful engine timestamp and verify actual fee/net/table coverage. All-table acceptance, live Aramex evidence and export/print parity remain open. No real receipt approved or bank refund posted as a test.


## Official production acceptance and live effect — 2026-11-08 19:52 UTC

PR1820 published and official V5 verify SUCCEEDED: verified=true, checks=3, source A a14d6715512bc756728e0389436b15a98f138c10, deployment M75b6b14762f1390d63973ef2103c0975a2056a49, runtime rg5-d59dc66bfbc9d2f2b6ea5b9d3978751dbc8035cc98f7b8497181a638560cb44c. Verification output /tmp/financial-live-1820-verify.json; final guard status active=false. Backend and frontend preview services restarted and both RUNNING. Production changed YES; deployment event94ed2247-638f-5dfa-9cfd-257047a456f9, UI version69cb828, finished22:35Riyadh. Do not repeat publish or Tabby scope save.

Live engine snapshot22:40Riyadh AFTER scope version7: Tabby170 paid/evidenced orders, unique affected7 (previous170); fee-unmatched163 resolved. Remaining7 lack dated capture evidence; aggregate financial totals remain unavailable rather than zero. Salla unique affected25, Tamara9, unknown provider14. Raw warning counts are not shipment counts or monetary impacts.

After code at22:38Riyadh, suppliers/external affected-source count10200→4992, removing5208 unrelated historical-delivery diagnostics from independent tables while retaining unresolved source issues. All-table acceptance remains OPEN. Known banks56254.49SAR and cash244.29SAR unchanged. No real receipt approval/refund/backfill. Normal provider-history-review UI showed generic failure; subsequent authenticated GET returned items=[] at19:42:47UTC (no approval was attempted). Live export download failed in browser handling; print/export parity NOT verified. Remaining: canonical source missing diagnostics (previous4759), proper per-table dependency analysis, live Aramex debit evidence, salary days, profit/forecast source completeness, provider remaining cases and screen/export/print acceptance. Next: read-only diagnosis against production evidence; any new fix must use isolated current-production branch/new A/B. Courier API compatibility tested, no device test or courier APK claimed. Daily financial movements stays in Mezan web.


## Read-only salary and source acceptance — after official verification

Normal authenticated reports snapshot22:53Riyadh confirms canonical_salla_source_missing4759 (UI samples include a malformed reference and old-looking numbers; ages are not proven solely by identifiers). 12130 warnings/30types are diagnostic counts, not monetary totals or unique shipments. One employee_salary_contract_incomplete source remains; exact internal reference is visible in owner UI, intentionally not copied to GitHub.

Salary start owner UI confirms first_day2026-11-01 NOT APPROVED. Current recorded daily rows cover16 employees Nov2–8 (112days). One read-only preview completed:16 additional days,1456.66SAR, can_approve=false, incomplete-source warning plus multiple recorded-day reconciliation conflicts. NO approval/check-box submission or financial mutation. Do not blindly add the amount or rewrite recorded days. Next diagnose recorded/proposed per-day differences (preview implementation exposes both, current UI only generic messages), resolve incomplete contract and review opening overlap before any financial backfill. Preserved salary preview tab for continuation. Production source remains the already verified1820; no new code change or deployment.
