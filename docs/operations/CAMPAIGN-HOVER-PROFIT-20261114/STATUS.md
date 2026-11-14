# Campaign hover costs, operating allocation and profit

Task: `CAMPAIGN-HOVER-PROFIT-20261114`

Repository: `AMASI-SA/AMASI-SA`

Task branch: `codex/campaign-hover-profit-20261114`

Reviewed production base J: `4c623c640fb63ac3c138fa4a9cabb20e2b39dca7` (PR #1919).
The initial work began on PR #1918 merge `23cc06da003c18c0f3fc6c2e749aad1326666166`
and was fast-forwarded to J before creating a source checkpoint. None of PR #1919's
source paths overlap this feature. The latest exact task SHA is recorded in the
task PR because this document cannot contain its own commit identity.

## User contract

Add three rows to the existing campaign card:

1. Direct total costs: the same product/component/selected-option, shipping,
   payment, and attributable advertising bank-fee authorities as the executive
   profit summary. Advertising spend and allocated operating expenses are not
   included in this subtotal.
2. Operating expenses: today's executive operating total divided by today's
   executive gross SAR sales, multiplied by the campaign's existing sales. The
   ratio is dynamic, not a fixed 3% or the screenshot's rounded 3.09%. Retain the
   full ratio until rounding the campaign amount to halalas. Display both the
   allocated amount and percentage, with the store-day basis/date visible.
3. Net profit: campaign sales minus campaign ad spend, direct costs and allocated
   operating expenses. Keep losses negative. Incomplete costs must show
   `التكلفة غير مكتملة`; incomplete profit must not display a numerical result.

The latest user instruction includes proportional operating expenses and
supersedes the earlier request to exclude them. The reference executive period
is today's store day in `Asia/Riyadh`; campaign commerce/spend retain their
existing advertising-account day. No saved allocation or accounting write is
performed. Existing campaign scope, counts, time windows, source matching, ad
spend, CPA, ROAS, cache and refresh behavior are preserved.

## Implementation

- `backend/order_engine/campaign_hover.py` retains the exact qualifying campaign
  order cohort and includes the raw/promoted fields needed for cost verification.
- `backend/order_engine/campaign_hover_profit.py` reuses the read-only cost
  authority, bounded catalog context, current option evidence, shipping/payment
  calculator and campaign-specific advertising bank-fee policy. One distinct
  order cohort is priced once for the visible campaign batch.
- `backend/order_engine/campaign_hover_operating.py` reads the executive status
  cohort and inferred-date settings; uses shared SAR conversion, Employee OS
  salary history/leave rules and V2 recurring obligations. Legacy rent/prepaid
  totals replaced by V2 in the executive summary are not added twice.
- Finite time, row and line limits fail closed. Missing product price, unmatched
  current lines/options/variants/parents, absent currency conversion, ambiguous
  bank-fee account identity, unresolved refunds or unavailable operating basis
  cannot certify net profit. Valid Salla base cost fallback remains permitted by
  the executive completeness rule.
- `CampaignHoverBadge.jsx` shows the three rows, provisional-spend warning and
  operating basis. Measured height, viewport bounds and pinned keyboard/pointer
  scrolling keep the expanded card usable. No new fetch, polling or sync path.
- Dedicated backend/frontend acceptance tests cover actual shared authorities,
  cost gaps, currency, fee normalization, refunds, proportional allocation,
  losses, current-versus-promoted identity and viewport interaction. The existing
  campaign acceptance workflow includes these suites and their dependencies.

## Independent review and local verification

Independent backend review checked executive operating parity, isolation,
read-only behavior and missing-data handling. Its findings about native line
coverage, Meta `act_` account aliases and work admission before copying were
fixed. The final identity extension additionally checks nested variants,
variant removal, parent identity and conflicting catalog aliases. Independent
review confirmed both final identity findings resolved, with no remaining
blocker in the reviewed financial scope.

Local frontend acceptance: 9 suites, 50 tests passed, exit 0. Existing React
test-environment/deprecation warnings remain; no frontend test failed.

Final integrated backend acceptance: **188 passed, exit 0**, across 12 suites
in 14.22 seconds. The new cost and operating suites account for 51 passing cases.
Two existing Pydantic deprecation warnings remain. An initial run found the old
query budget predating the financial sources; the test now exercises complete
successful calculations and proves the identical exact 30-read map for both 1
and 20 distinct campaigns. It checks each cost/operating source explicitly, with
the two intended commerce/executive order reads and separate settings reads.
The full existing daily suite also passed independently (28 cases).

The 12 backend suites are the exact list in
`.github/workflows/campaign-hover-source-acceptance.yml`, executed from `backend`
with `python -B -m pytest --noconftest -p no:cacheprovider -q --tb=short` and
`PYTHONDONTWRITEBYTECODE=1` plus a synthetic test-only JWT secret. The 9 frontend
suites are that workflow's exact `--runTestsByPath` list, run with `CI=true` using
`python scripts/frontend_release_toolchain.py exec -- yarn --cwd frontend test
--watchAll=false --runInBand --runTestsByPath ...`. `git diff --check` passed.

Local commands use the governed Node 22.23.2/Yarn 1.22.22 toolchain for frontend,
and an existing Python environment read-only for focused offline pytest. Exact
source CI, real-Mongo acceptance, full frontend/security gates and v5 clean-clone
rehearsals remain mandatory; mock-backed local tests do not replace them.

## Release and continuation

Production changed by this task: **no**. No `/app` mutation, lease or publish has
started for this feature. PR #1919 has its own independent release operation;
read the shared guard and current production branch before any future `/app`
operation. Preserve its lease and unrelated workspace/editor state.

No order, payment, settlement, product configuration, ad account, advertising
budget/bid, provider sync, scheduled refresh or business-data mutation is part
of this task. Paid Emergent chat is not used.

Issue #1006 has reached its comment limit. This document and the dedicated task
PR are the continuation ledger; do not repeatedly try to append there.

First source checkpoint: `2c26dea8487bf6779a6eb01a65771f175e83b0f4`, PR #1920.
Its governed candidate built and independently validated, but two CI jobs found
the new shared financial test fixtures require `mongomock-motor`, which their
old install commands did not include. The campaign and Snapchat workflows now
install the repository's existing pinned mock packages (`mongomock==4.3.0`,
`mongomock-motor==0.0.36`). No test was skipped, removed or weakened; no production
dependency or runtime source changed for this correction. The first candidate
must not be used after this new source checkpoint; a fresh exact-source build is
required. Other CI results remain evidence for their precise source only.

Next safe action: push the CI dependency correction as the new source A on PR
#1920, then wait for all applicable exact-A gates. Independently validate
the governed candidate intent, create intent-only B, pass exact-B gates, ordinary
merge with B's exact tree, and follow the authorized v5 deployment protocol. Do
not deploy this feature using the inherited PR #1919 intent.


## Release checkpoint — 2026-11-14 14:53:39 UTC

Current immutable source A: `2cf4d70d7e0c23c11048cad5c610e8c0c60d7eeb`, tree `b18db6ce619970c2a5c11c86e332fd21199b6dd3`; PR #1920. The revised source's campaign and Snapchat acceptance, security, CodeQL, source-readiness, operational web, shipping and integration gates have passed. Stock eligibility remains running at this checkpoint. Operational backend artifact verifies 4,672 testcases with no failures/errors/skips; official frontend verifies 733 tests/51 suites, browser 9/9, worker 4/4. Campaign suites are independently accepted by their own workflow; they use offline read-only mocks, not the operational workflow's real Mongo fixture.

The current independently verified candidate is from readiness run 43606645216, artifact 14118910376; SHA-256 `9980404a8cd44b0b9a687923202e428bbb2ccec7482e0c4a411ffe75fd68d02b`, intended runtime `rg5-5c3980b0f83f3859023070ef846486e877849ca7ca848367a181995ca32c1d5e`. The complete manifest comparison is retained in `verification/source-a-candidate-validation.json`.

Read-only original `/app` preflight observed the separate active lease `codex-financial-loading-recovery-1919`, source `5f7fce4fcf1b81f3fbe1093d0983184c281ca733`, deployment `4c623c640fb63ac3c138fa4a9cabb20e2b39dca7`, runtime `rg5-9b6621b12a348a9d27542452b3d2040456e720478b9fd0dc3a2b82af27438605`; tracked worktree was clean. No mutation, release, verification or lease closure for that owner was attempted. Do not advance the production ref or original workspace while that release is in progress.

Next: finish exact-A stock gate, freeze validated intent-only B, pass exact-B gates/rehearsals. Wait for the existing owner to close its lease; fresh guard status must be inactive before merging our release and changing `/app`. Production changed by this task: **no**.

This handoff branch is documentation-only and excluded from frozen A/B release ancestry.


## Reviewed intent B — 2026-11-14 14:59:53 UTC

All 12 exact-A workflows passed, including stock eligibility. Official A artifacts verify 4,672 operational backend cases and 4,481 eligibility cases with zero failures/errors/skips. Their retained summaries and provenance are in `verification/source-a-ci-readout.md` and `verification/source-a-ci-metadata.json`.

Intent-only B is `dcb97791d6ef8ad394575542eb4048c6c867bbba`, tree `14dfe135d6633f72c92cb1cb359f4b9933ac176a`, direct parent A `2cf4d70d7e0c23c11048cad5c610e8c0c60d7eeb`. Root verified the sole changed path and exact candidate bytes. Independent official B loader, Git relation and frontend-manifest checks passed. Exact-B workflows are running, including both Host20/24 clean-clone adapter rehearsals in run43607551229.

The other owner’s durable handoff reports authorized PR1919 publication started after its prepublish at14:52:07UTC and one click observed14:53:16UTC. Do not repeat or interfere with that action. Await its owner’s verification and inactive shared guard before our production merge/workspace changes. Production changed by this task: **no**.


## Exact B fully accepted — 2026-11-14 15:09:05 UTC

Every applicable exact-B workflow passed. The sole skipped TikTok candidate job has an explicit dedicated-branch condition and is not applicable to this branch; no applicable test gate was waived. Independent official B artifacts reconfirm 4,672 operational and 4,481 eligibility cases with zero failures/errors/skips, plus frontend733/51, browser9/9 and worker4/4. Host20.20.2 and24.19.0 generated293 identical files; official readers validated them against the intent, including both isolated package digests. Reports are in `verification/intent-b-*.json` and `intent-b-ci-readout.md`.

Live management UI shows the other owner's newly finished deployment a5eae68 (14November,18:02Asia/Riyadh). Its guard lease has not yet been observed inactive; the latest read-only status command is waiting behind the guard operation lock. Do not interrupt or close the foreign release, advance Production, or modify `/app` until its owner completes verification.

The narrow `release-runners/original-build.py` is prepared for the eventual exact mergeM. It pins J/A/Btree/merge parents; requires inactive guard; preserves all unrelated nonignored untracked files including bytes, modes, inodes and timestamps; executes the official adapter and package proof with actual exit records; and creates no lease or publication. SHA256 `14e3df7d4e5b010707f00f294801eec259a5b4da24adb973854bf242763b38cb`. Independent static review is pending; the runner has not been executed.

Production changed by this task: **no**. Next action remains the shared-lease coordination check, then ordinary exact-tree merge and the reviewed original build/own release sequence.


## Continuation after environment interruption — original runner reviewed

Recorded at 2026-11-14 15:23:07 UTC. The local execution environment and browser were restored, but the old scratch checkout was lost. Frozen source A, intent-only B, PR1920 and production J are unchanged on GitHub. The user restored the Emergent login; root visibly verified the signed-in Salla Analytics project. No merge, /app mutation, lease preparation or publication for PR1920 has occurred.

The previously saved original-build runner (SHA256 14e3df7d4e5b010707f00f294801eec259a5b4da24adb973854bf242763b38cb) is superseded. Independent review found a gap where a concurrent prepare could occur after an inactive check. The corrected runner holds the official operation mutex through the fresh inactive assertion, preservation snapshot, fetch/fast-forward, original governed build, package/identity verification and final inactive/preservation checks. It creates no lease during rehearsal. The pre-lock CLI status remains outside; direct lease checks inside avoid recursive-lock deadlock. Independent review traced both build/package paths and found no same-lock reentry.

The corrected bytes were recovered exactly from the durable baseline after the outage. Reviewed and restored SHA256: `7019b1b7f3fdc6bad77495cac3719c6c56f746d4afeb6239c9276c5ff7d915b0`, 5367 bytes; AST parse passed. This runner has NOT been executed. Never execute the superseded runner.

Other release handoff now points to `42ee1e5a26b0fba5bcb97aff70f2a2fb2433a684`. It records completed publication `a5eae68`, but not the official verifier's final result or lease closure. Read the existing evidence `/tmp/mezan-pr1919-original-72x801fd/28-verify` first after reconnecting; do not repeat its verify, prepare or publication, and do not clear its lease. A fresh inactive guard remains required before our merge/rehearsal. All A/B applicable CI acceptance already passed; no code changes or additional test reruns are needed.


## Prior lease closed; ordinary merge M verified

Recorded at 2026-11-14 15:31:43 UTC. The user restored both Emergent and original code-server access. Root inspected the existing PR1919 verifier receipts without rerunning it: actual_exit=0, problem=null, verified=true, checks=3, 582 frontend checked requests; exact prior A/runtime/deployment J; stdout SHA256 `47ab092fc2372fac8e979b957d821e181c05a145c46a2e7210f521112c12aca2` matched its summary. It finished at 2026-11-14T15:18:09.798861+00:00. Its after-status and a new official status both reported active=false. Root's read-only receipt validation at 15:29:35UTC passed. Original /app remains J, tracked clean, 120 nonignored untracked files, with the unsaved Untitled-1 buffer preserved. Prior financial UI acceptance is outside this task and is not claimed complete.

PR1920 was then merged normally as `29ec1e07eb7d1a23647cadff5bac26149accfa99`. Root and independent review verified ordered parents [J,B], exact B/M tree `14dfe135d6633f72c92cb1cb359f4b9933ac176a`, A ancestry and only the intent file in A..M. Exact-M push checks are running: Readiness43610488214, Security43610488216, CodeQL43610488271. The corrected original build runner7019 may now perform the governed rehearsal under the official operation mutex with no lease. No new publication has occurred; live production is still prior a5eae68.
