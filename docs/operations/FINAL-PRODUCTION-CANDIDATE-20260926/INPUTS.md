# Final Production candidate inputs and release boundary

This is the integration record for the owner's **NO-PREVIEW PRODUCTION CUTOVER** decision on 2026-09-26. Preview UAT is waived for this cutover and the Preview coordination review is paused. This record authorizes neither deployment nor financial activation.

## Frozen inputs

| Input | Commit | Tree |
| --- | --- | --- |
| Reviewed Production base J / rollback code target | `aa0eac73128dfcded4a1aaaa1217cb6c9d16810c` | Verified by the Git commit and its existing v5 intent |
| Accepted PR #1131 | `6f777f22c06fc9a7a96f131b7a8b3fd0aaf67a9c` | `a26d1f640f6812b3b8d275be0be8fd682600d485` |
| Accepted PR #1157 / G47 | `f2431282c3728a8d2fe618dbbe7a8616196f068d` | `79beaf467d25554ecc1c37b9d52dc330d6a624d9` |
| Common source baseline for transferring the accepted delta | `6365a042dfcb125e81e5e198ea1ff1537373ce51` | Fixed Git object |

The original PRs remain frozen, Draft/Open/Unmerged. This candidate uses a separate branch:
`codex/mz2-g47-final-production-candidate-20260926`.

The production base's source P is `1b3ac981b330fa538145707bd14d31b5d4d7931c`. P→J changes only `release/release-intent-v5.json`. Read-only Production health on 2026-09-26 reported this source, deterministic identity `rg5-90865e644b4bf68904b515efab7079fe0b2f8fabc8c56abe7ed9115d4e9612ae`, and all three identity/build verification flags true. This observation is not a new deployment or a substitute for rechecking immediately before any authorized rollout.

## Integration method and scope

The complete accepted source delta from the common baseline to frozen G47 is applied with Git three-way patch application onto J, excluding the historical release-intent file. The imported source lineage is listed in [SOURCE-COMMITS.txt](SOURCE-COMMITS.txt); these are provenance inputs, not a claim that their commit ancestry was merged into the production branch.

The input binary patch SHA-256 is:
`b28db399e5473f52fbb5acd66e59bc4dc52bab3446c066a776a5b687d74c4c16`.

The delta contains 211 files. Production has changed 42 files since the common baseline; only two paths overlap:

- `backend/preparation_piece_operations.py`: preserve Production's partial receiving, custody tracking and in-progress assembly behavior; layer the accepted G47 owner transaction, component consumption and idempotent-ready checks.
- `backend/supplier_receiving_routes.py`: preserve actual mobile employee identity and per-employee drafts; add the accepted explicit legacy-writer gate.

Before candidate-specific test/documentation integration, every non-overlapping incoming Git blob matched frozen G47, every production-only blob matched J, and the sealed V2 writer matched the accepted input exactly. Current Production Qoyod recovery/fencing, selected-piece receiving, PDF, preparation tracking and reverted-report decisions remain preserved.

Candidate-specific changes add no features: adapt the new partial-assembly unit fixture to its transactional body seam, add a real-Mongo partial-assembly/component-consumption regression, run the existing preparation-tracking unittest suite in Fulfillment CI, and record this provenance. The new integration case must prove no premature shipping and no duplicate component consumption.

## Required A/B release handoff

1. Finish and commit all source, tests, workflows and documentation as source A, descending from J. J's intent bytes must remain unchanged throughout J→A history.
2. Run the unchanged governed Release Readiness source-candidate workflow on exact A. Retain its two-clean-build reproducibility proof and candidate intent artifact.
3. Validate that artifact against A and J using the existing candidate-intent validator.
4. Commit B changing **only** `release/release-intent-v5.json`. Its `source_git_sha` remains A; B is the separate deployment candidate identity.
5. On exact B require actual passing MZ2 Accounting Module, G47 Focused Integration, Fulfillment V2, Security Gate, both CodeQL languages, frontend build and Release Readiness, including the Host Node 20 clean-clone adapter rehearsal.
6. Record A, B, tree, PR, full diff, build identity and final check evidence in Issue #1006, then stop for supervisor review.

No guard weakening, false GitHub environment markers, release-intent transplant, skipped-as-pass classification, production branch push or deployment is permitted. The workflow's production-push-only manual deployment handoff is not an executable pre-deployment validation job and is not counted as passed.

This input record is frozen before A's build. Final evidence belongs in the continuation ledger; modifying source or this document after the intent freeze would require a new A/build/B cycle.

## Code deployment and financial activation are separate

The later deployment phase is **CODE ONLY, WITH MZ2 ACCOUNTING WRITES DISABLED** and requires separate authorization. No action in candidate preparation changes live settings, posts financial entries, approves opening inventory or enables `v2_active`.

Source review establishes that V2 activation requires an explicit authorized transition; missing transition state defaults to legacy, and G47 reuses the V2/verified-opening gates. The existing MZ2 owner write pause must be explicitly verified before a later deployment permits those transactional paths to receive traffic: missing pause state is unpaused, not evidence of disabled writes. No live owner/pause/transition state has been established by this source-only record.

MZ2's pause is not a universal freeze for pre-existing legacy financial endpoints or Qoyod workers. Deployment review must state its actual operational scope and verify any necessary existing controls; it must not claim all financial activity is disabled merely from an MZ2 setting. No new bypass, financial writer, or activation policy is introduced here.

After a separately authorized code deployment, Production identity and required business data checks remain read-only. Financial activation waits for Production verification, opening data, G48/G49 and the owner's separate **FINAL CUTOVER AUTHORIZATION**, including final date/time approval.

## Rollback

The observed reviewed rollback code target is J and the release identity above. Before any deployment, rejecting this candidate changes no live state. After a future code-only rollout, use the existing release protocol to restore the reviewed target under separate authorization while retaining data and keeping new MZ2 writes paused. Once financial effects are later authorized, code rollback alone is not a financial reversal; preserve journals, receipts and operation identities and require an approved correction plan.
