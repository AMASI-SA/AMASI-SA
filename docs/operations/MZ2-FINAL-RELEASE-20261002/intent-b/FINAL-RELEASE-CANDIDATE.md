# MZ2_RELEASE_CANDIDATE_BLOCKED_BY_CODEQL

**The earlier readiness claim is withdrawn pending CodeQL review.** The 39 successful workflows did not include the separate failed GitHub Advanced Security check 110891031629. Exact B has 66 successful / 4 skipped / 1 failed check runs and 4 high-severity scanner alerts. Source/Intent unchanged; no merge/deploy. See [current Gate audit](../software-deploy/CODEQL-BLOCKER.md). The test evidence below remains valid within its stated scope.

Technical software release candidate only. **Full Business UAT remains NOT PASS** and financial go-live is not authorized. No software or Production execution follows this report.

| Identity | Value |
|---|---|
| Final Integration / Intent B | `e030b737ca50adb03f37b06dab2a5624d79474fa` |
| TREE | `db5ea30adaf8aa1f5bbe37288da9366734d60ea1` |
| Source A | `57688c6423848165525bbc85265c4bb56e65da1c` |
| Production/base/rollback | `83363097d48e034dc7140a60c290efc684e1ffde` |
| PR | #1243 OPEN / Draft; combines #1240 accounting + #1241 current-carrier operational source |
| New tracked Intent | `release/release-intent-v5.json`, SHA256 `d95fd06bfc0eb315fef96fc5354903c90119a63f6e842c80695b1dfafbd3adb7` |
| Runtime identity | `rg5-488cc40323938b362a24da2091a9a0c14047d6c56648471d7473a80faf171176` |

A is B's direct parent. Only the tracked Intent changes after A. The exact new candidate artifact11230198747 was validated by source-A Linux build37015231942 attempt2, then B was built/rehearsed afresh. No old Intent was reused. See [invariant](INVARIANT.json), [composition](SOURCE-COMPOSITION.json) and [fresh final GitHub read](FINAL-GITHUB-SNAPSHOT.json).

| Required technical gate | Exact-B result | Evidence |
|---|---|---|
| Complete CI | BLOCKED: 39/39 workflows PASS; separate CodeQL check FAIL; 66 success / 4 skipped / 1 failed checks | [complete matrix](CI-MATRIX.md), [jobs/steps](CI-MATRIX.json) |
| Backend regression | PASS:154 declared native/operational files,2172parent tests+900subtests;0failed/error/skipped | [selection](local-verification/backend-selection.json), [JUnit](local-verification/backend.xml), [log](local-verification/backend.log) |
| Full frontend regression | PASS:244suites/1417tests;0failed/pending | [summary](local-verification/frontend-summary.json) |
| Build | PASS ordinary compile plus governed reproducible build in exact-B CI | [commands](local-verification/commands.json), [Readiness run](https://github.com/AMASI-SA/AMASI-SA/actions/runs/37022936397) |
| Real Mongo / replica-set | PASS: fresh8.0.12 replica27141 plus standalone27142, all selected tests executed, cleanup clean | [replica](local-verification/27141-environment.json), [standalone](local-verification/27142-environment.json), [completion](local-verification/finished.json) |
| Supplier native invoice/payments; Employee/payroll; Shipping/COD/POS/TrackF; Advertising | PASS declared native regression and respective exact-B CI workflows | [matrix](CI-MATRIX.md), [backend results](local-verification/backend.xml) |
| Bank transfer; Customer advances/refunds; Payment-provider settlements; Daily movements | PASS declared exact-B native cases; atomic rollback/idempotency/owner/guard checks retained | [selection](local-verification/backend-selection.json), [backend log](local-verification/backend.log) |
| Security Gate / CodeQL / G47 / Accounting UI and workflows / Store Delivery | Workflows PASS; CodeQL acceptance BLOCKED by separate failed security check | [matrix](CI-MATRIX.md) |
| Host Node20 clean-clone adapter | Actually executed PASS on B; no Git metadata; governed22.23.2/1.22.22 build; isolated package and JSON-route checks | [full log](host-node20-rehearsal.log), [package boundary](rehearsal/mezan-package-boundary-v5.json) |
| MZ2_ONLY_SSOT_AUDIT | PASS within14converted native modules + declared dynamic regression;4guards unchanged | [SSOT](SSOT-SOURCE-CHECK.json) |
| Smoke B | PASS Acceptance-only actual full-app password+MFA/404/423 execution;writes_paused remains true;wholeDB/control equality | [actual execution](local-verification/smoke-b/result.json), [zero-write proof](ZERO-WRITE-PROOF.json) |
| Business UAT preparation |16/16setup PASS;public409 behavior and4synthetic physicalAPI tests PASS | [16-stage matrix](UAT-PREPARATION-MATRIX.md) |

Backend coverage is the declared154-file MZ2 native/operational selection, not all historical repository test files. SSOT is scoped to converted/tested native paths, not a claim that every historical Legacy route was exercised. No test or assertion was deleted, skipped manually, weakened or changed to obtain these results.

One CI preflight attempt failed during temporary Git directory cleanup (OSError39 `.git/objects` not empty). [Original failure](backend-preflight-attempt1.log) is retained. A single targeted job retry on identical B passed156tests; [retry log](backend-preflight-attempt2.log). No product, guard or fixture change was made. Host20 was actually run and retained on B; the former A skip was not counted.

## Financial/business holds remain

Full Business UAT = **NOT PASS**. Physical inventory commercial approval, actual owner opening balances/debts/source approval, Stage15 owner snapshot/version/fingerprint approval, positive public Opening, InventoryInitialization, Activation/P08 and stabilization remain unexecuted. Owner-manual input and commercial cutover2026-10-03T00:00:00+03:00 Asia/Riyadh remain approved input policy only. No unknown amount/cost/location/identity is inferred. Products/variants/existing components remain covered by the existing Owner input pack; components require no invented SKU.

The owner explicitly separated software deployment eligibility from the financial go-live gate. Neither CI, setup16/16 nor Acceptance SmokeB is Production financial acceptance. SmokeB intentionally has no generated governed runtime identity in the isolated test-startup environment; exact runtime source hashes are verified there, while Host20 separately verifies the generated governed release identity. `production_verified=false` remains unchanged. Public409/423 guards remain intact. No positive Opening/Activation capability is asserted from the negative tests.

## Provenance, safety and next boundary

- [Changed-files manifest](CHANGED-FILES.json):796paths vs Production/base, exactly matched [GitHub](github-changed-files.json). This step changes only the Intent in the software branch; review evidence is committed separately.
- [Verified results](VERIFIED-RESULTS.json), [local artifact hashes](LOCAL-VERIFICATION-MANIFEST.json), [Acceptance/rehearsal hashes](ACCEPTANCE-REHEARSAL-MANIFEST.json), [independent review](INDEPENDENT-REVIEW.json).
- Production financial writes by this task = **0**. Real execution used owned loopback fixtures with isolated credentials; the full-app SmokeB denies non-loopback network calls. Databases and controls match before/after the paused request; owned fixtures stopped and removed their test databases. This does not assert unrelated actors' global Production activity.
- Release Guard: unchanged; last owner-provided verified `/app` status was `active:false`. No new shared operational status/prepare/prepublish/lease was executed here. CI guard tests and the B adapter rehearsal passed. A fresh shared `/app`/source/lease check and governed publication verification are still required at the later authorized deployment boundary.
- No prepare, prepublish, release lease, Merge, Deploy, Opening, InventoryInitialization, Activation, P08, Backfill or financial schedule execution. Write-control unchanged.
- Host20 proves the reviewed candidate package, not an actual Emergent publication/snapshot handoff. Later publication must still satisfy the unchanged Release Guard and live verification protocol.
- Stop after this RC handoff. PR1243 remains Draft. Do not infer authorization to perform the financial holds from this report.

Evidence bytes are preserved by scoped -text attributes in the evidence branch. This avoids Git newline normalization invalidating the raw execution hashes; no application source or assertion is affected. Final index/blob verification is recorded in EVIDENCE-GIT-BYTES.json.
