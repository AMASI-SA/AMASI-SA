# Ready server release and Android continuation — 2026-11-14

## Current verified state and expanded scope

**The Ready server release is published and officially verified.** The user subsequently added supplier-receiving storage-full recovery and clarified that saved operational-product images are missing only in mobile assembly and labeling. No new APK has been built or distributed. The final app candidate now combines Ready ordering and storage recovery; the image permission repair is a separate server release dependency.

| Operation | Observed state |
| --- | --- |
| Ready PR1912 merge | Normal M, tree exactly reviewed B |
| Forward isolated/shared adapters | PASS77.44s / PASS65.14s;120 existing untracked files preserved |
| Scoped rollback | Source/intent prepared; fresh64tests+38subtests; exact-B Host20/24 and isolated Emergent adapter pass; not activated |
| Ready Publish | Exactly one normal Publish,23:39UTC /02:39UTC+03 |
| Platform outcome | New green `Deployment finished`,14November02:49UTC+03; new version `4e63025` |
| Official verifier | `verified=true`, `checks=3`, exact M/A/runtime; stderr empty |
| Matching lease | Closed by successful verify; fresh official status `active:false` |
| Original order readback | Current Salla status completed, local assembly incomplete; existing Ready source-status gate remains correctly blocked |
| Operational image on web | Existing image loaded with311×500 natural dimensions |
| Mobile image defect | Assembly-only request denied403 by earlier generic review permission bridge; narrow GET-only repair in PR1914 |
| Combined Android candidate | PR352, source `a667ad320eb1cb80b6e57e742eda58f84ec5bba4`,1.0.65/code80; fresh CI running |
| New APK | Not built or distributed; old c10-only build form canceled |

Ready verify JSON SHA256: `0a91a8097d530c0027b34bf5d6150eb536722335f98af1884af00df5b258e2c8`. Final guard JSON SHA256: `7410be1a1dec3ea052e3dbfb62c5896880cc53d98149b928eeef1cbfa1a5a2be`. The current platform says `Deployment finished`, not the old literal `Deployment Succeeded`; the existing November12 Google/courier protocol record accepts this green new event/new version followed by the unchanged official three-probe verifier. No protocol was weakened.

The actual production Git branch subsequently advanced through independent PR1913 to `6012ceb480bf9dc0f51ec6cc64c054e5e0db3846`; this does not change the recorded identity of the completed Ready deployment. The image source A is `f91eb6c43d2faeb2f7da174ecdc67a455ac0ec2c`, based on6012, preserving all other merged work. Its intent/deployment remains pending at this checkpoint.

## Canonical records and repositories

- Server repository: `AMASI-SA/AMASI-SA`.
- Production branch: `hotfix/prod-snap-meta-final`.
- Current forward task branch: `codex/ready-server-current-20261114`.
- Current release ledger: [server PR #1912](https://github.com/AMASI-SA/AMASI-SA/pull/1912).
- Current rollback branch: `codex/ready-current-rollback-20261114`.
- Mobile repository: `AMASI-SA/amasi-mobile`.
- Mobile continuation: [draft PR #351](https://github.com/AMASI-SA/amasi-mobile/pull/351).
- Issue #1006 has reached its comment limit. Do not repeatedly try to append to
  it. PR #1912 and this task's final documentation checkpoint are the continuation
  records.
- Documentation-only continuation branch: `codex/ready-release-continuation-20261114`.
- The exact documentation commit is recorded in [PR #1912](https://github.com/AMASI-SA/AMASI-SA/pull/1912); this file does not self-reference its own commit SHA.

Publish the completed status document on a separate documentation-only branch.
Do not modify, amend, rebase, squash, or regenerate a frozen release pair merely
to include this report. A documentation checkpoint is not a deployment and must
not be presented as one.

## Current forward release identity

| Role | Exact identity |
| --- | --- |
| Current production base J2 | `5d158f6006b63ac7663c293696d501f595ce8754` |
| Ready source A | `cdf175841376d2f68605d66c1979273d23aed70d` |
| Ready intent-only B | `a948f4592fd725792fe354dfa72fd40e99000915` |
| Normal merge M from PR #1912 | `304e8793a15fd3269fd6f6d4cfba903d1760e285` |
| Exact B and M tree | `baf88eeeb7eec15a043da14358fa77f2ba2bc149` |
| Intended runtime release ID | `rg5-3e41b3f0b14d180f45cd3b242798ea348797b06e8edc770592130ac87b2a9cb0` |
| Governed Frontend artifact digest | `9a34bf9ba4d65a89b3431002ab8e729d6b5311a332ec17800abffb88c2406ab6` |

J2 is the concurrent campaign-card merge from PR #1911. Its campaign Backend,
Frontend, tests, workflow, matching `dnspython==2.9.2` requirements pins, and
other source changes were preserved. The Ready integration starts from J2 and
does not replace it with the earlier production baseline.

Protocol v5 uses A for the governed runtime source identity and B, or the
tree-equivalent M, for deployment checkout identity. The intent-only change from
A to B is `release/release-intent-v5.json`. M is a normal merge whose tree exactly
matches B. Do not substitute a PR's cached `base.sha` for a fresh read of the
actual production ref.

## Ready behavior and review correction

The repair allows eligible, reviewed physical supplier pieces to become Ready
after the supplier and employee receipt requirements, completed services,
accepted current component reservation, matching numeric source revision,
eligible canonical order state, and absence of a hold have been established.
The existing owner transaction records Ready, consumes one reserved unit, and
advances the workflow atomically; retries remain idempotent. Final shipment
completion continues to require all current source units and assembly work to
be complete.

Independent review found and corrected an additional regression in the original
PR #1904. A physical-piece snapshot fence had also disabled otherwise eligible
direct and operational cards in historical mixed orders. The correction scopes
that additional fence to physical rows while retaining shared cancellation and
current-plan validity blockers. Search remains observational; it does not create
legacy markers, consume stock, or advance preparation state.

The four Ready runtime/test files are:

1. `backend/preparation_piece_operations.py`.
2. `backend/review_local_policy.py`.
3. `backend/tests/test_g47_component_lifecycle_integration.py`.
4. `backend/tests/test_review_local_assembly.py`.

Two real-Mongo regressions demonstrated the historical mixed-order defect before
the correction and passed afterward: missing source revision and newer source
revision must block the physical row without incorrectly blocking otherwise
eligible direct/operational rows. The affected forward suite passed **75 tests
and 38 subtests**, with zero failures, errors, or skips. Those are behavior
results; the current combined release also has its own exact-B CI evidence below.

## Forward verification and CI

The current exact B completed **18 applicable successful CI workflow runs**, plus
the expected out-of-scope TikTok workflow skip. This statement is about the
applicable workflow results; it does not assert that every conditional job or
test in every workflow executed.

| Evidence | Result / link |
| --- | --- |
| Current B release readiness | PASS — [run 43525479465](https://github.com/AMASI-SA/AMASI-SA/actions/runs/43525479465) |
| Current B security gate | PASS — [run 43525479623](https://github.com/AMASI-SA/AMASI-SA/actions/runs/43525479623) |
| Current B review/real-Mongo gate | PASS — [run 43525479780](https://github.com/AMASI-SA/AMASI-SA/actions/runs/43525479780) |
| Forward isolated Emergent adapter | PASS, **77.44 seconds** |
| Isolated governed Frontend artifact digest | `9a34bf9ba4d65a89b3431002ab8e729d6b5311a332ec17800abffb88c2406ab6` |
| PR #1912 merge tree check | M tree equals B tree exactly |
| Shared `/app` adapter retry | PASS, **65.14 seconds**, exit **0**, reviewed M; final tracked state clean |

The clean-clone/isolated adapter checks establish the governed build and package contract. The later platform event and completed official three-probe verification independently established the public runtime identity and served artifact bytes.

## Shared Emergent workspace and verified deployment

The Ready release owner independently confirmed that the previous PR #1911
owner had used the official abort path to close its own lease; the shared guard
then reported `active: false`. No earlier lease was reused or manually cleared.

Shared `/app` was fast-forwarded to exact M while preserving **120 pre-existing
untracked files**. The first shared adapter attempt refused two ignored Python
bytecode files under `backend/__pycache__`. The release owner preserved and
quarantined both files, with their hashes, under:

```text
/tmp/ready-304e-preserved-bytecode
```

Only those two identified files were moved out of the governed source boundary;
the refusal was not bypassed by weakening adapter validation or deleting unrelated
workspace files. The adapter was then rerun with `PYTHONDONTWRITEBYTECODE=1`.
That retry **passed in 65.14 seconds with exit 0**. The owner verified that shared
`/app` remained tracked-clean at M, all 120 pre-existing untracked files were
preserved, and zero untracked files were added. No source, manifest, or validation
check was weakened to obtain the pass.

| Quarantine record to retain durably | Final value |
| --- | --- |
| Original relative path of bytecode file 1 | `backend/__pycache__/frontend_build_identity.cpython-311.pyc` |
| File 1 SHA-256 and preserved destination | `489ba93d9df88a1e4f9a7e703f5ab838f93cf60e5f6f60fe10b7eee45361b67b`; `/tmp/ready-304e-preserved-bytecode/backend/__pycache__/frontend_build_identity.cpython-311.pyc` |
| Original relative path of bytecode file 2 | `backend/__pycache__/release_protocol_v5.cpython-311.pyc` |
| File 2 SHA-256 and preserved destination | `c1c09df55f54c1692c24af99f4001907d6717ff06f72b95c21ec58209962143d`; `/tmp/ready-304e-preserved-bytecode/backend/__pycache__/release_protocol_v5.cpython-311.pyc` |
| Shared status after adapter retry | Tracked clean, HEAD `304e8793a15fd3269fd6f6d4cfba903d1760e285` |
| Untracked preservation after retry | All **120** pre-existing files preserved; **0** added |

The earlier **`Agent Running`** publication blocker was resolved through normal
UI behavior. The release owner did not stop or close the hosting agent, use a
paid chat, invoke an alternate private publication route, or use the legacy
`NOT_COMMISSIONED` publisher.

After the completed rollback rehearsal, the owner again observed the shared
guard inactive and `/app` at M. The owner then completed the official preparation
and publication steps below. That earlier inactive observation is not the final
post-deployment guard state.

| Publication evidence | Observed value |
| --- | --- |
| Official prepare actor | `codex-ready-server-20261114` |
| Official prepared time | `2026-11-13T23:38:18.470493+00:00` |
| Prepared deployment SHA | `304e8793a15fd3269fd6f6d4cfba903d1760e285` |
| Prepared source SHA | `cdf175841376d2f68605d66c1979273d23aed70d` |
| Prepared runtime ID | `rg5-3e41b3f0b14d180f45cd3b242798ea348797b06e8edc770592130ac87b2a9cb0` |
| Official prepublish checks | Two executions; both `ready_to_publish=true` for the same exact identities |
| Normal UI action | One accepted `Publish changes` action at approximately **2026-11-13 23:39 UTC** |
| New platform deployment entry | `Deployment Started`, **Nov 14, 02:39 AM** in the UI's UTC+03 time |
| Completed platform stages | `Workspace Ready`; `Changes Packaged` |
| Final platform stage | New green `Deployment finished`,14November02:49UTC+03; version `4e63025` |
| Current UI success wording | `Deployment finished`; prior literal not shown; existing protocol precedent applied with unchanged verifier |
| Official three-probe live verification and matching lease closure | PASS, verified=true/checks3/exactM/A/runtime; matching lease closed, fresh active=false |

This Ready deployment is complete. Do not repeat its prepare, Publish or verify merely because a conversation or terminal changes. A subsequent image release requires its own fresh source/intent/gates and lease, preserving the current production branch.

The runtime adapter contract remains protocol v5: `frontend`'s governed build
entry point dispatches the adapter; the reviewed origin is
`REACT_APP_BACKEND_URL=https://mezansalla.com`; governed tooling is Node
**22.23.2** and Yarn **1.22.22**. Inherited ignored build outputs do not replace
the tracked intent or the Cloud Build verification boundary.

## Current rollback release and verified behavior

Recovery restores only the four Ready files from J2. It preserves campaign work,
both `dnspython==2.9.2` pins, other production source, and the forward intent while
constructing rollback source A_R. A new intent-only B_R then supplies the rollback
release identity; the forward identity is not reused.

| Role | Exact identity |
| --- | --- |
| Rollback branch | `codex/ready-current-rollback-20261114` |
| Rollback source A_R, parent M | `85a9bee0a3d0d0b7d4f3c571c1a1ec47fcb84af6` |
| A_R / freshly tested rollback behavior tree | `fe5b157f759ec0daf67f05e445fe841c295eff66` |
| Rollback intent-only B_R | `28adddb16c9e5b17bdd69e88c15be4a7451fbc18` |
| Rollback B_R tree | `a44b65c858e96fb9789b49e78f6cc151e89b8759` |
| Rollback intent Git blob | `770a61eb347d70168fa90843e0b3688821f36c41` |
| Intended rollback runtime ID | `rg5-d2624164803883432e7f6874508dfdfd259695e0daee00718d88f782683f701b` |

The independent current-tree rollback rehearsal started from exact forward B in
a separate worktree and restored only the four allowed paths from J2. Its full
tree is `fe5b157f759ec0daf67f05e445fe841c295eff66`. Compared with B, exactly those
four paths changed. Compared with J2 itself, the only differences are the Ready
status document and the retained forward intent. All other concurrent runtime,
campaign, dependency, and security content is therefore preserved byte for byte.

| Restored file | Blob identical to J2 |
| --- | --- |
| `backend/preparation_piece_operations.py` | `d776e921710d360fe188c7def24e83dae1af3dfc` |
| `backend/review_local_policy.py` | `3e1f2efff522ffaf485f9cd14cc0754f81c4d030` |
| `backend/tests/test_g47_component_lifecycle_integration.py` | `eee77623491b32925e37ef6a7be6d1e3d69019cc` |
| `backend/tests/test_review_local_assembly.py` | `0c2385a59173677df382289bbc7584b73d5c2725` |

The fresh suite passed **64 tests and 38 subtests in 241.76 seconds**, exit **0**.
Parsed JUnit contains 64 test cases, **zero failures, zero errors, and zero skips**.
It ran on an owned MongoDB **7.0.16** replica set at `127.0.0.1:38017`, with no
production connection or data. The owned Mongo process was stopped and its
temporary database directory removed after the run. The tested tree remained
unchanged; the root forward checkout remained clean at B.

Exact test command, from the isolated rollback worktree's `backend` directory:

```bash
MZ2_TEST_MONGO_URI='mongodb://127.0.0.1:38017/?replicaSet=readyreview' \
PYTHONDONTWRITEBYTECODE=1 \
PYTHONPATH='.:tests' \
/workspace/scratch/864304cb148b/mezan-items-venv/bin/python \
  -m pytest --noconftest -p no:cacheprovider -q --tb=short \
  tests/test_g47_component_lifecycle_integration.py \
  tests/test_review_local_assembly.py \
  --junitxml=/workspace/scratch/b2b642f1aff1/ready-current-rollback-behavior-real.xml
```

Local evidence, which must not be treated as a replacement for the remote
checkpoint if the workspace expires:

- Worktree: `/workspace/scratch/b2b642f1aff1/mezan-ready-current-rollback-behavior`.
- Runner: `/workspace/scratch/b2b642f1aff1/run-ready-current-rollback-behavior.py`.
- JUnit: `/workspace/scratch/b2b642f1aff1/ready-current-rollback-behavior-real.xml`.
- JUnit SHA-256: `cbd8b521a946626d9a38a5b22bf5ad63aeef43e3eea57f67cba17b75133db899`.
- Tree/blob proof: `/workspace/scratch/b2b642f1aff1/ready-current-rollback-behavior-tree.json`.

Rollback release evidence:

| Evidence | Result / value |
| --- | --- |
| A_R readiness | PASS — [run 43527390287](https://github.com/AMASI-SA/AMASI-SA/actions/runs/43527390287) |
| A_R readiness artifact ID | `14086111533` |
| Artifact ZIP SHA-256 | `aac60986e734e55e06bc50190487f34b54442403a6bb9d8d2eb02510430d0bb0` |
| Frozen rollback intent file SHA-256 | `2fc116d10079e737360a24dff67901226a7a6ce24dbeea5ce28d2d63a2844174` |
| B_R readiness and both adapters | **SUCCESS**, completed **2026-11-13 23:36:36 UTC** — [run 43527983003](https://github.com/AMASI-SA/AMASI-SA/actions/runs/43527983003) |
| B_R exact-head adapter coverage | Both actual Host Node **20** and **24** adapters, runtime checks, and package checks executed and passed |
| B_R isolated Emergent rehearsal | **PASS, 52.39 seconds, exit 0**, exact B_R with source A_R `85a9bee0a3d0d0b7d4f3c571c1a1ec47fcb84af6` and the intended rollback runtime ID |
| Isolated rehearsal directory | `/tmp/ready-current-rollback-28add-rehearsal` |
| Isolated rehearsal result JSON | `/tmp/ready-rollback-28add-result.json` |
| Supplementary B_R artifact/package evidence reference | `/tmp/ready-rollback-28add-packages.json`; CI evidence artifacts Host20 `14085204639`, Host24 `14085717612`; generated output artifacts Host20 `14085079575`, Host24 `14085947460` |

This is a prepared recovery path, not an instruction to activate it by default.
Before using it, re-read the actual production ref, guard ownership, and affected
operational state. If intervening source has changed, do not overwrite that work
with this snapshot; construct and verify a new scoped recovery from the actual
current production tree.

Recovery changes code behavior only. It must not delete events, reverse stock
consumption automatically, reset completed order/shipping states, repeat Ready,
or replay provider operations. Reconcile any in-flight outcome before deciding
on a further operational action.

## Android continuation

The employee-app Ready-ordering change remains separate from the server repair.
The prepared current source is [draft PR #351](https://github.com/AMASI-SA/amasi-mobile/pull/351),
exact commit **`c10fcd49adaaded57b69afdf3fc73dfc8d9df3bf`**, with **all seven CI checks
passed**. This is source evidence only: no new APK or completed EAS build has yet
been established for this source.

Expected configured application identity:

| Field | Expected value |
| --- | --- |
| Android package | `com.amasi.sa.mobile` |
| Version name | `1.0.65` |
| Version code | `80` |
| EAS build profile | `mezan-web-assembly-pilot` |
| Expo project | `f285e8fe-06a6-48b4-b095-f1c42d63f7c3` |
| OTA updates | Disabled; `checkAutomatically=NEVER`, `fallbackToCacheTimeout=0` |
| Runtime version policy | `appVersion`; verify the built artifact's resolved value |

Build 80 preserves the isolated build-77 scope and avoids the already-used
intervening version codes. The intended UI ordering is eligible Ready cards
first, blocked cards next, and completed Ready cards last; a newly confirmed
card moves to the end of the completed group. Do not claim that this source
change has reached installed employee applications before artifact distribution
and installation have been observed.

The attempted baseline APK77 browser download received an **explicit browser
URL-policy denial**. No alternate client or download route is authorized to
work around that denial, and none was attempted by the independent reviewer.
The baseline artifact and its signature have therefore not been independently
validated during this continuation. A physical phone is unavailable, so device
installation and live mobile acceptance are also unproven.

Official Android Build Tools 36.0.0 were prepared in an isolated local directory
for read-only artifact inspection. Google metadata and archive checks matched:

- Archive: `https://dl.google.com/android/repository/build-tools_r36_linux.zip`.
- Published size: `63,737,259` bytes.
- Published SHA-1: `b0b6376977657e8ad9b969bacf4093601da2c6fb`.
- Computed archive SHA-256: `5d9ac77fb6ff43d9da518a337b4fcf8f9097113df531d99ccefe80ef7ce8250b`.
- Tools directory: `/workspace/scratch/b2b642f1aff1/mezan-apk-validation-tools-no15bfz7/unpacked/android-16`.
- `apksigner version` and `aapt2 version` exited 0; Java and required native
  libraries were available. No system installation, global PATH change, key
  import, or private-key access was performed.

After server acceptance and a permitted artifact handoff, verify the actual APK
package/version, successful APK signature verification, signer certificate
SHA-256 equality with the authentic baseline, and compiled Expo metadata. Tool
availability is not artifact verification. Do not use the legacy
`NOT_COMMISSIONED` publisher for mobile or server release.

| Android outcome | Final observed value |
| --- | --- |
| Server acceptance prerequisite | **[PENDING]** |
| EAS authentication/build readiness | **[PENDING: observed status, without credentials]** |
| Built source SHA and EAS build ID | **[PENDING]** |
| Artifact URL/path and APK SHA-256 | **[PENDING: only after permitted acquisition]** |
| Verified package, version, and OTA metadata | **[PENDING]** |
| Baseline provenance and matching signer certificate | **[PENDING; baseline download currently denied]** |
| Device installation and functional acceptance | **[PENDING; physical phone unavailable]** |
| Distribution/publication | **[PENDING; not performed]** |

## Superseded references preserved

- Original server [PR #1904](https://github.com/AMASI-SA/AMASI-SA/pull/1904), source
  `9fee416d40e2b10031fb3d80c532d94e7593f5c9`, was not modified as part of this
  continuation. Current release work incorporates its reviewed behavior plus
  the independently corrected historical mixed-order regression.
- Original mobile [PR #350](https://github.com/AMASI-SA/amasi-mobile/pull/350)
  remains untouched by the current PR #351 preparation.
- Superseded server [PR #1910](https://github.com/AMASI-SA/AMASI-SA/pull/1910)
  source A `8518af15b3f49c179f37782805eb6eb09a552f11` and intent B
  `3f8fb6cdf0d8eb2c4f39d4fbc9a8ae882438fbd5` remain preserved on
  `codex/ready-server-intent-20261114`. Their successful historical checks do not
  authorize deploying them after J2's concurrent campaign merge. Do not merge,
  deploy, rebase, or repurpose that old frozen pair as current recovery.
- The old rollback behavior tree `c5c7dceca860065c3273b64e2c725a2a5addcb18`
  has been superseded by the freshly tested current tree
  `fe5b157f759ec0daf67f05e445fe841c295eff66` and the current A_R/B_R pair.

## Resume procedure and next safe commands

First read the latest PR #1912 ledger entries, this final documentation checkpoint,
the repository's `AGENTS.md`, and the exact current forward/rollback workflow
conclusions. Do not restart a completed deployment merely because a previous
terminal or conversation ended. Reconcile newer owner observations before using
the draft-time state above.

Read-only shared-state checks:

```bash
cd /app
python scripts/production_release_guard.py status
git status --short --untracked-files=no
git fetch origin hotfix/prod-snap-meta-final
git rev-parse HEAD origin/hotfix/prod-snap-meta-final
git rev-parse HEAD^{tree}
git merge-base --is-ancestor cdf175841376d2f68605d66c1979273d23aed70d 304e8793a15fd3269fd6f6d4cfba903d1760e285
git diff --exit-code a948f4592fd725792fe354dfa72fd40e99000915^{tree} 304e8793a15fd3269fd6f6d4cfba903d1760e285^{tree}
```

Expected forward deployment checkout is M, with tree
`baf88eeeb7eec15a043da14358fa77f2ba2bc149`. A changed actual production ref or
another owner's active lease requires reconciliation before a shared mutation.
Never clear another owner's lease or use `git reset --hard` to force agreement.
Preserve unrelated files and the recorded quarantine.

Already executed, for the audit record only; **do not rerun to resume the
in-progress deployment**:

```bash
cd /app
python scripts/production_release_guard.py prepare --actor codex-ready-server-20261114
python scripts/production_release_guard.py prepublish
```

The prepublish command was checked twice, and both checks passed. Exactly one
normal UI Publish action followed. No second Publish action is authorized merely
to compensate for a conversation or terminal interruption.

Next actions, in order:

1. Preserve the successful Ready publication and tested scoped rollback records. Do not republish the old Ready source.
2. Complete server PR1914 for the narrow operational-image permission fix: exact-source CI, new source A/intent-only B, exact-B gates, original isolated/shared adapters, normal merge, fresh inactive guard, prepare/prepublish, one Publish, green platform completion and unchanged three-probe verification. Re-read the actual production ref and never interfere with another owner.
3. Complete fresh CI on Android PR352, including storage recovery, full application TypeScript/lint, full-history route-return and Android export. Do not build the superseded c10-only source.
4. After server dependencies are accepted, build exact combined Android source through the existing Expo internal APK profile. Verify actual artifact identity/signature/metadata through permitted means and provide its install link. Retain any physical-device or signer-comparison limitations honestly.
5. Update this documentation-only continuation branch and the corresponding PRs at each verified milestone. Never modify a frozen release pair to add post-release documentation.

## Current final facts and remaining work

| Fact | Observed value |
| --- | --- |
| Ready production deployment | M304e8793, sourceAcdf17584, runtime rg5-3e41b3… exactly as the identity table above |
| Platform | One Publish; new green finished02:49UTC+03, new version4e63025 |
| Official verification | PASS3probes, full governed identity/hash/frontend and canonical/cache-busted artifact metadata/bytes/MIME; empty stderr |
| Final Ready lease | Closed by verifier; fresh official active=false |
| Reported order | Source completed / تم التنفيذ; local incomplete; source-status safety gate correctly blocks further Ready, no business mutation |
| Additional positive live Ready sample | Not yet recorded; do not claim the completed order exercises the new reviewed physical exception |
| Image server fix | PR1914 sourceAf91eb6c43d2faeb2f7da174ecdc67a455ac0ec2c on latest6012;45freshcombined-base tests pass, separately reviewed; new release pending |
| Combined Android | PR352 sourcea667ad320eb1cb80b6e57e742eda58f84ec5bba4, tree93be9b3ef5e3464640b4fcd9fdea9ea75b715f4c,1.0.65/code80;19local script commands and77independentchecks pass; fresh full CI pending |
| APK/device/distribution | No new build, no physical-phone acceptance; old Ready-only form canceled |
| Next action | Complete image server protocol and combined appCI, then build/verify/deliver the combined APK |

The historical Android parent/earlier APK/tool evidence above is retained for provenance; its c10 SHA is no longer the final candidate. No images, unresolved receipts, pending invoices, credentials, orders, stock or shipping records were cleared to obtain acceptance. The final deliverable is still in progress because the newly requested image release and combined APK remain outstanding.
