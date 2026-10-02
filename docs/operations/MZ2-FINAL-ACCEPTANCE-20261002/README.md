# MZ2 final acceptance — BLOCKED

Frozen candidate, unchanged throughout this acceptance:

- Production/base/rollback: `83363097d48e034dc7140a60c290efc684e1ffde`
- Integration HEAD B: `88cc9131027fd6783a46e2e00fc6aaec4788b8fa`
- TREE: `57483f44efa381ca872262f5a8bc61d2a87cae93`
- Source A: `385867187bd6d6fcbb7048e8857816b973471440`
- PR #1240: OPEN / Draft. No merge/deploy/opening/activation/lease/publish.
- No candidate application/test/assertion/config/guard changes.

This separate evidence branch must not replace or be merged into the frozen
A/B candidate. Its commits preserve verification records only.

## Final22-gate matrix

| # | Gate | Result and reviewable evidence |
|---|---|---|
|1|Full Business UAT|**NOT PASS**. No authoritative combined final business scenario/source/acceptor; see ACCEPTANCE-GAPS.md and UAT-16-STAGE-MATRIX.md. Historical16/16 is setup only.|
|2|Physical-stock approval|**NOT EXECUTED**. No supplied actual count/cost/location source/approver. Synthetic G47 contract tests are separate.|
|3|Opening acceptance|**Positive public-app acceptance NOT EXECUTED**. Public409quarantine; internal engine regression and denial tests PASS only.|
|4|Activation acceptance|**Positive public-app acceptance NOT EXECUTED**. Public v2_active409lock remains; no guard removed.|
|5|Smoke B|**PASS_ACCEPTANCE_ONLY** on exact88cc. Actual full app/MFA, paused control,404/423, complete unchanged database/index/options fingerprints, source/runtime manifests, cleanup. production_verified=false.|
|6|Backend regression|**PASS declared147-file native integration baseline**:2117PASS+900subtests,0errors/failures/skips; raw full rerun under evidence/retry-D/backend-88cc-retry. This is not all990historical backend files.|
|7|Full frontend regression|**PASS**:242suites/1391tests,0failed/pending; raw exact-head JSON/log/source manifests retained.|
|8|Build|**PASS** ordinary exact-head compile; exact-B governed build/Host20clean-clone rehearsal PASS in CI. Local ordinary output is not a governed deployment artifact.|
|9|Real Mongo / replica-set|**PASS technical contracts**: owned Mongo8.0.12 replica and standalone; UUID scopes, transactions/replay/rollback/isolation, cleanup. Failed first attempt retained.|
|10|Supplier native invoice/payment|**PASS technical regression**:66parent cases/4files in domain matrix.|
|11|Employee/payroll|**PASS technical regression**:84parent cases/4files.|
|12|Shipping/COD/POS|**PASS technical regression**:495parent cases/21files; manual POS and separate bank settlement, C3, late evidence, native history and rich contracts.|
|13|Advertising|**PASS technical regression**:178parent cases/8files.|
|14|Bank transfer|**PASS technical regression**:46parent cases/4files.|
|15|Customer advances/refunds|**PASS technical regression**:28parent cases/4files.|
|16|Payment providers|**PASS technical regression**:57parent cases/11files.|
|17|Daily movements|**PASS technical regression**:19parent cases/2files.|
|18|Security Gate|**PASS exact-B existing CI**, read back. Dependency/security/auth/CSP steps executed; see CI-MATRIX.json.|
|19|CodeQL|**PASS exact-B existing CI**, Python and JavaScript/TypeScript analysis steps executed.|
|20|G47|**PASS technical regression**:101parent cases/7files plus exact-B CI. Not actual inventory approval.|
|21|MZ2_ONLY_SSOT_AUDIT|**PASS within converted/inspected native paths**; see scoped audit and runtime/static evidence. No whole-historic-app claim.|
|22|Release Readiness|**NO**. CI workflow success does not close gates1–4 or attest Production. Stage16/live flags remain locked/false.|

Counts overlap across domains and must not be summed. Per-case source/name/result
mapping is in `evidence/retry-D/backend-88cc-retry/domain-case-matrix.json`.
JUnit counts3017 including900subtests, while individual parent testcase records
number2117. No assertion or test was dropped.

## Preserved failure and recovery

Attempt1 on C is **NOT_PASS**:2115PASS+900subtests,2setup errors (confirmed Mongo
OutOfDiskSpace and operation cancelled). Its unusually long setup stalls and
raw logs/XML remain in evidence/backend-88cc9131. No unsupported root-cause
assumption erases either error. The owned fixture was verified and stopped;
its data are retained offline.

A fresh replica/data/log/TEMP/output on roomy D used a512MiB test oplog and the
unchanged5s transaction lifetime. Under these conditions, the original affected
file passed15/15, then the unchanged entire147-file baseline
passed2117+900 in1017.06s. All test databases were absent before shutdown; both
owned replica and standalone listeners are gone. Source stayed exact88cc.

## Evidence index and limits

- [Raw artifact SHA256 manifest](EVIDENCE-MANIFEST.json)
- [Smoke execution and independent verification](evidence/SMOKE-B-VERIFIED.json)
- [Successful complete baseline](evidence/retry-D/backend-88cc-retry/verified-summary.json)
- [Full frontend/compile](evidence/frontend-88cc9131/verified-summary.json)
- [CI matrix](evidence/CI-MATRIX.json):39/39workflows success,66jobs success,
  4existing conditional skips. Exact-head run metadata retained separately.
  This turn read back the existing fresh88cc runs; it did not claim a new rerun.
  The requested final new CI run remains after all acceptance prerequisites.
- [Scoped SSOT audit](MZ2_ONLY_SSOT_AUDIT.md)
- [Business/public-route gaps](ACCEPTANCE-GAPS.md)
- [16-stage matrix](UAT-16-STAGE-MATRIX.md)
- [Independent review](INDEPENDENT-REVIEW.md)
- [Candidate changed-files manifest](evidence/CHANGED-FILES.json):791 exact paths
  relative to Production83363097; no final-acceptance source delta.
- [Frozen A/B and rollback reference](evidence/FROZEN-SOURCE-INTENT.json)
- [Zero-write boundaries](ZERO-WRITE-PROOF.md)
- [Replay commands](REPLAY.md)

Next action requires authoritative business/UAT/inventory inputs and an explicit
decision for the positive public opening/activation boundary. Do not expose
private test engines, alter409/423/503/SSOT guards, reinterpret synthetic counts,
or turn Acceptance Smoke into Production proof.

Production financial writes by this task=0. Write-control UNCHANGED.
Merge/Deploy/Production Opening/Activation/financial schedules/lease/publish=NO.
Release Readiness=NO. No Release Candidate Ready claim.
