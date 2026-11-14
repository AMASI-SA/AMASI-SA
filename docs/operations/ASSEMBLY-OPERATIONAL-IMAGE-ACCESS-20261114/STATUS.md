# Operational images in mobile assembly — release continuation

## Scope and latest result

The user confirmed that operational-product images are saved and visible in waiting review, but disappear only in assembly and labeling. The image save flow is correct. The reviewed server repair permits an assembly-authorized mobile employee to read the already-saved image through the mobile permission bridge.

This is a documentation continuation of the immutable source checkpoint, not another deployment candidate. Source/intent identities must never be amended for a status update. The current image release is **not yet merged or deployed**. Its local, independent, exact-source CI and isolated Emergent rehearsal gates have passed. Another owner's production publication is in progress and its lease is preserved.

The earlier Ready server release was separately published and officially verified. The combined Android app includes Ready ordering and supplier-invoice storage recovery; one exact-source APK build is in progress. Final app distribution waits for image-server acceptance.

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

## Release coordination

Before image merge, the official shared guard showed another active owner:

- Actor: `codex-operational-source-diagnostics-1913`.
- Deployment: `6012ceb480bf9dc0f51ec6cc64c054e5e0db3846`.
- Runtime: `rg5-51a27cab964259844260959047effc778bbe1aa204c5da13133af9e0628fcaaa`.
- Emergent displayed Deployment Started14November2026 03:31 UTC+03.

No shared `/app` mutation, image merge, image lease, or image Publish has occurred while that owner is active. Only the mutable task PR and this separate local documentation draft were updated. No other owner's files or lease were cleared.

The exact next safe action is to observe normal closure, read a fresh official inactive guard and actual production ref, normal-merge immutable B, and verify the actual merge has parents J/B and tree exactly equal to B. Then preserve all unrelated files during a fast-forward-only shared checkout update, run the original governed adapter, prepare/prepublish, Publish once, observe a new explicit green platform completion/version, run the unchanged official three-probe verifier, and confirm inactive guard.

## Scoped rollback behavior

Rollback content has been prepared and tested by restoring only the two image files from J while preserving every other B-tree file. Its tested tree is `84eff2ab486cafb741a57dfe4d0a5998e1cfe367`. The original28tests pass with no failures/errors/skips, and explicit HTTP checks restore assembly-only403 while both review roles remain200, foreign-merchant assets404 and disabled grants403.

This is a tested behavior plan, **not a frozen deployable rollback**. A rollback source must parent the actual forward merge and receive a new v5 intent before any activation. Never deploy the old forward intent with reverted runtime content or blindly redeploy an older production commit. No business data is reversed by this code restoration.

## Earlier Ready server acceptance

Ready PR1912 was published once. Emergent displayed a new green `Deployment finished` at14November2026 02:49 UTC+03 and platform version `4e63025`. The unchanged official verifier returned `verified:true`, `checks:3`, deployment `304e8793a15fd3269fd6f6d4cfba903d1760e285`, source `cdf175841376d2f68605d66c1979273d23aed70d`, runtime `rg5-3e41b3f0b14d180f45cd3b242798ea348797b06e8edc770592130ac87b2a9cb0`. Verify JSON SHA256: `0a91a8097d530c0027b34bf5d6150eb536722335f98af1884af00df5b258e2c8`. The matching lease closed and fresh status was inactive.

Read-only acceptance found the reported order's authoritative status is now completed. Its Ready button correctly remains blocked by the existing completed-order rule. A bounded29-order read also found ordinary positive and expected blocked cases; no live business operation was executed. The reviewed physical-reservation exception is covered by its regression suite, not a claimed live positive example. The saved operational image already loaded in web assembly with natural dimensions311×500.

## Combined Android application

[Mobile PR352](https://github.com/AMASI-SA/amasi-mobile/pull/352) preserves source `a667ad320eb1cb80b6e57e742eda58f84ec5bba4`, parent `c10fcd49adaaded57b69afdf3fc73dfc8d9df3bf`, tree `93be9b3ef5e3464640b4fcd9fdea9ea75b715f4c`. Version1.0.65 / Android80, package `com.amasi.sa.mobile`, existing signing/profile and OTA disabled.

It combines Ready ordering with the reviewed storage-only donor. Confirmed scan journals and verified committed invoice bodies compact; unresolved work, retry bodies and identities remain. Serialized writes/CAS protect newer attempts. SQLITE_FULL reclaims recognized safe history and retries the same local write once, without business-POST replay. PDF retention targets only recognized generated invoice files and protects active viewers.

All8 exact-source app workflows passed, including full TypeScript/lint, historical route-return checks, native Gradle tests and Android export. One EAS build is in progress: <https://expo.dev/accounts/amasi-sa/projects/amasi/builds/c68427db-ebd7-4978-9a7e-27fd833bccda>. The build UI confirms exact a667, Android internal distribution,1.0.65(80), `frontend`, `mezan-web-assembly-pilot`, production environment and no automatic store submission. Do not submit a duplicate build.

The actual APK still needs completion and permitted artifact inspection. No employee-phone installation or device acceptance is claimed. The app build's mutable PR is the current continuation record; historical immutable STATUS files remain source checkpoints.

## Evidence boundaries and durable continuation

Production changed by the earlier Ready task: **YES**, verified above. Production changed by this image task at this checkpoint: **NO**. No live order, stock, invoice, image, shipping, or financial data was modified for testing. No message was sent to employees. Issue1006 is at its comment limit; task PRs and dedicated status documents serve as the continuation record.

The platform exposes runtime logs, not a complete Cloud Build transcript. The observed green UI wording is `Deployment finished`; do not claim the older literal wording `Deployment Succeeded` or unobserved historic Cloud Build argv/cwd. Governed package tests and the unchanged live verifier provide the bounded release evidence.
