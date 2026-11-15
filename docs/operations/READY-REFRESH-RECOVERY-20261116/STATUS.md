# Ready refresh recovery — publication evidence checkpoint

This branch is an evidence ledger only. **Do not merge it as application source.**

Updated: 2026-11-15 23:55:24 UTC

## Exact release

- Application PR: https://github.com/AMASI-SA/AMASI-SA/pull/1955
- Application branch: `codex/ready-refresh-recovery-release-20261116`
- Evidence branch: `codex/ready1955-publication-evidence-20261116`
- Source A3: `1accaa42e132ca1232ce86b3122971b48169ca81`, tree `be16191c9c2498a942fa7974455c6c24e6fac005`.
- Sole source parent/base J2: `2589f440106a7c5d463ee03eac3617d113319673`.
- Intent-only B/current application head: `dd4fc24dbef4b2d6e4abafac5ee5ee47d14e802b`, tree `aded5cb29b69b5bb09b7386932ad9fc5c35d1f44`.
- B has sole parent A3 and only `release/release-intent-v5.json` changed. Exact intent blob `bf8f2ac59543bbde3e1c73fa0a43a768ca9a7f22`, SHA256 `0d90b88def88e0c84d595376d050e80f3e18493bf9ca71284382af1af8754899`.
- Runtime identity: `rg5-255b2b13ed893324729b31c21409fa1be3be98d8c536485f712424d70bab4b4e`.
- Merge M: not created. Production changed by this task: **no**.

## Completed acceptance

A3 passed all 13 exact-head workflows. Review run `43775290295` / job `133000104970` executed 35 focused, 329 Android, 828 review/provider, 124 source-snapshot and 1 conflict cases; all zero-skip/failure/error gates passed. Both official candidate parsers accepted exact A3/J2. Root and an independent reviewer validated B with unchanged official source/intent loaders in a fresh clean clone.

B Readiness run `43776446819` passed, including actual Host20.20.2 job `133003993603` and Host24.19.0 job `133003993626`: fresh clone, no Git/no inherited generated inputs, actual adapter, package/identity validation and real HTTP JSON-MIME checks. Both downloaded evidence ZIPs matched GitHub's exact SHA256 and contained identical package proofs. Raw package proof SHA256 `325543cb75ef5de23df3886adca21ebebc8434b5255b6c1773cd3c25ebd6688a`; canonical JSON SHA256 `7f42de559eaccc3c8c9129017639ed5bffaae6d84600047ebf61a9fd295e6fdd`.

Evidence files under `evidence/` preserve the exact candidate, provenance, both candidate reviews, complete A3 CI receipt, B source validation, B readiness and package proof.

## Remaining B gate / preserved failure

Full B acceptance is **pending**. Operational Balance run `43776446776` attempt 1 / job `133003708822` failed only a pre-existing process-CPU ratio test at 4000 distinct issues; output equality passed. Suite: 4963 passed / 1 failed. Values: old CPU `0.2859268099999923`, new CPU `0.24774235800001065`, threshold new < old * 0.65. The exact failure and artifact are preserved in `ci-B-operational-attempt1-failure.json`.

The affected test/implementation is unchanged and A3 passed it. One isolated local test passed under Python3.12 (not equivalent to CI Python3.11). Root requested exactly one failed-job rerun on unchanged B; attempt 2 new Backend job is `133006178250`. Timing variance is plausible but not conclusively established. Do not bypass the gate or retry repeatedly; a repeated failure requires diagnosis. Review Completion B also remains under observation. Conditional inapplicable TikTok workflow skip must be distinguished from required executed-test zero-skip gates.

## Original rehearsal helper — reviewed, unexecuted

`evidence/original-rehearsal.py` is frozen at SHA256 `f795b6ee49cafbef56745e11e85f85ef476d9cd7eae529338ffc7f316a6298d7` (13880 bytes). Root and independent reviewer accepted the bounded plan after fixes for the absent optional scan root, early metadata-only configuration protection, and ignored-file collisions at newly tracked paths. AST passes; the helper has not been imported or executed.

It requires exact accepted M, current original J2/clean source, no lease, and preserves unrelated untracked inventory. Backend configuration is inspected only through directory metadata, never opened/hashed/copied; preservation claims are metadata-only for that sidecar. The helper uses the existing operation mutex, performs one ff-only handoff and official adapter build, then revalidates source/runtime/remote/preservation. It does not prepare, publish, verify/close a lease, or perform business writes. Failure after ff/build may leave original source advanced/generated artifacts changed; stop and review, with no automatic retry.

## Publication coordination / next safe action

PR #1951 owns a separately recorded active publication lease (actor `chatgpt-provider1951-20261116-root`, prepared `2026-11-15T23:45:16.349437+00:00`; owner evidence `e625bf05e4daa05dd6db4ec2bf8870e5f037fb3c`). Its completed live verification/handoff has not yet been accepted here. Do not touch original /app, merge into its live release path, reuse or clear its lease. Historical inactive observations are not a handoff.

Next: finish the bounded B rerun and all required B gates; independently verify the prior owner's completed handoff and current production Git still J2. Then normal-merge B preserving A3/B history, verify exact M tree/parents and push gates. Only afterward read original guard afresh, stage the immutable reviewed helper outside /app and execute once with exact M. Compare emitted package proof to the accepted canonical digest; then use official prepare/prepublish, one authorized platform publish, explicit Deployment Succeeded and official three-probe verify, ending with inactive lease and exact local/remote M.

The separately received garment remains blocked by source/approval representation proof, not stale workflow. This application patch does not clear that guard or mark Ready/receipt/service/provider state. Private order/customer evidence is excluded. Issue #1006 is full; PR #1955 and this record are continuation anchors.
