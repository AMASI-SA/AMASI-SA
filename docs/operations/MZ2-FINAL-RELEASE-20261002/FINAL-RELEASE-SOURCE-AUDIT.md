# FINAL_RELEASE_SOURCE_AUDIT

Source audit PASS for composition. The source is **not yet a deployable reviewed
v5 A/B pair**. No prepare, prepublish, lease, merge or deploy was performed.

## Exact GitHub identities

- Production/base/rollback reference J: `83363097d48e034dc7140a60c290efc684e1ffde`.
- Integration/source A: `57688c6423848165525bbc85265c4bb56e65da1c`.
- Integration TREE: `9f5a6f76a827e91602eee994343e5bb68d29f131`.
- Production TREE: `1053db45d38aac9092a2cf55989cb6349fceffcd`.
- Combined review: [PR1243](https://github.com/AMASI-SA/AMASI-SA/pull/1243), OPEN/Draft.
- Source is five commits ahead of J and zero behind. Clean source checkout.
- These are GitHub source identities, not a new live deployment claim.

## Included sources, verified against current PR heads

| Source | Inclusion proof |
|---|---|
| MZ2 final Accounting / PR1240 | Source385867187bd6d6fcbb7048e8857816b973471440 is an ancestor of A. All Backend blobs equal PR1240 HEAD88cc9131027fd6783a46e2e00fc6aaec4788b8fa. Relative to that HEAD, only two operational Frontend files, two associated tests, one operational status document and the deliberately un-reused old intent differ. |
| PR1241 current-carrier fix | Commitd5db39003d6590feb89821e7246c1b0b11083a66 transferred by cherry-pickdac573ddd1a17a0943ec81d925b3a4a5bfda321f.16/18 files have identical blobs; repository.py additionally preserves PR1240 financial_delivery_snapshot/pin_financial_delivery_snapshot, and the printer test restores its mock implementation after CRA resetMocks. No assertions removed. |
| Original PRs | PR1240 and PR1241 remain OPEN/Draft; inclusion means source content is present, not that either PR was merged. No force push/rebase performed. |

Relative to PR1240 source A, the complete delta is:

- docs/operations/SALLA-CURRENT-CARRIER-20261001/STATUS.md
- frontend/src/hooks/useOrders.js
- frontend/src/hooks/useOrders.shippingRefresh.test.jsx
- frontend/src/pages/OrderDetailsV2.jsx
- frontend/src/pages/OrderDetailsV2.shippingIdentity.test.jsx

Current GitHub PR files match the local Git diff and CHANGED-FILES.json exactly:
795 total: backend200, frontend98, scripts62, workflows10, docs425.
See `audit-github-files.json` and `CHANGED-FILES.json` for every path/blob.

## Exact-A CI

Fresh GitHub reread:39/39 workflows SUCCESS;65 jobs SUCCESS,5 SKIPPED,
zero failed/pending. `audit-ci-jobs.json` records all jobs and steps.
Security Gate, CodeQL, G47, Track F, supplier, employee, advertising, Accounting,
H2 and source integration workflows succeeded at exactA.

Skipped jobs are explicitly not counted as executed passes:

1. Warehouse Frontend: unrelated path filter, no matching files changed.
2. Snapchat settings Frontend: dedicated scope filter.
3. Snapchat settings Backend: dedicated scope filter.
4. Release Readiness HostNode20 clean-clone adapter rehearsal: requires reviewed
   intent B; intentionally does not run on source candidate A. **Still required.**
5. Manual redeploy notice: push-only stage; PR event does not execute it.

Release Readiness run37015231942 attempt2 succeeded. Attempt1 was cancelled before
jobs by queue activity and remains disclosed. Candidate build, artifact
verification, freeze and `load_candidate_release_intent` provenance checks all
executed successfully in the Linux Frontend job110871045737. No new CI/test run
was started during this audit.

## Existing completed verification inspected in this audit

The already-started local runner completed on unchanged exactA:

- Native/operational Backend selection154 files:2172 parent tests +900 subtests,
  zero errors/failures/skips. This is the declared integration regression, not
  every historical test in the repository.
- Full Frontend244 suites /1417 tests PASS, no pending tests; ordinary build PASS.
- Real Mongo8.0.12 replica27141 and standalone27142 were owned disposable local
  fixtures. Both stopped, only admin/config/local remained; source hashes unchanged.
- Smoke B: actual full-app Acceptance execution PASS, canonical writes_paused=true
  before/after, full database/control fingerprints equal, original404/423 probes,
  auth and cleanup recorded. production_verified=false, not Production acceptance.
-16/16 synthetic setup and public409/physicalAPI subsets already passed. Full
  Business UAT remains NOT PASS as the separately approved financial go-live gate.

Completed evidence is preserved under `local-verification`. Static native SSOT
checks cover14 declared converted modules and4 unchanged guards; the completed
regression supplies the corresponding current-source dynamic evidence. This is
not a whole-application claim of zero Legacy access.

## Release Intent

Tracked `release/release-intent-v5.json` remains identical to J and binds OLD
source967ac7a90b8d798ce8a52a2d62bcf0f985034a9f / base901568ccaaf510dc1f84d9c28f38d368d07dc64d.
It is **NOT VALID FOR THIS RELEASE** and was not used for deployment.

New candidate artifact11230198747 from run37015231942 attempt2 binds exact
A57688c6423848165525bbc85265c4bb56e65da1c and J83363097d48e034dc7140a60c290efc684e1ffde.
Its GitHub archive digest matches the downloaded archive:
afacc37bb13a03adfa40adadf6746d4bf00af6bf5ffe3b3b3d5b304c50bb0682.
Candidate JSON SHA256:
d95fd06bfc0eb315fef96fc5354903c90119a63f6e842c80695b1dfafbd3adb7.
Linux CI validated source manifests/provenance and deterministic build evidence.
The local Windows read-only revalidation stopped at the existing POSIX permission
check (`frontend/.env` reported0666); no permission/validator/guard was changed.
That local limitation does not erase Linux CI evidence or establish an application
defect. No intent-only B was created or committed during this audit.

## Guard and remaining gates

Owner-provided terminal evidence establishes `/app` on clean J, GitHub authPASS
and unchanged Release Guard `status` returning `active:false`. This audit did
not independently rerun the shared Guard. Browser/Kernel remains unverified;
the owner explicitly accepts the manually operated /app channel.

Remaining technical release work, not executed here:

1. Freeze the verified candidate in intent-only B and prove A->B provenance.
2. Fresh exact-B CI including actual HostNode20 clean-clone/no-Git adapter
   rehearsal; A's skipped job cannot satisfy this requirement.
3. Later authorized source handoff and unchanged shared /app adapter/release
   gates on the exact final source. No shared rehearsal or release action now.

No new economic C or missing inclusion was established by this audit. Final
Business UAT, Owner balances/inventory, Opening, initialization, Activation and
P08 remain separate financial authorization/execution dependencies.

Production financial writes by this task=0; write-control UNCHANGED;
Opening/Inventory initialization/Activation/Backfill/schedules=NO;
prepare/prepublish/lease/Merge/Deploy=NO. No application source change in this audit.
