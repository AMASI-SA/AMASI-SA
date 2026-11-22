# CUSTOMER-INVENTORY-UNIFIED-20261122

## Current state — unified intent frozen; exact-B acceptance in progress

The owner requested publication code, supplied the original shared inactive-guard inspection, withdrew the misdirected slowness report, and asked what to execute next. Release preparation has resumed for the already approved customer refund/collection and inventory/purchase/receiving scopes. No additional source was supplied. The earlier publication deferral is retained in historical commits; this record is the current preparation handoff. No performance work or new performance gate is assigned to this task.

Unified PR: https://github.com/AMASI-SA/AMASI-SA/pull/2074
Branch: `codex/customer-inventory-unified-r2-20261122`.

| Identity | Exact value |
|---|---|
| Reviewed Production M/J | `06763ffd6e3c3f7c861769b61d2c4e6b9b2d5f35` |
| Frozen combined source A2 | `4e6c8a61bd1566e061a4f312e797ce239b582475` |
| A2 tree | `8f99772552fba3df9a14f55952648aa5d0ed5408` |
| Intent-only B | `447e83bfb95d2627877d46c7baa528e454a2e143` |
| B tree | `6a906db6cf4fd3dda4b550483a0ef96067c6f1ed` |
| Intent blob | `33ffe9f95b8093c1eeec33b2869708422358a7d0` |
| Runtime release | `rg5-2e0e3797d6ff3b955a3a23fd31487bd92721e70f316f3bdf34cab0d94c5d630f` |

B has A2 as its sole parent and changes only `release/release-intent-v5.json`. Its 1,492,319 bytes equal the original reviewed source candidate exactly (SHA256 `762a168ddd6bbe86afd29dd6ed8b5add13d33c426d2edc1fbf4d934c772326ab`). Empty full-history intent-path history M..A2 was verified. A2 and B must not be amended, rebased or squashed. Any additional source requires a new source identity and fresh candidate.

## Completed proof

All 17 exact-A2 source workflows passed. [EVIDENCE-4e6c8a61.json](EVIDENCE-4e6c8a61.json) retains the complete source-bound runs and artifact hashes. Operational backend: 6,502 +90 subtests; demand contracts:93; frontend:1,620 /103 suites; browser:33 (14+12+7); stock:5,525 +97 subtests; worker:4. Counts overlap and must not be summed. Two clean governed builds matched, and the original candidate validator passed outside a clean A2 checkout. Independent candidate revalidation also passed before B creation.

Exact-B [Release Readiness 45056863095](https://github.com/AMASI-SA/AMASI-SA/actions/runs/45056863095) succeeded, including actual clean-clone adapter rehearsals under Node20.20.2 and24.19.0, not merely dispatcher contracts. Both governed builds, original validators and package/JSON routing checks ran. The four retained output/evidence artifacts are under independent acceptance review. Other exact-B workflows still running at this checkpoint are not reported as passed.

Original B `load_release_intent` + `verify_release_intent_git` passed in a separate clean local B clone (exit0, clean after). The first attempt in the existing test workspace correctly rejected ignored prepackaged bytecode; the rerun used a new clone without deleting shared files or weakening validation. The missing generated identity diagnostic in a source-only clone was nonfatal; no generated identity was materialized to bypass a check.

## Scope and preserved accounting behavior

Complete inventory source J2..A3 (`5d09108e6d33ed95b7b2946cdb5a382a49aefae7`) and customer source (`4c308b19bb5d972bea5fff092bc0efbe9b70b29f`) remain integrated with the demand-read Production M changes. Standalone inventory B `9de88ea18b194d9d6b3e964642ae4e06e58a42e1` is excluded. PR2065 and PR2067 remain open/unmerged provenance and must not publish independently. Superseded PR2073 is closed without merge.

Confirmed customer payment remains recognized while refund/collection differences await confirmation. Confirmation moves the selected bank/provider once and applies the actual collection fee basis. Supplier obligation, physical receiving and payment stay separate. Prior-payment allocation creates no new cash debit or double reduction. Partial receipt retries remain idempotent, historical cash seals retain their evidence, and missing ancestry is not reconstructed. Required physical product options, stock valuation and current dispensing controls are preserved. Customer-order dispensing and unevidenced historical receiving remain outside scope.

## Host handoff — prepared, not executed

[MANUAL-HOST-ISOLATED-REHEARSAL-2074.py](MANUAL-HOST-ISOLATED-REHEARSAL-2074.py) is adapted from immutable PR2069 helper c01f238687e3193f096d55e6e0deb34fa8a57ec5. Its SHA256 is `b11576e193ecd80994a3127474433e50a75fa624eb78e22e4bbeca5e5e03c9d6` (7,340 bytes). Independent review and outer/child AST checks passed. Changes are limited to identities/ref/temp prefix and stronger explicit identity assertions.

The helper requires a fresh exact inactive original `/app` guard, clean M and unchanged remote M/B refs. It clones locally into a unique `/tmp` directory with no hardlinks, validates B with the original functions, and runs the official `cd frontend; yarn build` entry point only in that isolated clone. It observes the original package proof and rechecks shared state. It does not merge, alter shared outputs, prepare a lease or publish. A launcher must pin the evidence commit and helper SHA256. Do not execute until all exact-B applicable gates pass and artifact acceptance completes.

The owner's prior inspection observed `active:false`, clean shared M, inherited intent source063611ff163c90d80cdbf96446a28584fe429846 and `target_source_in_intent:false`. The inspection did not close a lease. These are past observations; the helper must recheck them live. PR2069's foreign lease is now recorded as verified and closed by its owner; preserve all later unrelated task work.

## Exact next safe action

Finish exact-B CI and retained artifact acceptance. Freeze and verify a pinned launcher in this separate evidence branch, update Issue1006's bounded task section, then hand off the single isolated host rehearsal command. On actual successful rehearsal and fresh unchanged Production/guard checks, the later authorized sequence is non-squash merge, safe shared update/materialization, original prepare/prepublish immediately before one Re-publish changes, a new explicit Deployment Succeeded, original three-probe verification of source/runtime/files/build/exact JSON MIME, and final active:false. No claim of shared-host or live success exists yet.

Production changed by this task: **NO**. No shared `/app` mutation, owned lease, merge, publication, live business test writes or paid Emergent chat. This evidence branch is documentation/handoff only; never deploy it. Canonical ledger: https://github.com/AMASI-SA/AMASI-SA/issues/1006#issuecomment-6238987123 .
