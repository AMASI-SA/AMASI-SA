# Operational Balance — isolated readiness checkpoint, 2026-10-09

Result: the implemented operational scope is a functional isolated candidate. This is not Production readiness, a deployment authorization, or a claim that every screen was retested on the latest APK. Scroll preservation, final slow-request error completion and recovery passed on the isolated emulator.

## Reviewed identities and CI

- Backend/Web Draft PR [1271](https://github.com/AMASI-SA/AMASI-SA/pull/1271): `5ec37f567ab5655abfcf612288967a04e90f0644`; tree `a5771313695b9959c5838267353fdfe3bd6c058b`.
- Dedicated operational runs [37938194923](https://github.com/AMASI-SA/AMASI-SA/actions/runs/37938194923) and [37938189258](https://github.com/AMASI-SA/AMASI-SA/actions/runs/37938189258): Backend 310 PASS; Frontend 102 PASS / 8 suites. Six general checks also completed PASS. These results are tied to the backend identity above.
- Native Draft PR [257](https://github.com/AMASI-SA/amasi-mobile/pull/257): verified checkpoint `b767f7d6dcf89276c80641d1437829a6aa9341bf`; tree `6827c47599ebbde4f9caed71916d1ab91103d990`; [CI 37939887277](https://github.com/AMASI-SA/amasi-mobile/actions/runs/37939887277) succeeded.
- Final native product candidate: `d95e08761c58d53505cc654b502b64c349b1c35f`; tree `eb7cb2e59c42cef330da953e3b1bb098582263d8`. [CI37943246673](https://github.com/AMASI-SA/amasi-mobile/actions/runs/37943246673) PASS. Exact-source build/install PASS; APK `Mezan-Operational-d95e087-x86_64.apk`, SHA256 `82ccb05a6fd1278e768e8553d01aea67241d0cd2f4c83baabce934113cbab38e`.
- Current native full typecheck/verifier chain passed, including permissions29, inventory31, cases21, supplier15 and standalone14. Slow API returned503 after8seconds on each of four attempts; the final APK displayed its error after36seconds, crossing the30-second background poll without being superseded. Clearing the fault restored liquidity1274.00. Evidence: `acceptance-slow-error.png/xml`, `acceptance-recovered.png/xml` and fixture trace14:21:50–14:22:26UTC.
- Freshly observed Production Git HEAD: `128bfc1c2b4670d853a8bce33f15a667d2fdb120` (external #1299 Salla-intent merge). This task did not make that change. Git state alone is not evidence of a runtime deployment. Current integration planning must use this fresh base, not historical `a7977f4`.

The dedicated workflow now includes all operational Web tests and the navigation shell, runs on the companion task branch/base, and rejects failures, errors, missing minimum collection and skipped tests. Its initial backend failure was missing isolated test dependencies imported by existing authorization/route helpers. The workflow installs those helpers' dependencies without changing requirements, product code, or test assertions. Fresh minimal-environment execution passed 310 tests and `pip check` before the successful remote runs.

## Actual isolated acceptance evidence

Environment: dedicated emulator `emulator-5560`, Android 15 / API 35 / x86_64; standalone package `com.amasi.sa.mezanoperationalpreview`; version 1.0.11 / code 14. Local API `http://127.0.0.1:8135/api` through ADB reverse, synthetic database `operational_balance_device_uat_20261005`. No Production accounts or financial data were used. The other emulator was left untouched.

| Check | Observed result | Evidence in local evidence directory |
| --- | --- | --- |
| Backend authorization combinations and direct API restrictions | 39 PASS; independent write/read grants and denials | `readiness-api-permissions.json` |
| Native owner, both grants, write-only, read-only, no-grant login/navigation | Functions visible only under the applicable grants; forbidden deep links rejected | `readiness-owner-success`, `readiness-both-final`, `readiness-write-only`, `readiness-write-denied`, `readiness-read-only`, `readiness-read-denied`, `readiness-none-result`, `readiness-none-deeplink` PNG/XML |
| Incoming settlement 1 without receipt | Saved using assigned test bank | `readiness-settlement-saved.png` |
| Outgoing expense 1, lost acknowledgement, restart and retry | Fixture committed then returned 503; original command restored; retry produced no second financial effect | `readiness-expense-lost-ack`, `readiness-pending-restart`, `readiness-retry-success` PNG/XML |
| Readback before/after retry | Entire report JSON equal; no duplicate movement | `readiness-before-retry.json`, `readiness-after-retry.json` |
| Order bank credit through operational worker | Reviewed credited 9 once; in-progress plus concurrent refresh/ticks retained one movement and bank 864, cash 410 | `readiness-worker.json`, `readiness-foreground-bank.png` |
| Missing fee contract | Incomplete-setting warning shown; restored policy removed that warning | `readiness-warning-final.png`, `readiness-restored-policy.png` |
| Report API 503 | Error hides unavailable data; retry restores report | `readiness-report-error.png`, `readiness-report-retry.png` |
| Report list with 5,000 synthetic rows | Virtualized rendering exercised; leaving report screen stopped its polling during 55-second observation | `readiness-scale-5000`, `readiness-scale-scrolled`, `readiness-offscreen` PNG/XML; `readiness-offscreen-proof.json` |
| Scroll position across refreshed large report | Visible records 4–9 stayed stable during a 35-second observation across refresh; PASS on the tested scroll-fix artifact | `ready-scroll-records`, `ready-scroll-after` PNG/XML |
| Genuine Web-principal full report and audit | Local `uat-web-login` principal allowed full report/audit; bank 864, cash 410, liquidity 1,274 and the same single order-bank movement 9 | Actual local browser/API readback; no Web product-code change |

Local evidence directory: `D:/codex-evidence/operational-standalone-20261008`. Screenshots are UI evidence, paired with persisted readback and sanitized fixture logs; they do not independently prove Production authentication or financial behavior. The worker evidence deliberately retains incomplete salary-contract warnings for synthetic fixture employees; it does not certify production salary setup.

## Previously verified slices retained

- [Inventory variants](INVENTORY_VARIANTS_20261008.md): same product silver 10 + gold 20, accepted return silver 2 leaves silver 8 / gold 20. Actual Web lost-ack retry and Android purchase/return/restart readback passed. Those screenshots/APK identities remain historical evidence for that slice.
- [Inventory purchases](INVENTORY_PURCHASES_20261007.md): quantity invoices, partial bank/full cash payments and cross-surface readback.
- [Standalone checkpoint](STANDALONE_CHECKPOINT_20261008.md): supplier fixed discount and accepted quantity return, custody paying inventory invoice, custody-to-cash transfer, customer refund and later shipping completion. Pending-operation recovery was subsequently retested successfully; the older [custody RCA](CUSTODY_RESTART_RCA_20261008.md) describes the original defect, not an unresolved duplicate-money defect.
- [Earlier native UAT](ANDROID_UAT_20261007.md): category payments, returns and exchange purchase/contribution evidence on its recorded integrated-app source. These older successes are not relabeled as latest standalone-APK full-screen acceptance.

The current regression suites preserve MZ2 source restrictions, idempotency, no double bank effect, customer-return/exchange and variant contracts. No operational accounting writer, journal or inventory initialization was added.

## Remaining boundaries and acceptance work

1. Final native build, CI and slow-request retest completed. The earlier scroll-restoration offset approach failed actual UAT and was replaced by concealed layout preservation; its successful5000-row35-second refresh evidence is `ready-scroll-records.png/xml` and `ready-scroll-after.png/xml`. The final source adds only background-read gating to that verified layout. All fixture faults were cleared after testing.
2. Latest physical Samsung UAT is not performed. The software-rendered emulator has measurable jank, so no physical-device performance or universal responsiveness PASS is claimed.
3. Existing authentication/MFA behavior is preserved, not replaced by this operational work. Its full real-environment/session-expiry integration, offline/server-restart paths and every per-category edge case on the final standalone artifact are not all newly device-tested here. This is a coverage limit, not a newly established functional defect. Historical local tests and current automated coverage remain separately labeled.
4. The earlier Web-report mobile-principal harness mismatch is resolved by a genuine local browser principal: full report/audit UAT passed without a Web code change. This verifies the local operational path; it is not Production authentication or deployed-runtime acceptance.
5. Temporary per-owner state has a fail-closed 12 MiB capacity limit (`operational_balance_store.py`); no archive procedure or indefinite-volume acceptance is claimed. No data model change is included.
6. PR1263 (`c511722f...`), Release Guard PR1265 (`8be1a261...`) and Security PR1269 (`84d3...`) remain open/unmerged. Formal Security attestation associated with #1269 remains pending according to its status. These external release gates are not cleared by local UAT or dedicated CI.
7. A final coordinated release candidate and separate integration review against fresh Production `128bfc1c...`, fresh governed release checks/intent and separate owner release authorization remain necessary. No Salla, release intent, release identity or lease changes were made here; no Prepare, Prepublish or deployment was performed.

Production unchanged by this task. Production writes = 0. PR1263 is not modified. All implementation changes remain on the dedicated companion task branches; no merge or deployment is authorized by this report.

## Follow-up: current Production comparison and final inventory readback

The user requested emulator-only testing because the physical phone is occupied. No APK was installed or launched on that phone. An already-started isolated ARM64 build completed but is not device-tested or an acceptance artifact.

Read-only Git integration analysis used Production `128bfc1c2b4670d853a8bce33f15a667d2fdb120` and task `30c1df84c51150558d156d503bdd3c8824d94b25`. Their merge-base is `a7977f4cfc1ef0a721fc25d783661332f32fe3b6`; Production has29 unique commits and the task40. `git merge-tree --write-tree --name-only` exited0, with no textual conflict, producing an unreferenced comparison tree `6bf56ad43b5606f32f185f65f07b0665dc6282da`. No merge commit, branch update, checkout or release was performed. Operational backend modules and Web operational pages are byte-identical between task HEAD and that comparison tree. The server diff against Production contains only the operational router, one worker start and one shutdown-list addition. This is conflict analysis, not combined-candidate test or release acceptance; new Production dependency/runtime changes still need coordinated integration testing later.

Fresh emulator readback on the final installed application confirmed invoice `APP-COLORS-1008`: silver10, returned2, remaining8; gold20, returned0, remaining20. Net500, tax75, gross575, outstanding552 after the accepted23 return credit. Aggregate silver18/gold40 includes a separate Web invoice; it is not a duplicate of this invoice. Evidence: `continuation-purchase.png/xml`, `continuation-stock-reports.png/xml`, `continuation-invoice-colors.png/xml` in the isolated evidence directory. No new financial write was required.

Security1269 still has no formal-attestation closure in its PR body/comments. Its source checkpoint remains84d3c938;1265 remains8be1a261 and1263 remainsc511722f. Final documentation-head CI from the preceding checkpoint completed successfully: backend30c1df84 runs37944015332/37943998167 and six general workflows; native2946f71 run37944026903. No product source changed in this follow-up.
