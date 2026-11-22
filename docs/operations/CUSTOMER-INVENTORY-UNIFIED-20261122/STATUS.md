# CUSTOMER-INVENTORY-UNIFIED-20261122

## Current state — PR2074 merged; complete manual deployment handoff ready

The owner requested publication code, supplied the original shared inactive-guard inspection, withdrew the misdirected slowness report, and asked what to execute next. Release preparation has resumed for the already approved customer refund/collection and inventory/purchase/receiving scopes. No additional source was supplied. The earlier publication deferral is retained in historical commits; this record is the current preparation handoff. No performance work or new performance gate is assigned to this task.

Unified PR: https://github.com/AMASI-SA/AMASI-SA/pull/2074 — merged with ordinary merge commit `24270ebdfdebb83282389308a2349cdb27e2b8e4`.
Branch: `codex/customer-inventory-unified-r2-20261122`.

| Identity | Exact value |
|---|---|
| Reviewed Production M/J | `06763ffd6e3c3f7c861769b61d2c4e6b9b2d5f35` |
| Frozen combined source A2 | `4e6c8a61bd1566e061a4f312e797ce239b582475` |
| A2 tree | `8f99772552fba3df9a14f55952648aa5d0ed5408` |
| Intent-only B | `447e83bfb95d2627877d46c7baa528e454a2e143` |
| B tree | `6a906db6cf4fd3dda4b550483a0ef96067c6f1ed` |
| Deployment merge D | `24270ebdfdebb83282389308a2349cdb27e2b8e4` |
| Intent blob | `33ffe9f95b8093c1eeec33b2869708422358a7d0` |
| Runtime release | `rg5-2e0e3797d6ff3b955a3a23fd31487bd92721e70f316f3bdf34cab0d94c5d630f` |

B has A2 as its sole parent and changes only `release/release-intent-v5.json`. Its 1,492,319 bytes equal the original reviewed source candidate exactly (SHA256 `762a168ddd6bbe86afd29dd6ed8b5add13d33c426d2edc1fbf4d934c772326ab`). Empty full-history intent-path history M..A2 was verified. A2 and B must not be amended, rebased or squashed. Any additional source requires a new source identity and fresh candidate.

## Completed proof

All 17 exact-A2 source workflows passed. [EVIDENCE-4e6c8a61.json](EVIDENCE-4e6c8a61.json) retains the complete source-bound runs and artifact hashes. Operational backend: 6,502 +90 subtests; demand contracts:93; frontend:1,620 /103 suites; browser:33 (14+12+7); stock:5,525 +97 subtests; worker:4. Counts overlap and must not be summed. Two clean governed builds matched, and the original candidate validator passed outside a clean A2 checkout. Independent candidate revalidation also passed before B creation.

Exact-B [Release Readiness 45056863095](https://github.com/AMASI-SA/AMASI-SA/actions/runs/45056863095) succeeded, including actual clean-clone adapter rehearsals under Node20.20.2 and24.19.0, not merely dispatcher contracts. Both governed builds, original validators and package/JSON routing checks ran. All four retained ZIP digests and both internal tar checksums were independently verified. Original artifact/proof/identity validators and fresh package-boundary checks passed for both outputs; freshly computed package proofs equal the CI artifacts and each other. All 17 applicable exact-B workflows completed successfully. The unrelated TikTok workflow and PR-only manual-publishing handoff were expected skips; neither actual adapter job was skipped.

Original B `load_release_intent` + `verify_release_intent_git` passed in a separate clean local B clone (exit0, clean after). The first attempt in the existing test workspace correctly rejected ignored prepackaged bytecode; the rerun used a new clone without deleting shared files or weakening validation. The missing generated identity diagnostic in a source-only clone was nonfatal; no generated identity was materialized to bypass a check.

## Exact-B acceptance records

- [EVIDENCE-447e83bf.json](EVIDENCE-447e83bf.json): all17 workflow identities and final states.
- [EXACT-B-NODE20-NODE24-ARTIFACT-REVIEW.json](EXACT-B-NODE20-NODE24-ARTIFACT-REVIEW.json): four artifact digests, original validation results, cross-host package equality and exact JSON MIME CI assertions.
- [EXACT-B-OPERATIONAL-ARTIFACT-REVIEW.json](EXACT-B-OPERATIONAL-ARTIFACT-REVIEW.json): real-Mongo XML, frontend/worker/browser results and source-bound artifact integrity.
- Independent helper and launcher reviews plus original clean-B validation are retained alongside these files.

Exact-B result counts: backend6502 +90 subtests, demand93, shipping16 +18subtests, stock5525 +97subtests, frontend1620/103suites, worker4, browser33=14+12+7. All three unified regressions executed; no failures/errors/skips. Seven operational/stock artifact ZIP digests match GitHub metadata. Inventory58, simple29 and operational23 source hashes match exact B. Fresh browser JSON/source acceptance is separate from the prior unchanged-A2 visual review. Counts overlap.

These records establish CI and local artifact acceptance only. They do not assert that the unified source is staged in `/app` or running on Production.

## Scope and preserved accounting behavior

Complete inventory source J2..A3 (`5d09108e6d33ed95b7b2946cdb5a382a49aefae7`) and customer source (`4c308b19bb5d972bea5fff092bc0efbe9b70b29f`) remain integrated with the demand-read Production M changes. Standalone inventory B `9de88ea18b194d9d6b3e964642ae4e06e58a42e1` is excluded. PR2065 and PR2067 remain open/unmerged provenance and must not publish independently. Superseded PR2073 is closed without merge.

Confirmed customer payment remains recognized while refund/collection differences await confirmation. Confirmation moves the selected bank/provider once and applies the actual collection fee basis. Supplier obligation, physical receiving and payment stay separate. Prior-payment allocation creates no new cash debit or double reduction. Partial receipt retries remain idempotent, historical cash seals retain their evidence, and missing ancestry is not reconstructed. Required physical product options, stock valuation and current dispensing controls are preserved. Customer-order dispensing and unevidenced historical receiving remain outside scope.

## Host rehearsal — passed in owner's actual Emergent terminal

[MANUAL-HOST-ISOLATED-REHEARSAL-2074.py](MANUAL-HOST-ISOLATED-REHEARSAL-2074.py) is adapted from immutable PR2069 helper c01f238687e3193f096d55e6e0deb34fa8a57ec5. Its SHA256 is `b11576e193ecd80994a3127474433e50a75fa624eb78e22e4bbeca5e5e03c9d6` (7,340 bytes). Independent review and outer/child AST checks passed. Changes are limited to identities/ref/temp prefix and stronger explicit identity assertions.

The helper requires a fresh exact inactive original `/app` guard, clean M and unchanged remote M/B refs. It clones locally into a unique `/tmp` directory with no hardlinks, validates B with the original functions, and runs the official `cd frontend; yarn build` entry point only in that isolated clone. It observes the original package proof and rechecks shared state. It does not merge, alter shared outputs, prepare a lease or publish. The reviewed [RUN-HOST-REHEARSAL-2074.sh](RUN-HOST-REHEARSAL-2074.sh) launcher pins helper evidence commit `916e5f5f37e1651e2379174c1afa96e2a0204202` and the helper SHA256. Launcher SHA256 is `3a8112a283672cb62b99d560c90e4b9a744533e32a7992fb2bc8abeaa94fb3de` (2,703 bytes). Independent shell/Python syntax and source review passed; remote pinned helper bytes were reread and matched. The owner executed this exact pinned launcher in the actual Emergent /app terminal and returned a complete successful transcript. Original intent validation bound A2/runtime, ADAPTER_EXIT was0, package_boundaries_verified wastrue, the artifact tree matched, and all three displayed original shared-guard checks were inactive. This is owner-supplied actual host evidence, not a local simulation. Source/output staging, lease and publication were not performed by the rehearsal.

The owner's prior inspection observed `active:false`, clean shared M, inherited intent source063611ff163c90d80cdbf96446a28584fe429846 and `target_source_in_intent:false`. The inspection did not close a lease. These are past observations; the helper must recheck them live. PR2069's foreign lease is now recorded as verified and closed by its owner; preserve all later unrelated task work.

## Exact next safe action

The actual isolated host rehearsal is complete; retain `/tmp/mezan-pr2074-icmywyrd` and its adapter.log. [HOST-REHEARSAL-PASS-20261122.json](HOST-REHEARSAL-PASS-20261122.json) records the supplied result. Fresh GitHub reads confirmed reviewed Production M and clean/mergeable PR2074 at B with all17 applicable gates passed. The authorized ordinary merge is complete at D `24270ebdfdebb83282389308a2349cdb27e2b8e4`. Its exact tree equals B and its ordered parents are M/B; remote Production points to D. [NORMAL-MERGE-2074.json](NORMAL-MERGE-2074.json) records this verified result. The reviewed staging helper will require fresh inactive guard, clean old M, unchanged remote D, original retained adapter.log proof and exact generated outputs. It performs a guarded fast-forward and verified replacement of only the three generated outputs, with immutable backups and unchanged unrelated files/settings/dependency root. It accepts an absent backend bytecode cache without creating one; any existing regular .pyc-only cache is preserved. Original artifact/intent/source/frontend validation is repeated after staging. The original merge Git validator also passed at D in a clean clone (exit0, exact tree/parents, only intent diff after A2, empty source-intent history).

A separate reviewed helper is intended to run in a fresh Python child only after staging succeeds. It invokes the unchanged original guard prepare and prepublish, binding actor `codex-customer-inventory-unified-2074-20261122` and exact D/A2/base/runtime/protocol. Open Manage Publishing first; execute the pinned launcher; only after PREPUBLISH_PASSED click Re-publish changes exactly once immediately. Require a new explicit Deployment Succeeded, then the original three-probe verification with exact source/runtime/files/build/JSON MIME and final active:false. Any STOP preserves evidence and the current lease; no automatic retry, reset, cleanup or rollback. Actual isolated host success is now recorded; live unified Production success is still unverified.

## Reviewed host files

- `MANUAL-HOST-STAGE-2074.py`: SHA256 `e52f11663f943d9a4ab24f917b9ccd4a83ea8526cf1dd471867029c3fda3adde` (21483 bytes). AST, bounded filesystem/self-tests and independent source review passed. Eighteen original utility/check function bodies are unchanged from the immutable PR2069 precedent. No operational main was executed locally.
- `MANUAL-HOST-PREPARE-PREPUBLISH-2074.py`: SHA256 `a02f10c61751c439e38e81880aaa555bbe43ad7bc9a2906c8f6ab334d91c0bca` (4410 bytes). AST and independent source/API/schema review passed. No guard operation was executed locally. A fresh process avoids stale pre-staging module imports.
- `ORIGINAL-MERGE-VALIDATION.json` records clean-D original validation. Independent helper review files are retained alongside the scripts.

The final [RUN-HOST-DEPLOY-2074.sh](RUN-HOST-DEPLOY-2074.sh) launcher is pinned and independently reviewed: SHA256 `fa596f80844cb3e272f2819b6ca52686aca1e994fe41da9af2ea4fa2d4222958`,3679bytes. It pins helpers bundle `afdbd4e293760938706849232a15f0afe6fa1edf`, validates all three helper hashes before operational execution, and runs only staging then the original prepare/prepublish gate in separate fresh Python children. All three remote files were independently reread and match accepted bytes. No new source/build identity is created.

The launcher also saves the reviewed [RUN-HOST-VERIFY-2074.sh](RUN-HOST-VERIFY-2074.sh), SHA256 `e23d910b9c03ef8e979a7f78cc4cc8947e640ced03efdce35953c1efd88e132e`,4821bytes, in its unique /tmp directory with mode0400. It prints an exact `VERIFY_COMMAND` after the gate succeeds. It never executes verification automatically. Run that command only after a NEW explicit Deployment Succeeded. The wrapper preserves the original operation lock and unchanged original three-probe verification core, adding an exact-own-lease fence inside the same lock. This prevents a foreign lease being substituted between a separate status and verification call. It requires complete original JSON, verified:true/checks3/exact IDs/artifact, original lease closure and final original status exactlyactive:false.

Both launchers and the verifier passed shell/Python syntax and independent source reviews. These are prepared commands, not evidence that staging, lease or publication has occurred. Do not replay the already completed isolated rehearsal. Open Manage Publishing first, execute the deployment launcher once, click Re-publish changes once only after PREPUBLISH_PASSED, wait for the new success, then execute VERIFY_COMMAND and return its output. Stop on any failure and preserve recovery directories and the current lease.

Production Git branch changed by this task: **YES**, ordinary merge D above. Live runtime publication: **NO**. No shared `/app` source/output mutation, owned lease, publication, live business test writes or paid Emergent chat. This evidence branch is documentation/handoff only; never deploy it. Canonical ledger: https://github.com/AMASI-SA/AMASI-SA/issues/1006#issuecomment-6238987123 .
