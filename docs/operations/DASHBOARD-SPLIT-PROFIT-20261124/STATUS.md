# Dashboard split-payment profit correction — current integration

## Current source checkpoint

- Task: `DASHBOARD-SPLIT-PROFIT-20261124`, repository `AMASI-SA/AMASI-SA`.
- Current source branch: `codex/dashboard-split-profit-current-20261125`.
- Production target: `hotfix/prod-snap-meta-final`.
- Reviewed integration base J4: `30c970807c05e291e2f48b35c84155b271fb5f09` (PR #2128).
- This source A4 has J4 as its sole parent. Its exact enclosing SHA is read from the remote branch/PR; it is not embedded in its own source document.
- All ten application, test and workflow blobs are copied exactly from reviewed source A3 `10bb33957c8c2994814a76ba5feed1182336ebda`. This updated status document is the eleventh changed path. Current inherited intent blob `ec72ea9c139cd2d69e2de553320bf9ecd7439def` is unchanged.
- User authority remains explicit upload, merge and publication: "نعم ارفع وادمح وانشر", renewed by "كمل" on 25 November 2026. No additional confirmation is required for this authorized release.
- Current milestone is a **source checkpoint**, not a reviewed intent B or completed publication. Fresh integrated tests, governed build and release verification remain required.

## Confirmed defect and corrected behavior

The executive dashboard withheld net profit with `provider_split_payment_review_required` for two supported native, fully paid SAR payment shapes: an external card plus internal customer wallet, and distinct mada captures. The existing generic hint made the split-evidence gap resemble a missing commission configuration.

The correction uses current, owner-bound native payment evidence and the configured capture tariff only when it remains valid throughout the possible payment interval. It does not invent a capture date. Each distinct external capture receives its fixed fee once; identical references are not charged twice. Imported actual fees and statement evidence retain priority over configured estimates.

Internal-wallet redemption stays in sales and profit and is excluded exactly once from electronic principal and expected transfer. Unknown wallet evidence can withhold cash-like totals independently of known actual fees. Ambiguous, refunded, cancelled, foreign-currency, stale, incomplete and conflicting evidence remains guarded. Cross-order reference checks use the already selected owner cohort, remain bounded and yield through the existing CPU budget. The frontend gives a specific Arabic split-matching hint and continues hiding unknown or stale profit.

No tariff, settings, source status, sync, backfill, accounting posting or real transaction was changed for this correction. Customer data and real payment references are excluded from committed fixtures and evidence. This dashboard correction does not claim to repair the separate operational-profit snapshot calculation.

## Compatibility with current production

Between old base `f199a471f8bc6e05b475bb9b65a12107cbdc946b` and J4, 25 production paths changed. None overlaps the eleven dashboard source/status paths. Complete untruncated Git trees show that all seven modified base blobs still match the old base exactly, while the four added paths remain absent. New operational cost projection, bounded refresh diagnostics, employee progress/tracking/salary changes, their tests and the current release intent are preserved.

Independent source review found no new conflict. The affected product-cost builder differs only by its named timing wrappers and local import; dashboard fee-evidence hydration remains intact. This source comparison is not a substitute for fresh integrated tests. AGENTS.md, the v5 protocol, release workflow and core release controls are byte-identical to the previously reviewed controls.

## Preserved history and evidence

- Historical PR #2121 and original source branch `codex/dashboard-split-payment-profit-20261124` remain preserved.
- Historical PR #2122 and branch `codex/dashboard-split-profit-release-20261125` preserve A3 and frozen B `c3fd1d1a4e3e7fc590b1f5b82d1bf0a091c54bf7`. They must not be merged or deployed over J4. No squash, rebase, force push, intent conflict resolution or hash substitution is used.
- Historical complete status, reviews, host operators and checks remain on the evidence history. The restored continuation is [E8 `2c6041d31c0d2dc1343885fecbe64b9e35dd4e4d`](https://github.com/AMASI-SA/AMASI-SA/blob/2c6041d31c0d2dc1343885fecbe64b9e35dd4e4d/docs/operations/DASHBOARD-SPLIT-PROFIT-20261124/STATUS.md), preserved at `codex/dashboard-split-profit-evidence-r2-20261125`.
- E8 restores the complete E6 STATUS plus the unchanged E7 appendix after E7 accidentally lost its prefix. The original evidence branch remained at E7 because two expected-head ref updates returned GitHub GraphQL errors; E8 was safely preserved on a new branch and read back. Other evidence artifacts and all historical commits remain intact.
- Old operator v3 binds old A/J/B/tree/release and historical process identities. It must not be executed unchanged for A4. Any new operator/envelope requires exact new identities, fresh host observation and independent review.

## Verification already completed on preserved source

These are historical results, not fresh A4 acceptance:

- 300 focused backend tests and 189 dashboard frontend tests passed, including 45 split-fee regressions, four cash-basis cases and seven unchanged legacy same-rail cases. Suites overlap later CI.
- A3 corrected one exact collector-context expectation without weakening the exact dictionary, query-count, privacy or nonmutation assertions. The full four-platform backend selection passed 449 tests and seven subtests.
- Old B completed 36 successful checks and ten legitimate conditional skips, with no failure or pending check. Both actual Host Node 20.20.2/24.19.0 clean-clone adapter jobs passed. The old intent packet, source manifests, artifact bytes, critical hashes and reproducibility proof were independently verified.
- Old builds, intents and those runtime IDs cannot be reused for this new source/base.

Synthetic acceptance remains: sales 706.99, product cost 20, configured fee estimate 13.21, profit 673.78, internal wallet 33.01, electronic principal 673.98 and expected transfer 660.77. These are synthetic fixtures, never a current live-profit claim.

## Shared publication state observed on resumption

Root read PR #2126's original receipts from the existing Emergent terminal. Its unchanged original verifier returned exit 0, verified true and three probes; its matching lease closed, followed by original status active false at `2026-11-25T15:34:11.304640+00:00`. Source was `6d4aa546259e9cad1cc379d4a464e245af6cfa91`, deployment `f3d8ee39e60ed087f0a4d6debf844d128e92a758`, provider revision `97dc193`. This is another release's observed completion, not publication of this fix.

PR #2128 subsequently merged as J4 and claimed the serial shared-host publication handoff. At this source checkpoint, its publication and final original verifier are pending. Root must re-read that publisher's completion and the actual original guard before using the shared host. Do not clear, reuse or duplicate another owner's lease/verifier, or change `/app` during its release operation.

## Required next actions

1. Verify remote A4 has sole parent J4, the exact ten inherited source blobs, only the eleven intended paths, and no intent edit anywhere in J4-to-A4 history. Run the original candidate-transition validator.
2. Run fresh affected backend/frontend checks, including existing four-platform source-parity tests and inherited refresh-diagnostic regressions. Require exact-source CI and the original Node 22.23.2/Yarn 1.22.22 two-pass governed build with public origin `https://mezansalla.com`.
3. Independently validate the new candidate intent against exact A4/J4. Create B changing only `release/release-intent-v5.json`; require its exact-head CI and Node 20/24 adapter rehearsals. Do not alter frozen A/B history.
4. Confirm current production still equals J4 and the current publisher has completed its original verification/closed lease. Normal merge must preserve A/B ancestry and produce D with B's exact tree. Require exact-D push gates.
5. Use the reviewed new operator and envelope for an actual owned clean-clone and canonical `/app` adapter rehearsal, preserving unrelated files. Only then use original prepare, immediately-prepublish, one authorized publication and a newer explicit success.
6. Run the unchanged original production verifier once and require its three matching probes plus original status active false. Then refresh the affected dashboard period through the normal demand/read-only UI and confirm payment completeness and numeric net profit. Preserve genuine independent completeness gates.

Root owns source adoption, intent freeze, PR metadata, merge, actual-host work and publication. Post-freeze evidence belongs on a dedicated evidence branch, never on the frozen source. Production changed by this task: **no** at this checkpoint. Live net-profit acceptance remains pending.
