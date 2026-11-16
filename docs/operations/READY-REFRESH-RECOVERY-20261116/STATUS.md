# Ready refresh recovery — publication evidence checkpoint

This branch is an evidence ledger only. **Do not merge it as application source.**

Updated: 2026-11-16 00:17:24 UTC

## Exact release

- Application PR: https://github.com/AMASI-SA/AMASI-SA/pull/1955
- Application branch: `codex/ready-refresh-recovery-release-20261116`
- Evidence branch: `codex/ready1955-publication-evidence-20261116`
- Source A3: `1accaa42e132ca1232ce86b3122971b48169ca81`, tree `be16191c9c2498a942fa7974455c6c24e6fac005`.
- Sole source parent/base J2: `2589f440106a7c5d463ee03eac3617d113319673`.
- Intent-only B/current application head: `dd4fc24dbef4b2d6e4abafac5ee5ee47d14e802b`, tree `aded5cb29b69b5bb09b7386932ad9fc5c35d1f44`.
- B has sole parent A3 and only `release/release-intent-v5.json` changed. Exact intent blob `bf8f2ac59543bbde3e1c73fa0a43a768ca9a7f22`, SHA256 `0d90b88def88e0c84d595376d050e80f3e18493bf9ca71284382af1af8754899`.
- Runtime identity: `rg5-255b2b13ed893324729b31c21409fa1be3be98d8c536485f712424d70bab4b4e`.
- Normal merge M: `b05cd3c9b25ce22df1b2dd06c23d97606bd632c6`, tree identical to B, ordered parents [J2, B]. GitHub production branch advanced to M. Live Production changed by this task: **no**.

## Completed acceptance

A3 passed all 13 exact-head workflows. Review run `43775290295` / job `133000104970` executed 35 focused, 329 Android, 828 review/provider, 124 source-snapshot and 1 conflict cases; all zero-skip/failure/error gates passed. Both official candidate parsers accepted exact A3/J2. Root and an independent reviewer validated B with unchanged official source/intent loaders in a fresh clean clone.

B Readiness run `43776446819` passed, including actual Host20.20.2 job `133003993603` and Host24.19.0 job `133003993626`: fresh clone, no Git/no inherited generated inputs, actual adapter, package/identity validation and real HTTP JSON-MIME checks. Both downloaded evidence ZIPs matched GitHub's exact SHA256 and contained identical package proofs. Raw package proof SHA256 `325543cb75ef5de23df3886adca21ebebc8434b5255b6c1773cd3c25ebd6688a`; canonical JSON SHA256 `7f42de559eaccc3c8c9129017639ed5bffaae6d84600047ebf61a9fd295e6fdd`.

Evidence files under `evidence/` preserve the exact candidate, provenance, both candidate reviews, complete A3 CI receipt, B source validation, B readiness and package proof.

## B complete acceptance and preserved first-attempt failure

All 13 required exact-B workflows passed. The separate TikTok Creator workflow is inapplicable to this branch by its exact source condition and is recorded as skipped, distinct from mandatory zero-skip test gates.

Review run `43776446789`, attempt 1 / job `133003708652`, executed the actual B tree against MongoDB 8.0.12: 35 focused (+35 subtests), 329 Android, 828 review/provider, 124 source-snapshot and 1 conflict cases, with no skipped, failed or errored test cases. Artifact `14186882656` has ZIP SHA256 `754212c37a007ed1713d236dad36a705701b687a0c24cf9b26a755b5137c5fac`.

Operational Balance run `43776446776` attempt 1 / job `133003708822` failed only a pre-existing process-CPU ratio test at 4000 distinct issues; output equality passed. Suite: 4963 passed / 1 failed. Values: old CPU `0.2859268099999923`, new CPU `0.24774235800001065`, threshold new < old * 0.65. The exact failure and artifact remain in `ci-B-operational-attempt1-failure.json`.

Root requested exactly one failed-job rerun on unchanged B after reviewing the source/logs and one isolated local diagnostic. Attempt 2 / job `133006178250` succeeded at `2026-11-15T23:58:21Z`: 4964 passed, then 16 passed +18 subtests, with the required no-skips/failures/errors gate passing. Artifact `14186977242` has ZIP SHA256 `2f3337727ab90a92d64d2524b7597f7fd7414ddad21e6cfc199f530b0c324517`. No source, test or threshold changed. The precise timing cause remains unproven; attempt 1 is not erased or conclusively labeled flaky. No further rerun was requested.

Complete B receipt: `evidence/ci-B-final.json`, SHA256 `94f0644693d8706d7c8cd51d56455aa56128cd64c2e16ccc992c8c60e9068ba5`. Root independently read the final run/job metadata and relevant successful job logs.

## Original rehearsal helper — reviewed, unexecuted

`evidence/original-rehearsal.py` is frozen at SHA256 `f795b6ee49cafbef56745e11e85f85ef476d9cd7eae529338ffc7f316a6298d7` (13880 bytes). Root and independent reviewer accepted the bounded plan after fixes for the absent optional scan root, early metadata-only configuration protection, and ignored-file collisions at newly tracked paths. AST passes; the helper has not been imported or executed.

It requires exact accepted M, current original J2/clean source, no lease, and preserves unrelated untracked inventory. Backend configuration is inspected only through directory metadata, never opened/hashed/copied; preservation claims are metadata-only for that sidecar. The helper uses the existing operation mutex, performs one ff-only handoff and official adapter build, then revalidates source/runtime/remote/preservation. It does not prepare, publish, verify/close a lease, or perform business writes. Failure after ff/build may leave original source advanced/generated artifacts changed; stop and review, with no automatic retry.

## Publication coordination / next safe action

PR #1951 completed its separately owned publication. Its immutable final receipt at evidence commit `bb06a2c63e9b56e3bd5e399143a5fc2ff00a5ff2` has SHA256 `1b1596a3c2f0632f2cd58d06e421237bdca2e51376f4c59f4b60d022095d5248`, `verified:true`, `checks:3`, `active_lease:false`, platform `c1784c4`, and exact J2 runtime. Root read this receipt and independently observed the official original guard return `active:false` at `2026-11-16T00:12:53.324590+00:00`. No lease was reused or cleared by this task.

Root then freshly confirmed production Git still J2, exact PR head B, no auto-merge and clean mergeability, and performed one normal PR #1955 merge. M is `b05cd3c9b25ce22df1b2dd06c23d97606bd632c6`; its exact tree equals B and ordered parents are [J2, B]. Root and an independent observer verified remote production=M. The original workspace remains expected at J2; no own original source handoff/build/lease/platform publish has run.

Exact-M push gates are running: Security `43779331347`, CodeQL `43779331479`, Readiness `43779331359`. Preserve source and intent unchanged. Next: accept all required M gates including actual Host20/24 package and HTTP proofs, then read original guard afresh, stage the immutable reviewed rehearsal helper outside /app and execute once with exact M. Compare emitted package proof to canonical digest `7f42de559eaccc3c8c9129017639ed5bffaae6d84600047ebf61a9fd295e6fdd`; then official prepare/prepublish, one authorized platform publish, explicit deployment completion and official three-probe verify, ending inactive with exact local/remote M.

The separately received garment remains blocked by source/approval representation proof, not stale workflow. This application patch does not clear that guard or mark Ready/receipt/service/provider state. Private order/customer evidence is excluded. Issue #1006 is full; PR #1955 and this record are continuation anchors.
