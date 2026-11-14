# Ready server release and Android continuation — 2026-11-14

## Current verified state and completed delivery

**The Ready server release and the subsequent operational-image server repair are both published and officially verified. The combined Android80 APK was built successfully and its actual downloaded artifact passed all20 inspection checks.** The original scope expanded to include supplier-receiving storage-full recovery and saved operational images missing only in assembly and labeling; both additions are included in the completed release work.

| Operation | Observed state |
| --- | --- |
| Ready PR1912 merge and Publish | Normal M with the exact reviewed-B tree; one normal Publish |
| Ready platform outcome | New green `Deployment finished`,14 November02:49 UTC+03, version `4e63025` |
| Ready official verification | `verified:true`, `checks:3`, exact M/A/runtime; matching lease closed and final `active:false` |
| Ready scoped rollback | Source/intent prepared and tested, Host20/24 and isolated Emergent rehearsal passed; not activated |
| Original order299308624 | Authoritative status completed; existing completed-order Ready gate remains correctly blocked |
| Image PR1914 | Normal-merged M `d6f246b4d3b14fd56ef2cbc574b99f08bb7d6fc1`, preserving intervening PR1913 |
| Image platform outcome | One Publish; new green completion14 November04:08 UTC+03, version `892c7f3` |
| Image official verification | Original verifier exit0 in562.28s, `verified:true`, `checks:3`, exact M/A/runtime; final original status `active:false` |
| Saved image after deployment | Fresh web reload/read-only order search loaded operational image at311×500; no re-upload |
| Combined Android source | PR352, `a667ad320eb1cb80b6e57e742eda58f84ec5bba4`,1.0.65/code80; all8 applicable exact-source workflows passed |
| APK outcome | EAS build `c68427db-ebd7-4978-9a7e-27fd833bccda` Succeeded; actual20/20 artifact checks passed |
| Physical employee phones | No installation, on-device upgrade or employee distribution was performed |

Ready verify JSON SHA256: `0a91a8097d530c0027b34bf5d6150eb536722335f98af1884af00df5b258e2c8`. Its final guard SHA256: `7410be1a1dec3ea052e3dbfb62c5896880cc53d98149b928eeef1cbfa1a5a2be`.

Image verify JSON SHA256: `340780ca2c752c932f6d843f1d32f30d949578257bc06f486b8e0ea4aeace85e` (33,299bytes); empty stderr. Image final guard SHA256: `595219792dd54a38a8ace80a6a7fc33b928ea74f0dd1bdfac6a2b708dbe0dd1e` (22bytes).

The platform's current green wording is `Deployment finished`. The existing November12 Google/courier protocol record accepts this new event/new version followed by unchanged official verification; no protocol was weakened. The platform also displays a general performance alert whose detailed metrics could not be inspected because browser input actions timed out. Successful release verification is not a complete performance audit.

[Accepted image-server continuation](https://github.com/AMASI-SA/AMASI-SA/blob/7199b910f9450a52486a7561b8ddd18e987d0b31/docs/operations/ASSEMBLY-OPERATIONAL-IMAGE-ACCESS-20261114/STATUS.md) and [completed APK continuation](https://github.com/AMASI-SA/amasi-mobile/blob/25a922a9d3d00b70338e933af0f088e59ef63b6d/docs/operations/ANDROID80-READY-RECEIVING-STORAGE-20261114/STATUS.md) preserve the expanded release evidence. [Install/view the completed Android80 build](https://expo.dev/accounts/amasi-sa/projects/amasi/builds/c68427db-ebd7-4978-9a7e-27fd833bccda). The historical c10-only build form was canceled; exactly one combined a667 build was submitted and no duplicate is needed.

The production lineage advanced from Ready M through independent PR1913 at6012 and then the accepted image M. These later releases preserve the prior Ready repair and do not change the recorded identity of the original Ready deployment.

## Canonical records and repositories

- Server repository: `AMASI-SA/AMASI-SA`.
- Production branch: `hotfix/prod-snap-meta-final`.
- Current forward task branch: `codex/ready-server-current-20261114`.
- Current release ledger: [server PR #1912](https://github.com/AMASI-SA/AMASI-SA/pull/1912).
- Current rollback branch: `codex/ready-current-rollback-20261114`.
- Mobile repository: `AMASI-SA/amasi-mobile`.
- Final mobile continuation: [PR #352](https://github.com/AMASI-SA/amasi-mobile/pull/352), built from exact a667. Earlier [PR #351](https://github.com/AMASI-SA/amasi-mobile/pull/351) is its preserved Ready parent.
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

## Android continuation — completed build and actual artifact

The final combined application source is `a667ad320eb1cb80b6e57e742eda58f84ec5bba4`, tree `93be9b3ef5e3464640b4fcd9fdea9ea75b715f4c`, sole parent Ready source `c10fcd49adaaded57b69afdf3fc73dfc8d9df3bf`. The scoped storage donor is `2fe5dfe883009b23c25b83bf09517f116fb0881a`; unrelated notification/native release changes were not imported.

All8 applicable exact-source workflows passed. Local verification covered19 script commands, and independent storage review passed77 checks. The full Android export, TypeScript/lint, historical route-return checks, real-screen Ready reverse-acknowledgement tests and native checks passed.

| Android outcome | Observed value |
| --- | --- |
| Build | [c68427db-ebd7-4978-9a7e-27fd833bccda](https://expo.dev/accounts/amasi-sa/projects/amasi/builds/c68427db-ebd7-4978-9a7e-27fd833bccda), Succeeded |
| Version / package |1.0.65 /80; `com.amasi.sa.mobile` |
| Profile / environment | `mezan-web-assembly-pilot` / production, internal APK, no automatic store submission |
| Timing | Started14 November03:22 UTC+03; build20m1s / total20m9s |
| Actual APK size |115,538,714bytes |
| Actual APK SHA256 | `93730a8ca69481a8c49e6a4762a1cb32760065881e0f74c6dcdf6c15055397b2` |
| Actual artifact inspection |20/20 checks passed with official Android Build Tools36; command exit0, all inspector stderr empty |
| Signature |Valid v2, one signer; certificate `ca6b376f14e01eadf611b0ea0208eab7c75576ac5ee51606d01df571f0d1ac54` |
| Manifest/runtime |Neither debuggable nor test-only; native OTAfalse/NEVER/0; runtime1.0.65 |
| Report SHA256 | `723acac614e00be99a14c9d39b8acab3044ad3aa6f0460737cb343c4c915cdd4` |

The APK was obtained through the successful authenticated EAS build's normal Download build action. Source provenance comes from the build receipt plus that download association; APK inspection alone cannot prove a Git commit.

The current signer matches historical75/76 evidence. The earlier baseline77 download received an explicit browser URL-policy denial; no retry or alternate-path bypass was used. Therefore a fresh77/installed77 signer comparison and actual on-device upgrade have not been established. The permitted Android80 normal download succeeded independently.

Storage recovery bounds completed scan/invoice history and generated invoice PDFs while preserving unresolved intent, identity and retry bodies under the tested cases. Verified invoice readback precedes compaction. SQLite-full handling performs conservative cleanup and one local-write retry, without replaying business POSTs. This does not establish physical RAM improvement or add durable offline-photo upload.

The [full completed APK record](https://github.com/AMASI-SA/amasi-mobile/blob/25a922a9d3d00b70338e933af0f088e59ef63b6d/docs/operations/ANDROID80-READY-RECEIVING-STORAGE-20261114/STATUS.md) preserves hashes, test identities and limits. Do not rebuild old c10, the standalone donor or the documentation branch. Do not silently uninstall an existing app or clear its data to force installation.

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

## Current final facts and remaining device acceptance

| Area | Final fact |
| --- | --- |
| Ready server |Published and officially verified; full original release/rollback evidence retained above |
| Image server |PR1914 published and officially verified, exact M/A/runtime, three checks, final inactive guard |
| Combined application |Exact a667 Android1.0.65/80 built successfully; actual signed APK verified20/20 |
| Saved operational images |Existing saved image returned after fresh web reload; narrow assembly-only bridge behavior covered by45 regressions |
| Business data |No live Ready, receipt, invoice, image-save, stock or shipping mutation was used as a test |
| Employee communication |No message or notification was sent to employees |
| Outstanding device outcome |Install/update and accept on a physical employee phone during ordinary work; not performed by this session |

The release installation link can now be delivered. The first device acceptance should check eligible Ready items at the top and approved items at the bottom, supplier-invoice receiving and safe retry behavior, and the existing operational image in assembly/labeling under the employee's own account. Preserve current application data during upgrade. If Android rejects the update, inspect the reported reason before any uninstall or data clearing.

For any future deployment or rollback, read the actual production ref and original guard afresh and preserve subsequent owners' work. The previously verified inactive guard is an observation at release closure, not a permanent permission to reuse an old deployment candidate. No further build, merge or Publish is required to deliver the completed APK linked above.
