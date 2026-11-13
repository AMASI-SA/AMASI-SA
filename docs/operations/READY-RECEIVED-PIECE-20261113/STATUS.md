# Ready server release and Android continuation — 2026-11-14

## Checkpoint status and authority

This is a WIP continuity checkpoint recorded during the accepted deployment.
The latest platform observation is described below; unobserved outcomes remain pending. It records completed evidence and explicitly marks facts
that were still pending at that time. The release owner must fill the final-state
fields below from observed results before publishing the final documentation
checkpoint. A pending field is not a passed check.

The owner authorized this order of work: **publish the Ready server repair,
verify the live service, then build and publish the separate Android Ready-ordering
repair**. That authorization remains in effect. Android publication depends on
successful server acceptance.

At this checkpoint:

| Operation | Observed state |
| --- | --- |
| Ready source reviewed and checked | Complete for the current frozen source/intent pair |
| Ready PR #1912 merged | Yes, normal merge M; M has the exact reviewed B tree |
| Forward isolated Emergent adapter | Passed |
| Forward adapter in shared `/app` | PASS, 65.14 seconds, exit 0; tracked clean and all 120 untracked files preserved |
| Previous owner's release lease | Officially aborted by its owner; inactive state independently observed by the Ready release owner |
| Ready task's own lease | Officially prepared by `codex-ready-server-20261114` for exact M/A/runtime; final closure pending |
| Ready prepublish | Executed twice; both returned `ready_to_publish=true` for the same exact M/A/runtime |
| Ready Publish action | **One normal UI `Publish changes` action accepted at approximately 23:39 UTC** |
| Current deployment stage | **In progress: `Building website`**; `Workspace Ready` and `Changes Packaged` complete |
| Ready live identity and functional acceptance | Pending; no claim that Ready is deployed |
| Hosting publication control | Historical `Agent Running` blocker resolved through the normal UI; the deployment was accepted |
| Current rollback behavior suite | Passed on the current combined tree |
| Current rollback B readiness/adapters | PASS, including both actual Host Node 20/24 adapter/runtime/package checks |
| Current rollback isolated Emergent adapter | PASS, 52.39 seconds, exit 0 |
| Android source and CI | Draft PR #351 prepared; all seven checks passed |
| New Android APK | Not built or published; EAS execution not yet confirmed |

The production Git branch **has changed** through PR #1912's merge, and **one
Ready deployment has now started**. Whether the reviewed runtime has become
active is still pending platform completion and live verification. This is an
in-progress deployment, not a blocked publication handoff. Do not confuse an
accepted Publish action, adapter materialization, or a successful CI run with a
successfully verified live rollout.

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

The clean-clone/isolated adapter checks establish the governed build and package
contract for the reviewed candidate. They do not establish that the accepted
deployment has completed or that the public runtime serves those bytes. The
newer platform success result and live probes remain required.

## Shared Emergent workspace and deployment in progress

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
| Current stage | **`Building website` — in progress** |
| Newer explicit `Deployment Succeeded` | **[PENDING]** |
| Official three-probe live verification and matching lease closure | **[PENDING]** |

Keep observing this already-started deployment. Do not repeat `prepare`, click
Publish again, or abort the matching lease merely because the conversation or
terminal changes. A failed deployment or verification needs its own observed
result and an owner-directed recovery decision.

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

1. Reconcile the most recent release-owner and platform observations with this
   draft. Retain the completed forward and rollback proof. Do not launch a
   duplicate adapter rehearsal or another publication while the accepted
   deployment is still running.
2. Observe the existing **Nov 14, 02:39 AM UTC+03** deployment through the normal
   UI. Preserve sanitized build/adapter output when available. `Workspace Ready`
   and `Changes Packaged` are complete; `Building website` was still running at
   the latest observation. Require a newer explicit **Deployment Succeeded** for
   this deployment, not a historical success entry.
3. Keep this deployment's matching lease intact during the in-progress attempt.
   Read-only status checks may confirm its exact M/A/runtime binding. Do not
   clear another owner's lease, force a reset, stop/close the hosting agent, or
   treat the earlier pre-lease `active: false` result as final closure. If the
   platform or verification fails, record the actual failure and reconcile the
   live state before an owner-directed retry, abort, or scoped recovery. An
   official abort must match this lease's recorded deployment SHA and release
   ID; it does not itself undo a deployment.
4. Once the newer platform success has been observed, use the official
   verification command:

   ```bash
   cd /app
   python scripts/production_release_guard.py verify --url https://mezansalla.com
   python scripts/production_release_guard.py status
   ```

   The single governed verification must pass all three consecutive probes for
   the intended forward release ID and source A, with
   `verified_identity_available=true`, `critical_file_hashes_match=true`, and
   `frontend_build_verified=true`. Canonical and cache-busted `/build-meta.json`
   must return the exact governed JSON bytes with `application/json` content
   type. SPA HTML or a stale identity is failure. The matching lease must close,
   and final status must be `active: false`.

5. Inspect the reported order and representative unaffected direct/operational
   behavior read-only. Compare eligibility, receipt/services, current revision,
   readiness, and shipping completeness with the reviewed contract. Do not
   manufacture acceptance evidence by repeating Ready, consuming stock,
   completing/shipping an order, printing a provider label, syncing/backfilling,
   or performing accounting, supplier, or advertising writes. Preserve useful
   sanitized outcomes; do not commit customer records or secrets.
6. Proceed to Android only after the server's live identity and acceptance are
   established. Build exact reviewed PR #351 source, verify actual artifact
   identity/signature/metadata through permitted means, and record the remaining
   device or distribution limitation honestly.
7. Publish the completed documentation-only checkpoint and update PR #1912 with
   its branch, full SHA, final live/rollback/mobile state, blockers, and the exact
   next safe action. Do not alter frozen forward or rollback release content to
   add the documentation.

## Final-state fields for the release owner

These fields must be completed from new evidence. Leave an item explicitly
blocked or not performed when that is the observed outcome; do not replace an
unknown with success.

| Required final fact | Value to record |
| --- | --- |
| Final record timestamp, UTC and Asia/Riyadh | **[PENDING]** |
| Actual production branch SHA/tree at final check | **[PENDING]** |
| Shared forward adapter retry | PASS, 65.14 seconds, exit 0; append durable output reference |
| Shared tracked status and preservation proof | Clean at M after adapter; all 120 pre-existing untracked files preserved, 0 added; append final post-attempt observation |
| Rollback B_R final CI/adapters | PASS: run 43527983003 complete at 23:36:36 UTC, both Host 20/24 adapters executed; isolated Emergent PASS 52.39 seconds, exit 0 |
| Hosting `Agent Running` blocker | Resolved through normal UI; one Publish action accepted without agent stop/close or paid chat |
| Ready lease prepare/prepublish | Official actor `codex-ready-server-20261114`; prepared exact M/A/runtime; two prepublish checks returned `ready_to_publish=true`; final closure pending |
| Publish action and platform result | One action accepted approximately 23:39 UTC; new `Deployment Started` Nov 14, 02:39 AM UTC+03; `Building website` in progress; **final success/failure pending** |
| Three live identity/hash/Frontend probes | **[PENDING: exact values or not performed]** |
| Canonical/cache-busted build metadata bytes and MIME | **[PENDING]** |
| Final shared guard state | **[PENDING: fresh output; do not reuse an earlier inactive observation]** |
| Read-only functional acceptance | **[PENDING: sanitized findings and any unresolved discrepancy]** |
| Running Production changed by this task | Deployment initiated; **[PENDING: observed live cutover and final identity, with platform/live evidence]** |
| Android build, artifact verification, device and distribution state | **[PENDING]** |
| Documentation-only checkpoint branch/full SHA/PR | **[PENDING]** |
| Exact next safe action if work remains | **[PENDING]** |

At this draft's latest update, all preparatory gates had passed, the official
lease and both prepublish checks had succeeded, and one normal UI Publish action
had started the new deployment. Continue observing that deployment; do not
republish. Its final platform outcome, live verification, matching lease closure,
read-only functional acceptance, and Android publication remain pending.
