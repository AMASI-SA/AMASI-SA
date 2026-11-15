# Option-price currency recovery: release handoff

## Current checkpoint

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

B CI is currently running. Readiness43658937913 includes actual Host20/24 clean-clone adapter/package/JSON-route rehearsals, currently in progress. Review Completion43658937865, Stock43658937944 and Balance43658937910 are also pending. Do not infer B acceptance from A success. TikTok Creator Release Candidate is an existing different-branch skip, not a skipped test or waived gate.

The original51ee source failed one obsolete canonical-status fixture. Its failed evidence and narrow RED/GREEN correction remain in PR1933 and the source status history; the old51ee intent is superseded and must not be used.

## Separate production blocker

Read-only /app guard status at approximately00:31UTC remains active:

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
- EAS authenticated details show full merged commit, Android internal distribution, version1.0.67/code82, profile mezan-web-assembly-pilot and production environment. Source directory frontend; auto-submit off. Build is in progress, not yet accepted as a signed APK.
- Do not submit a duplicate build. After success, inspect actual APK signature/package/version/OTA and bind its download to that EAS provenance. Device UAT has not run.

## Next safe steps

1. Complete and independently review all B CI plus both actual clean-clone host artifacts. Do not change thresholds or waive failures.
2. Finish the single existing Android82 build, verify the downloaded APK and record its hash/signer and final result in mobile PR357.
3. Recheck owner completion/cancellation and guard inactive before any main merge or /app change; read production ref again. If J moves, derive a new source/intent pair rather than reusing stale B.
4. Once allowed, normal-merge PR1933 with expected B head and verify M tree equals B. Pass required push gates, then preserve unrelated /app files and perform the governed adapter rehearsal. Only then prepare this task's own lease, prepublish, publish once and verify three probes plus inactive status.
5. After verified main deployment, read the affected order's fresh owner/revision inside the server, run the existing scoped revalidation dry run, apply only a fully proven eligible result with the same revision fence, and verify received pieces eligible while an unreceived piece remains blocked. No manual Ready or receipt mutation.

The implementation policy and local regressions remain documented in source A's docs/operations/OPTION-PRICE-CURRENCY-PROOF-20261115/STATUS.md. Issue1006 is at its2500-comment capacity; PR1933 and this evidence branch are the active continuation record. Never store customer order data or secrets in these records.
