# Option-price currency recovery: release handoff

## Current combined release — lease closed, PR1933 merged

Owner authority: «ايش هي المحادثه الي حاجزه النشر تاكد من انه النشر تبعه اكتمل وصل الخادم فك النشر انت ونشرك», then explicit confirmation «نعم موافق اجمعهم وفك الحجز وادمج وانشر». Goal remains actual Ready eligibility for otherwise eligible pieces, including full source revalidation after deployment.

The prior PR1932 lease was **officially aborted**, not falsely verified, at2026-11-15T01:14:02.965298+00:00. Command exit0 returned aborted=true for deployment7ef45017e5221635ce3c80c3788f73d9c50b1e08/sourceaf36682ec95e578764242001dccd16ef9e61c07b/releaserg5-3c726702879630f436b0b615a4e2d1dd85ff5889c45b43b1be477f84cb3043fe. Immediate official status returned active=false. The bounded runner checked exact actor/identity and no concurrent named release process. Receipt: /tmp/option-ready-release-1933/abort-1932.json.

Basis: latest prior-task durable checkpoint f3a8fad113f388c530103979fa71f10dfbded543 records prepare only; fresh live health at01:10:09UTC still served prior PR1929 source fcb827f556c40bebd843d8c9d85fedc0b60cf8fa/runtime rg5-4a98965dc9ad5922b4dbd25e5c960360af8b09f8e5590cced0b87c8c8a0f07a2 with all flags true. UI still showed100th publish75ee080 and no running publication. Historical /tmp/mezan-operating-period-original-iljwu1fd is no longer present, including known-successful prepare receipts; its absence is **not** evidence that later commands never ran. Current identity/UI/process evidence and explicit transferred authority support superseding the old lease.

PR1933 normally merged as **9afb38cf0dc0d8e8cd887f2c2cdb4a6be5a7ca68**, parents [7ef45017e5221635ce3c80c3788f73d9c50b1e08,5165b3aaeece7022d178fa02df4654ac30cafea3]. Root fetched actual Git commit: tree exactly7a89d5e1bacf3a78b268a348e27fa3c998517c75, equal accepted B. All PR1932 campaign source remains inherited; no campaign files were removed or changed by this fix.

Actual-M push gates underway: Readiness43662171036, Security43662171057, CodeQL43662171086. Do not infer their result from B. No new /app source update, original build, own prepare/prepublish, or UI publication has happened yet. Production branch changed=yes (merge); live runtime changed=no. No business/order/receipt/financial writes.

Next: accept actual-M push gates; capture/preserve120 unrelated /app paths; fast-forward exact M; perform governed original adapter/package rehearsal with no active lease. Then prepare task's own lease, prepublish immediately before one authorized UI publication, require newer success event and official three-probe verify + inactive status. Finally scoped fresh-revision dry run/apply and Ready readback for the affected order.

## Previous completed checkpoint

- Task: OPTION-PRICE-CURRENCY-PROOF-20261115.
- Repository: AMASI-SA/AMASI-SA; PR https://github.com/AMASI-SA/AMASI-SA/pull/1933.
- Reviewed release branch: codex/option-price-currency-proof-20261115.
- This separate branch is an evidence checkpoint only. Do not deploy or merge it into the release candidate.
- Owner explicitly authorized this fix's push, merge and publication on 2026-11-15: “تمام وادمج وانشر”. No new permission is required for this fix.
- Production changed by this task: **no**. No main merge, /app source change, lease mutation, deployment, order revalidation, receipt or financial write.

## Frozen reviewed source and intent

| Item | Value |
| --- | --- |
| Production base J | 7ef45017e5221635ce3c80c3788f73d9c50b1e08 |
| Source A | fc5ff89f5794061bf62a7de283eedc9ecd4ec7c9 |
| A tree | c890c51d486d58a3a487580b8fb02cd33d5b7c36 |
| Intent-only B | 5165b3aaeece7022d178fa02df4654ac30cafea3 |
| B tree | 7a89d5e1bacf3a78b268a348e27fa3c998517c75 |
| Intent Git blob | 0a51838a49fbaa7b86655c7eeba921e002b9265a |
| Intent SHA256 | c5eef438c004bafbc666b1edb4eb55a10de03b2973ecf844d28f3d9443a9c3f5 |
| Runtime ID | rg5-92ce2e87026743df6ed7cd3083940f596b3a562e1c608cc985df3dc7c15cabe3 |

Root and independent reviewer validated the candidate in clean checkouts, exit0. Root fetched B and verified its parent is exactly A, its only A-to-B change is release/release-intent-v5.json, and bytes match the accepted artifact. Independent B load_release_intent also passed. No governed source changed after A; do not squash or rebase.

## Acceptance evidence

All 19 exact-A workflows succeeded. Main Mongo workflow https://github.com/AMASI-SA/AMASI-SA/actions/runs/43658044976 executed 809 approval/recovery cases, 329 Android-operation cases, 122 source-snapshot cases and one acceptance guard: 1261 cases, zero failures/errors/skips. Root directly parsed all four JUnit files. The seven new real-Mongo currency cases and corrected legacy retry all passed.

A JUnit artifact14139256254 has SHA256 7a1e2c1aa25cbd5748d0657dc3392d53a5879d7395f6920198d878c20ce046df.

Readiness A https://github.com/AMASI-SA/AMASI-SA/actions/runs/43658045024 produced artifact14138683871, ZIP SHA256 c6b0bf7657a037dfe5106bb3ccd808137dd9efb56df6a11030d1cd5fa7f51fc2. The candidate covers1218 backend runtime files,291 release controls and1182 frontend source files. Frontend artifact tree58479fd2f6f25764d8d68eb48cc43b53eb731863af7fb2b2448afd9ef0ea0d51; build-meta SHA256559938ac85a385e0d05f73e7e8e4f8fbef5ca03e9b6c2f83b8144999853a9f8a; proof72545e1217499bdf0712a634ac3582b7d7f7f47d0852b5d91b4db12270ee0a3b.

B CI is accepted. A fresh direct Actions GET (head_sha=B, event=pull_request, per_page=100) returned total_count20 and20 rows:19 successful workflows, zero pending/failure/cancellation, and the existing other-branch TikTok workflow skip. PR1933 is ready for review, not merged. No CI reruns or threshold changes were used.

Review Completion43658937865 / artifact14139631444 passed all1261 cases without skips/errors/failures. Root and independent reviewer directly parsed all four JUnit files;7 Mongo currency cases and the corrected legacy retry passed. ZIP SHA256 a786a2e9c59e40752f72b78916fd51e4a0a7cccaf0d856567a0c3d9ba124f58e.

Stock43658937944 passed4513 cases. Balance43658937910 passed4799 backend,783 frontend,4 worker,14 browser acceptance and16 shipping-summary tests. Their retained JUnit/JSON/ZIP provenance was inspected. Security and CodeQL succeeded.

Readiness43658937913 and both Host20/24 clean-clone adapter/package/JSON-route rehearsals passed. Independent official validators passed in reconstructed isolated packages; root independently checked artifact digests and all approved frontend bytes. All293 generated files match across hosts. Backend package1219 files/tree112535d55f755c11e685ec738593cd4ce910f6e29e498e90161f4f84aadbc5bd; frontend package1472 files/treee0c2f01bb849b79bc5acaf2f39149dc21fd626fa2057cd21fd6017e053cd07b1.

Artifact IDs: outputs20=14138638772, outputs24=14139695462, evidence20=14138144703, evidence24=14138409111. Exact ZIP hashes and detailed acceptance are retained in PR1933. These are CI proofs, not evidence of an Emergent production deployment.

The original51ee source failed one obsolete canonical-status fixture. Its failed evidence and narrow RED/GREEN correction remain in PR1933 and the source status history; the old51ee intent is superseded and must not be used.

## Separate production blocker

Read-only /app guard status rechecked after all B gates at 2026-11-15T00:59:10.449504+00:00 remains active:

- Actor: codex-campaign-operating-period-1932.
- Prepared: 2026-11-14T22:10:56.977380+00:00.
- Deployment: 7ef45017e5221635ce3c80c3788f73d9c50b1e08.
- Source: af36682ec95e578764242001dccd16ef9e61c07b.
- Release: rg5-3c726702879630f436b0b615a4e2d1dd85ff5889c45b43b1be477f84cb3043fe.

Read-only health returned200/ok with all verified identity flags true, but served earlier source fcb827f556c40bebd843d8c9d85fedc0b60cf8fa and release rg5-4a98965dc9ad5922b4dbd25e5c960360af8b09f8e5590cced0b87c8c8a0f07a2. Canonical build-meta GET returned403, so frontend identity was not verified. No deployment conclusion is inferred merely from the UI's older “Deployment finished” entries.

AGENTS.md prohibits changing /app with an active lease or clearing another conversation's lease. verify is a mutation because it closes the matching lease on success; do not call it for PR1932. Moving production HEAD would also invalidate that owner's prepublish condition if not yet complete. Its owner must complete or cancel its handoff; elapsed time is not stale-owner evidence.

## Independent mobile message release

- Mobile PR: https://github.com/AMASI-SA/amasi-mobile/pull/357.
- Published base81: ef3984ee423d2e2645155fc33d154c6376d83a76; PR356 excluded.
- Accepted source: 2d6b97656c84d9cc45a6adb2e907db6a57d028c7.
- Normal merge: de2103e64ecf50d3397fd48ad09de6e11be633b1.
- Merge/candidate tree equal:109a633c42a10f80414b16b873e052b257b2c377.
- Seven exact-source workflows passed; two unchanged old-branch-only jobs skipped. Actual19/19 card tests, TypeScript, session, Android export and native printing passed.
- One EAS build submitted: https://expo.dev/accounts/amasi-sa/projects/amasi/builds/5179cefb-8ef2-485e-beeb-f3ea6da25842 .
- EAS authenticated details show full merged commit, Android internal distribution, version1.0.67/code82, profile mezan-web-assembly-pilot and production environment. Source directory frontend; auto-submit off. The one build SUCCEEDED (20m21s build /20m28s total) and its actual downloaded signed APK is accepted.
- Verified EAS download: https://expo.dev/artifacts/eas/bRTiM-56zxPkYsD1w9k1-onnT1E-JhWHbF8w6zH1GNE.apk . Artifact size115548426 bytes, SHA25687be820b972841c585d237b836eab380fa50aec69cc640422ad0da0e4813dc81.
- Root APK verifier exited0:21 PASS,0 FAIL,0 PARTIAL. Its four SDK commands exited0. Package com.amasi.sa.mobile, version1.0.67/code82, native runtime1.0.67, OTA disabled/NEVER/0, non-debuggable/testOnly false. Valid existing signing certificate SHA256 ca6b376f14e01eadf611b0ea0208eab7c75576ac5ee51606d01df571f0d1ac54.
- Independent reviewer checked the actual artifact hash/size, unchanged before/after hashes, report checks and command exits, embedded app config, verifier hash and GitHub merge/tree provenance without rerunning the verifier. Sanitized report SHA2561765367006661fcadbd45ba18ce874404a5503142dc188e5d4d7f261a79ddadc. No APK binary is committed.
- No duplicate build, OTA update or store submission occurred. Physical-device install/UAT and push delivery were NOT_RUN. The APK message alone does not resolve a pending backend source snapshot.

Mobile evidence checkpoint: [final mobile STATUS](https://github.com/AMASI-SA/amasi-mobile/blob/536cab6a216e3e8bc1983f391c89675877414587/docs/operations/ANDROID82-SOURCE-RECONCILIATION-20261115/STATUS.md), H536cab6a216e3e8bc1983f391c89675877414587, treea46c1add5f602575f3bfc444c9f6eb814630dddb, parent the immutable merge de2103e64ecf50d3397fd48ad09de6e11be633b1. It contains only STATUS, sanitized APK report, provenance and the verifier; no release-source or binary changes. Root read the immutable commit and status; the writer verified the four-file-only branch readback.

## Next safe steps

1. All B CI and actual clean-clone host artifact reviews are complete. Preserve frozen B and its accepted source; no optional retesting is needed absent a new change.
2. Android82 is complete and accepted as the signed internal APK. Preserve its single-build provenance; no further build or optional verification is needed.
3. Recheck owner completion/cancellation and guard inactive before any main merge or /app change; read production ref again. If J moves, derive a new source/intent pair rather than reusing stale B.
4. Once allowed, normal-merge PR1933 with expected B head and verify M tree equals B. Pass required push gates, then preserve unrelated /app files and perform the governed adapter rehearsal. Only then prepare this task's own lease, prepublish, publish once and verify three probes plus inactive status.
5. After verified main deployment, read the affected order's fresh owner/revision inside the server, run the existing scoped revalidation dry run, apply only a fully proven eligible result with the same revision fence, and verify received pieces eligible while an unreceived piece remains blocked. No manual Ready or receipt mutation.

The implementation policy and local regressions remain documented in source A's docs/operations/OPTION-PRICE-CURRENCY-PROOF-20261115/STATUS.md. Issue1006 is at its2500-comment capacity; PR1933 and this evidence branch are the active continuation record. Never store customer order data or secrets in these records.
