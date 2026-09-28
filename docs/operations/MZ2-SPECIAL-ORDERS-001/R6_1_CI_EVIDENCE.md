# R6.1 direct committed-source verification and outstanding PR contract mismatch

Task MZ2-SPECIAL-ORDERS-001,2026-09-28. IN_PROGRESS_NOT_READY_TO_DEPLOY.
This report distinguishes verified task source from an unaccepted newer-Production
integration. A documentation commit above the tested source is not a new runtime test.

## Direct source, no reconstruction

Source/checkout:4e0ea1fdfe1655f691ee0b2cddc7d0825c0bf398.
Tree:eb794b5417ec214007451edf9e1aa4aa54f17d7f.
Push run36460707191,job109058216588: SUCCESS.
Artifact10988146140,11229bytes, downloaded and independently hashed.
ZIP SHA256:b5e435d1c96a03dbbcb779bd456a41e3fa53bc5b52284a77e58e180580105e96.

Inspected artifact:
- r6-baseline.xml:117 tests,0failures/errors/skips.
  SHA256:7c0357ff8ac007afae03cbd43a96db250deb752a85841f728cc1da94e89b75ac.
- r6-candidate.xml:311 tests,0failures/errors/skips, same117 baseline+194special.
  SHA256:608456a4b6ef294a0bdea9326fe52be1164a02e7e674c3893e5a52d654a7e9af.
- r6-frontend-tests.json:14 tests,14pass,0failed/pending, all three label files.
  SHA256:eef48cf830d0cd3c94cb4588bb18279c12a9175cad6ff76279999366d701bf39.
- r6-tested-identity.json binds exact checkout/tree above,48verified paths,
  historical_archives_unchanged=true,applied_patch=false,deploys=false.
  SHA256:63ac711fa71b9e6b9f50ce185d3fdceaadb78fa0ec83ae1b9eabc58fb797a477.
- Offline checker suite:55unit tests, successful separate CI step, not a live drill.
- Push continuity36460707053 and core36460707132 also succeeded.

R6.1 adds four real-Mongo cases proving DeleteOne/DeleteMany/ReplaceOne/UpdateOne
cannot be smuggled through protected bulk_write. All four failed against oldR6,
then passed after refusal. No tests weakened. Ordinary native bulk behavior remains.
Original Production741.40 label test remains byte-identical; two R5 additions are
in their own test file and execute alongside the private-label tests.

## PR merge is a different tree: blocked source acceptance

Actual PR checkout:a36370bddf1d93a2ba606011c2c29bec41debc2c,
merging source4e0ea with Production Git a7bcb1626e2ad4defba2260d4a113417f91f3120.
R6 control run36460714799/job109058242487 fails at current source verification
before its backend/frontend tests. Continuity36460714837 also fails. Do not
present either as PASS or transfer the direct-source311/14 results to this merge.

GitHub comparison4e0ea...a36370b shows newer Production supplier receiving changes
391added/12removed. This path is in the48-path source contract:
backend/supplier_receiving_routes.py
PR-merge blob:025b13a35196d7af2dedc9080c647d08060228e1.
Task source blob:e8193ca03be83d695ab6cdc0180960a89f821138.
Other comparison changes include native employee monitoring, reviewed preparation,
order mapper/shipping label service and their tests. No Production source or intent
was copied away to make the guard green. Review and reconcile native supplier
semantics, then create an explicit new cumulative layer and rerun latest baseline
and merge tests. Do not edit the immutable R5/R6/hardening fingerprints in place.

Other PR workflows observed successful include Core36460714749,
CodeQL36460714851,Security36460714718,Readiness36460714669 and native delivery,
fulfillment,products,preparation and supplier policies. Those workflow conclusions
are not proof of no security findings or execution of every conditional release
step, and do not overrule the blocked source-contract tests.

## Limits and safe continuation

Synthetic dedicated Mongo replica set; no merchant data. Control protects only
instrumented local DB transactions, not arbitrary raw/legacy workers or unknown
external effects. Static activation and server registration remain OFF. No actual
recovery target, backup restore, operational rollback or Android client acceptance.
Sidecar and actual reporting remain incomplete. No Merge/Deploy/Preview/Production,
real financial/provider writes, APK/OTA, operational lease/intent or DB restore.
