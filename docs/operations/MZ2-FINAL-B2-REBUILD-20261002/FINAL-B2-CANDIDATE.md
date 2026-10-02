# MZ2_FINAL_B2_CANDIDATE_READY

Technical software candidate only. Full Business UAT remains NOT PASS; no release or financial operation is authorized by this evidence.

## Immutable source
- New Draft PR: [#1245](https://github.com/AMASI-SA/AMASI-SA/pull/1245).
- Final HEAD / Intent B2: `d6c0553ae6a99a85d52b03871b7bf64024180514`.
- TREE: `806e935c8ecc3268f98096cd2b477050d235c99e`.
- Source A2: `79f307f6ff69ba658958ebcdce03341b39a55939`.
- Production base / rollback: `83363097d48e034dc7140a60c290efc684e1ffde`, freshly confirmed unchanged.
- Runtime Intent identity: `rg5-8f098823ae66cfa6c93a7d8effc3a87b0515796f8ef49eba4e4bb94e53696ff6`.
- Included source: #1240 MZ2 Accounting, #1241 current Salla carrier fix, all #1243 source fixes, and #1244 bounded webhook log privacy fix.
- Historical PRs #1243/#1244 remain OPEN/Draft and their HEADs remain e030b737…/551bd3d5…. No history rewrite, squash, rebase, force push or Production merge.

A2 directly descends from accepted CodeQL source3cb4ecc8; its sole addition is the source-composition record. That CodeQL source preserves all previous sourceA57688c64 except the two reviewed logging/test files. The former divergence was an Intent-only sibling, not missing accounting/carrier source. Including either old frozen Intent in J..A would violate the v5 history guard, so the new branch preserves the approved source ancestry and documents the historical Intent references.

**A2→B2 invariant PASS:** A2 is the direct parent, B2 changes only release/release-intent-v5.json, no J..A2 commit changes Intent, and all1,880 generated source-manifest records match Git modes/blobs/bytes/SHA256. The newly generated artifact11242545755 came from source-build37039757710; its archive SHA256 is c0f9fddbef29e1bfa1ea09360f4f232e001f44b4309e55caa786dab6a3869dc7. No old Intent reused.

## Fresh gates on exact B2
| Gate | Executed evidence / verdict |
|---|---|
| Complete CI | **41/41 workflows SUCCESS**, 69 successful checks including independent CodeQL, 4 conditional skips; no failures/pending |
| CodeQL | PASS; Python analysis1882507092, zero results in selected webhook file; separate security check110950062187 PASS/zero annotations |
| Security Gate | PASS |
| G47 | PASS in fresh CI and local real-Mongo regression |
| Backend | **2178 PASS + 900 subtests**, 0 failures/errors/skips; complete declared154-file native/operational Release Gate, exact selection and executed-file match |
| Frontend | **244 suites /1417 PASS**; all244 tracked test files executed, no failures/skips/todo/filter |
| Build | Ordinary local Vite build PASS; governed CI build/reproducibility and adapter PASS |
| Real Mongo | Fresh owned replica set plus standalone fixture; native transactional/rollback/idempotency/owner-boundary tests PASS |
| Host Node20 | Executed PASS on B2, job110950767389/run37040597248; isolated package boundaries and build-meta JSON route verified |
| Track F / Shipping / COD / POS | PASS; existing semantics, proof requirements, C3 history and guards preserved |
| Supplier / employee-payroll / advertising / bank transfers / advances-refunds / providers / daily movements | PASS within the declared native regression and fresh domain workflows |
| MZ2_ONLY_SSOT_AUDIT | PASS within14 converted native modules plus declared dynamic regression; no whole-historical-application claim |
| Smoke B | **PASS — Acceptance only**, newly executed on exact B2; never Production proof |
| Full Business UAT | **NOT PASS**; owner inventory/balances approval, Opening, inventory initialization, Activation/P08 remain separate financial go-live gates |

See CI-MATRIX.md/JSON, CHECKS.json, VERIFIED-RESULTS.json, SSOT-SOURCE-CHECK.json and LOCAL-VERIFICATION-MANIFEST.json. The four CI skips are unaffected Warehouse frontend, unaffected Snapchat Settings backend/frontend, and manual redeploy notice on a PR. Host Node20 was not skipped or inherited from A2.

GitHub CodeQL analyzed test-merge dc985be9fcad9a900a7a60a38874b153ac303899 whose TREE equals exact B2. Original alerts78–81 are absent on PR1245; no suppression, configuration edit, annotation or dismissal.

## Actual Smoke B execution
Environment identifier: `mz2_smoke_b_acceptance_d9f50d966a614c60a4335f9f20d61bf2`.
Backend: http://127.0.0.1:18775. Mongo: loopback27141, replicaSet=mz2release. Fresh synthetic owner, isolated credentials, dotenv disabled, non-loopback socket connections denied.

Executed full server.app, password login202 then real MFA verification200, canonical paused-control GET200, nonexistent-session GET404, and opening-draft POST423 with code `mz2_writes_paused`. No Opening was executed. Whole database documents/indexes/options and canonical control match before/after; paused=true/revision1 unchanged. Runtime source manifest matches exact B2 before/after; no denied external connections. App process stopped, disposable SmokeB database absent, both owned Mongo fixtures stopped with only admin/config/local databases remaining.

Runtime identity remains unverified in this supported test environment and `production_verified=false`. Source attestation comes from the runtime/parent Git+file manifests; governed runtime identity is separately tested by Host20. This Acceptance contract does not assert a deployed Production identity.

Execution: existing run_technical_verification.py with --root pointing to the exact B2 checkout, --expected-head d6c0553a…, --output D:/CodexAcceptance/mz2-final-b2-d6c0553a-20261002 and existing Node22.23.2/Mongo8.0.12. All four stages exit0: frontend96.413s, ordinary-build8.143s, backend1108.753s, SmokeB16.263s. Exact commands, executor provenance, logs, XML, hashes and cleanup are retained. CPJ rejected unsupported win32 before launching anything; the ordinary foreground runner was launched once. No test retry.

## Two pre-existing failures — explicitly not called PASS
Both unchanged tests in test_salla_webhook_attribution_ledger_bridge.py fail on untouched originalB and the CodeQL source:
- test_verified_order_webhook_refreshes_attribution_ledger replaces fulfillment_v2_routes with a module stub missing existing persist_component_source_snapshot.
- test_ledger_failure_never_blocks_salla_order_ingestion supplies object() instead of a Mongo database supporting command/transactions.

They fail before the attribution logic, are absent from the established154-file Release Gate and its CI selectors, and do not invoke the changed logger. Relevant source/test Git blobs are identical. Runtime passes the real Motor database, not those fixtures. Parent-owned fresh probes against oldB and exact finalB2 proved normal attribution and throwing-attribution behavior: canonical order retained, bridge called once, ingestion succeeds; a bridge exception becomes ledger_bridge_unavailable. These probes use a mocked transaction capability and are not Mongo atomicity evidence; fresh G47 real-Mongo tests cover the native persistence boundary.

No test, assertion, fixture or gate selection was removed/changed to obtain green. They remain known failures outside this declared gate; unrestricted repository-wide tests are not claimed PASS. See fixture-audit/README.md, original failure XML, exact-final-probe.log.gz and file manifest.

## Source manifest and zero-write boundary
CHANGED-FILES-VS-PRODUCTION.txt contains798 paths. Compared with the old frozen B1, exactly4 paths change:
1. backend/salla_integration/webhook_event_capture.py
2. backend/tests/test_salla_current_shipping.py
3. docs/operations/MZ2-FINAL-B2-REBUILD-20261002/SOURCE-ASSEMBLY.json
4. release/release-intent-v5.json

All other original-B tracked entries preserve their bytes/modes. No accounting writer, ledger/journal, economic semantics, default account, Legacy fallback, C3, Track F,409/423 guard or write-control changes.

Production financial writes by this task = **0**. Tests used owned loopback fixtures and isolated CI databases, with no Production credentials or endpoint used for financial testing. Production Git ref is unchanged. This is evidence of this task's zero Production writes, not an assertion about unrelated actors.

Prepare/prepublish/lease/merge/deploy/Opening/inventory initialization/Activation/P08/backfill/financial schedules = **NOT EXECUTED**. Write-control = **UNCHANGED**. Full Business UAT = **NOT PASS**. Stop after this candidate handoff; no automatic release step.
