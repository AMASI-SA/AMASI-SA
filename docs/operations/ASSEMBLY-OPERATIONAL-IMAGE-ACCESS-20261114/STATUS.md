# Operational images in mobile assembly — release continuation

## Scope and latest result

The user confirmed that operational-product images are saved and visible in waiting review, but disappear only in assembly and labeling. The image save flow is correct. The reviewed server repair permits an assembly-authorized mobile employee to read the already-saved image through the mobile permission bridge.

This is a documentation continuation of the immutable source checkpoint, not another deployment candidate. Source/intent identities must never be amended for a status update. The image release is now normal-merged as `d6f246b4d3b14fd56ef2cbc574b99f08bb7d6fc1`; its tree exactly matches reviewed B. The authorized **single Publish changes** was submitted and Emergent displays **Deployment Started14November2026 03:58 UTC+03**. Our matching lease is active. Explicit platform completion and official three-probe verification remain pending; no live image-success claim yet.

The earlier Ready server release was separately published and officially verified. The combined Android app includes Ready ordering and supplier-invoice storage recovery; the one exact-source APK build succeeded and its actual artifact passed20 verification checks. Final installation-link delivery waits for image-server acceptance.

## Cause and behavior

`backend/mobile_app_request_context.py` previously classified the single-image URL under the generic review-page permission prefix. An assembly-only employee received HTTP403 before the image endpoint could apply its existing assembly permission and merchant ownership checks.

The runtime patch is nine added lines. It grants the existing `app.page.assembly_shipping` permission as an alternative only for **GET of one valid operational-image identifier**, with an optional trailing slash. It retains driver denial, enabled live grants, linked merchant resolution and tenant-specific asset lookup. It does not expand review-page access, uploading, deleting, or employee account grants. Existing saved images do not need re-uploading.

## Immutable release identities

| Role | Identity |
| --- | --- |
| Repository | `AMASI-SA/AMASI-SA` |
| Production branch | `hotfix/prod-snap-meta-final` |
| Task branch | `codex/assembly-operational-images-20261114` |
| PR | <https://github.com/AMASI-SA/AMASI-SA/pull/1914> |
| Base J | `6012ceb480bf9dc0f51ec6cc64c054e5e0db3846` |
| Source A | `f91eb6c43d2faeb2f7da174ecdc67a455ac0ec2c` |
| Source A tree | `f6095d6f92fdadf5339d78a0e6ef8fd15edba5d0` |
| Intent-only B | `c9c24270766eb59f9a0e42b84f5f38883af7fccf` |
| B tree | `84fdb6d5ac2ff520ee78fc2ff294dc62f519889b` |
| Actual normal merge M | `d6f246b4d3b14fd56ef2cbc574b99f08bb7d6fc1` |
| Intent blob | `c4eedef2f78128df6888cfd0589fd40c12160f24` |
| Intent SHA256 | `4ca67c11fa28590dfd3e1d1943785000a182029f1793038b50589c4f1d880c6d` |
| Runtime release | `rg5-da00190ef168c57061fc38676a46099d28817d602aab27baff59155c6956cd2c` |
| Frontend artifact digest | `9a34bf9ba4d65a89b3431002ab8e729d6b5311a332ec17800abffb88c2406ab6` |

The source delta from J contains exactly the runtime bridge, its image regression tests, and the task status document. B changes only the tracked v5 intent. The runtime blob is `31fa05226a1e05c09f52cc11fd312da0ac2d39a6`; the test blob is `d6f21bc7cad19d91bf5b5a4f03f4ceefec7979f3`. The five PR1913 diagnostic changes and preceding Ready/campaign work are preserved.

## Verification completed

The red HTTP regression returned200 for both review roles and403 for assembly-only. After the fix,45 tests passed locally, all45 passed in a separate independent review, and all45 passed again on latest base6012 in2.711s with no failures/errors/skips. Latest-base JUnit SHA256: `484f3745c782f3bda06a687164ec3270c086d509ec748b9df9514236de2b3cf8`.

Coverage includes real response bytes/MIME through the permission bridge and image router, disabled and ungranted accounts, drivers, foreign-merchant images, and refusal of upload/delete/create/review-data requests. Fixtures use synthetic identities and `mongomock_motor`; this is not a physical employee-phone or full production JWT acceptance claim.

All12 applicable workflows passed on source A before freezing B. Source Readiness43532476217 produced candidate artifact14087528835 with ZIP SHA256 `7ff9ba33e421d9aa01c3958be82424616e5cf0f9ad7becc4fb7683051ebf1a5e`. The unchanged candidate validator passed on clean exact A.

All12 applicable workflows then passed on exact B. The branch-inapplicable TikTok workflow was the only expected workflow skip. Exact-B Readiness43533379096 passed the actual Host20 and Host24 no-Git clean-clone adapters, isolated packages and runtime JSON/MIME checks. Evidence artifacts14088037957 and14089365245. Fulfillment passed508tests+59subtests; Mobile Tracking passed157tests+4subtests; Stock Eligibility43533379008 passed3790tests+7subtests with an explicit no-skips gate; CodeQL43533378972 passed.

After a fresh inactive official guard, isolated worktree `/tmp/assembly-image-c9c2427-rehearsal` ran the original `yarn --cwd frontend build` dispatcher with Python bytecode disabled. It passed in47.27s, build exit0, explicit reviewed-intent frontend verifier exit0, package verifier exit0, exact A/runtime match and tracked-clean checkout. Evidence is retained under `/tmp/assembly-image-c9c2427-*`; this record preserves the meaningful result independently of those temporary files.

## Earlier competing release coordination — historical checkpoint

Before image merge, the official shared guard showed another active owner:

- Actor: `codex-operational-source-diagnostics-1913`.
- Deployment: `6012ceb480bf9dc0f51ec6cc64c054e5e0db3846`.
- Runtime: `rg5-51a27cab964259844260959047effc778bbe1aa204c5da13133af9e0628fcaaa`.
- Emergent displayed Deployment Started14November2026 03:31 UTC+03.

No shared `/app` mutation, image merge, image lease, or image Publish has occurred while that owner is active. Only the mutable task PR and this separate local documentation draft were updated. No other owner's files or lease were cleared.

That owner subsequently completed normal verification and closed its lease. A fresh official status reported inactive before our merge and shared checkout mutation. The actual merge parents are `[6012ceb480bf9dc0f51ec6cc64c054e5e0db3846,c9c24270766eb59f9a0e42b84f5f38883af7fccf]`, and the actual production ref and merge tree were independently verified.

## Current operator publication

Shared `/app` fast-forwarded from J to M without reset. The original `yarn --cwd frontend build` dispatcher passed in65.92s, followed by explicit reviewed-intent frontend verification exit0 and isolated package verification exit0. SourceA/runtime matched exactly, tracked files were clean, and all120existing untracked files were exactly preserved with0added,0removed,0changed and0appended.

Original prepare succeeded for actor `codex-assembly-image-20261114`, prepared_at `2026-11-14T00:57:10.603194+00:00`. Both original prepublish steps passed, the second immediately before the one Publish click. Runtime/source/deployment identities match the table above. Full operator records are under `/tmp/assembly-images-d6f246-*`.

The predecessor platform version is `164bf90`. The platform now shows disabled Publishing and Deployment Started at03:58 UTC+03. Do not mutate shared `/app`, clear our lease, start another release or repeat Publish while this publication is active.

The exact next safe step is to observe a new explicit green completion and new published version, run the unchanged official `python scripts/production_release_guard.py verify --url https://mezansalla.com`, verify all3probes and matching source/runtime, then confirm official status inactive. Continue the read-only image acceptance and final app-link delivery afterward.

## Scoped rollback behavior

Rollback content has been prepared and tested by restoring only the two image files from J while preserving every other B-tree file. Its tested tree is `84eff2ab486cafb741a57dfe4d0a5998e1cfe367`. The original28tests pass with no failures/errors/skips, and explicit HTTP checks restore assembly-only403 while both review roles remain200, foreign-merchant assets404 and disabled grants403.

Rollback source A_R `d36163dd41b50c83cc7332c6ad0cea103ffd8173` now has sole parent actual M and exactly the tested rollback tree. Its clean candidate-source validator passed. Readiness43537354137 succeeded and produced artifact14090651318 (ZIP SHA256 `d1c370d3bbdbd2d6f3e559f4546ee5b51dbb4e111784de2211b705fded65dc92`). Root independently verified the external intent on clean A_R: SHA256 `6d0900368e1b05dc442316d6bd139adea89507d37771272845363c5733dfd090`, blob `32d99c7111982b233c1b8d2450708f066026d114`. Intent-only B_R `63a68eaea74c5f6a94c80fbe8415cbbbe027d1e9` is frozen on the dedicated rollback branch, tree `a92e136576c10d09f3bf6ccdba45759ffe1270ef`, sole parent A_R. Root independently verified the remote ref, parent/tree and intent-only delta; frozen-intent validation passed on a clean checkout. Its runtime is `rg5-8736b517cc71786f0209408062dd11d07bbb2da06f08ee447a39582de8075526`. Exact-B_R Readiness is being dispatched without the candidate-base input; it still needs its actual package gates and later isolated rehearsal before being called a deployable rollback. Never deploy the old forward intent with reverted runtime content or blindly redeploy an older production commit. No business data is reversed by this code restoration.

## Earlier Ready server acceptance

Ready PR1912 was published once. Emergent displayed a new green `Deployment finished` at14November2026 02:49 UTC+03 and platform version `4e63025`. The unchanged official verifier returned `verified:true`, `checks:3`, deployment `304e8793a15fd3269fd6f6d4cfba903d1760e285`, source `cdf175841376d2f68605d66c1979273d23aed70d`, runtime `rg5-3e41b3f0b14d180f45cd3b242798ea348797b06e8edc770592130ac87b2a9cb0`. Verify JSON SHA256: `0a91a8097d530c0027b34bf5d6150eb536722335f98af1884af00df5b258e2c8`. The matching lease closed and fresh status was inactive.

Read-only acceptance found the reported order's authoritative status is now completed. Its Ready button correctly remains blocked by the existing completed-order rule. A bounded29-order read also found ordinary positive and expected blocked cases; no live business operation was executed. The reviewed physical-reservation exception is covered by its regression suite, not a claimed live positive example. The saved operational image already loaded in web assembly with natural dimensions311×500.

## Combined Android application

[Mobile PR352](https://github.com/AMASI-SA/amasi-mobile/pull/352) preserves source `a667ad320eb1cb80b6e57e742eda58f84ec5bba4`, parent `c10fcd49adaaded57b69afdf3fc73dfc8d9df3bf`, tree `93be9b3ef5e3464640b4fcd9fdea9ea75b715f4c`. Version1.0.65 / Android80, package `com.amasi.sa.mobile`, existing signing/profile and OTA disabled.

It combines Ready ordering with the reviewed storage-only donor. Confirmed scan journals and verified committed invoice bodies compact; unresolved work, retry bodies and identities remain. Serialized writes/CAS protect newer attempts. SQLITE_FULL reclaims recognized safe history and retries the same local write once, without business-POST replay. PDF retention targets only recognized generated invoice files and protects active viewers.

All8 exact-source app workflows passed, including full TypeScript/lint, historical route-return checks, native Gradle tests and Android export. The one EAS build succeeded: <https://expo.dev/accounts/amasi-sa/projects/amasi/builds/c68427db-ebd7-4978-9a7e-27fd833bccda>. The build UI confirms exact a667, Android internal distribution,1.0.65(80), `frontend`, `mezan-web-assembly-pilot`, production environment and no automatic store submission. Build20m1s, total20m9s, start03:22 UTC+03. The normal authenticated Download build action returned the actual115538714-byte APK. Its official Android Build Tools36 inspection passed20/20 checks, including valid v2 signature, package/version, non-debuggable/test-only, OTAfalse/NEVER/0 and resolved runtime1.0.65. APK SHA256 `93730a8ca69481a8c49e6a4762a1cb32760065881e0f74c6dcdf6c15055397b2`; signer `ca6b376f14e01eadf611b0ea0208eab7c75576ac5ee51606d01df571f0d1ac54`, matching historical75/76. A fresh77 signer and physical-device upgrade have not been verified. Artifact reportSHA256 `723acac614e00be99a14c9d39b8acab3044ad3aa6f0460737cb343c4c915cdd4`. Do not submit a duplicate build.

[Documentation-only APK continuation](https://github.com/AMASI-SA/amasi-mobile/blob/0b89ada64efebfbb1c2c7d41fc6ce456a5564a70/docs/operations/ANDROID80-READY-RECEIVING-STORAGE-20261114/STATUS.md) preserves the completed artifact evidence. No employee-phone installation or device acceptance is claimed. The app build's mutable PR is the current continuation record; historical immutable STATUS files remain source checkpoints.

## Evidence boundaries and durable continuation

Production changed by the earlier Ready task: **YES**, verified above. Image publication is in progress; production acceptance is **pending**, not yet verified. No live order, stock, invoice, image, shipping, or financial data was modified for testing. No message was sent to employees. Issue1006 is at its comment limit; task PRs and dedicated status documents serve as the continuation record.

The platform exposes runtime logs, not a complete Cloud Build transcript. The observed green UI wording is `Deployment finished`; do not claim the older literal wording `Deployment Succeeded` or unobserved historic Cloud Build argv/cwd. Governed package tests and the unchanged live verifier provide the bounded release evidence.
