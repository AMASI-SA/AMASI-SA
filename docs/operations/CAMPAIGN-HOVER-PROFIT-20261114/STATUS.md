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


## Original governed rehearsal passed

Recorded at 2026-11-14 15:35:56 UTC. The reviewed runner7019 ran once and completed in `/tmp/mezan-pr1920-original-a_3qop92`. Root read the actual command receipts: `cd /app/frontend && yarn build` exit0 in64.0998787919525seconds; `python -B scripts/emergent_deployment_adapter.py verify-packages` exit0 in2.1528923290316015seconds. Backend identity equals the reviewed intent runtime_identity exactly, SHA256 `dccfab7d9d59cc5670969744606dcc2f97f1351401b9b60c0a4563aba6eb622c`, matching both independent CI host builds. Original /app is exact M, tracked clean. Both 120-file preservation manifests match including bytes/mode/inode/mtime; guard inactive under lock after build and freshly through official status afterward. Untitled-1 remains unsaved and untouched.

Exact-M Readiness43610488214 and Security43610488216 have passed; CodeQL43610488271 remains running at this checkpoint. No prepare/prepublish or new publication yet. Wait for the remaining required gate, then use independently reviewed evidence wrapper for official prepare/prepublish/verify, recording real new deployment history/version before verify.


## All merge gates passed; official release phases ready

Recorded at 2026-11-14 15:39:12 UTC. Root and independent reviewer confirmed exact-M Readiness43610488214, Security43610488216 and CodeQL43610488271 all SUCCESS. Original rehearsal and preservation are already successful. The compact official guard phase wrapper is committed with SHA256 `d072a7a0d92f8f6f79ade613094ebe0dda4c123563341b907f6134f85b8a9aff`; root and separate static reviewer found no material blocker. It pins A/B/J/M/tree/runtime/actor and original evidence, requires actual06/07 receipts and preserved120 files, invokes only official prepare/prepublish/verify, records real argv/exit/times/stream digests, and fails closed on duplicate phase claims or wrong lease/identity. No outer official lock is held around CLI invocations. Verify requires completed prepublish plus root's actual newer Deployment finished event/time and a new current UI version, then checks official3-probe output and inactive guard.

No release phase has run yet at this checkpoint. Emergent management shows prior current version a5eae68 (100th publish); the Republish dialog is open with Publish changes. Next: invoke prepare once, read its actual successful receipt/owned lease, then prepublish immediately before the one authorized UI publish. Preserve phase receipts under `/tmp/mezan-pr1920-original-a_3qop92/release-gate/` and never blindly retry a started phase.


## Own prepare succeeded — publication not started

Recorded at 2026-11-14 15:40:35 UTC. Reviewed gate prepare completed once at15:39:38.341084UTC. Root read actual official prepare receipt: exit0 in2.6583157920395024seconds, stdout SHA256 `20d5b57fe2faf3213a50ed1d5dae6725d5a318cc7b4ffce48d250a71d0fa1694`; empty stderr. The matching active lease belongs to `codex-campaign-hover-profit-1920`, prepared_at15:39:37.177675UTC, exact A/M/runtime. Postchecks preserve all120 files and tracked clean M. Evidence: `/tmp/mezan-pr1920-original-a_3qop92/release-gate/prepare`.

Do not duplicate prepare or clear this lease. Prepublish has not run and Publish changes has not been clicked. Next action is reviewed gate prepublish once, read successful ready_to_publish/owned lease receipt, then immediately click the already-authorized Publish changes in the open Emergent dialog. Wait for a newer actual Deployment finished event and new current version before verify.


## Authorized publication started once

Recorded at 2026-11-14 15:42:15 UTC. Reviewed prepublish completed once: actual official exit0 in3.5445305380271748seconds, finished15:40:50.371166UTC, ready_to_publish=true, exact A/M/runtime and owned lease retained,120-file preservation passed. Root read its actual receipt/stdout; SHA256 `e405edb4c8bf68a8e8f0d3db93b4d1ddc71e6cd3fc7a817729f5b0efa22386f9`.

Immediately afterward root clicked Publish changes once in the already-open authorized Emergent dialog. UI closed the dialog and showed disabled Publishing..., confirming start. Observation recorded by15:41:50UTC. Do not click publish again. Current prior version remains a5eae68 until a newer completion is observed. New production outcome is pending; no successful new deployment or official verification is claimed. Keep this task's matching lease until the one official verify succeeds.

Next: observe a newer explicit Deployment finished event and a new current published version, then run the frozen gate verify once using those actual UI values. Its output must pass3checks, exact identity, preservation and inactiveguard; then verify the new campaign rows on the live page. Do not alter A/B/M or rerun prepare/prepublish/build.


## Publication finished — 2026-11-14 15:55:38 UTC

- Emergent history after one ordinary page refresh now explicitly shows `Deployment finished Nov 14, 06:51 PM` (Riyadh; `2026-11-14T15:51:00Z`).
- New current published UI version observed: `1b4c4f6`; prior `a5eae68` moved to previous version.
- One Publish changes click only; no republish repeated.
- Own prepared lease remains active pending official verify. Verify has not yet been invoked.
- Next: execute frozen release gate d072 once with actual UI proof, then confirm official three live probes, exact identity/source closure and inactive guard; live card acceptance in parallel.


## Official verify invoked once — 2026-11-14 15:56:08 UTC

- Frozen gate `d072a7a0d92f8f6f79ade613094ebe0dda4c123563341b907f6134f85b8a9aff` invoked once in original `/app` terminal3 with phase `verify`, evidence `/tmp/mezan-pr1920-original-a_3qop92`, actual UI event `Deployment finished Nov 14, 06:51 PM`, timestamp `2026-11-14T15:51:00Z`, version `1b4c4f6`.
- Terminal changed to running python; no final exit/result observed yet. This is start evidence, not verification success.
- Do not repeat or interrupt the running verifier. Own lease remains owned pending completion. Live UI read-only acceptance proceeds in parallel.


## Verification preflight stopped; official verifier not started — 2026-11-14 16:01:22 UTC

- Actual frozen wrapper failed at `2026-11-14T15:55:57.683782Z` with `RuntimeError: Preservation baseline changed` before any `guard.started.json` was created. Prior running label was only wrapper launch evidence, not official verifier start.
- Tracked M lineage/cleanliness checks passed. Same 120 untracked paths, no additions/removals, 119 metadata/content records unchanged. Only platform bookkeeping path `.emergent/stripped_env_names` appended bytes with same inode/mode.
- Subsequent private read confirmed original 48,418-byte prefix hash still exact; size now 48,676 (258 appended), mtime15:59:53Z. No contents/environment names exposed. Cause is ongoing append-only bookkeeping; do not attribute it solely to publish.
- Failed directory remains immutable. No rerun, republish, abort, lease clear or application source change. Own lease still pending official verification.
- Live client after reload shows all nine card rows and numeric direct costs. Operating allocation and net are unknown in two existing campaign samples despite executive operating summary being present. This is not accepted as complete numeric feature proof; read-only causal diagnosis is in progress. No customer/order data retained here.
- Safe recovery under independent review: distinct one-shot verifier receipt directory, retain failed evidence, enforce exact M/A/runtime and owned lease, permit only proven append-only bookkeeping file while retaining all other119 original records and preserving each observed prefix.


## Narrow verification continuation reviewed — 2026-11-14 16:09:22 UTC

- Root and independent review approved a distinct verify-only wrapper, SHA256 `f3eef7ec485f47e3c1bd301efd941acb0921d484ba81c93e46a639d333d85a61`, without changing frozen source M, original gate d072 or any old receipt.
- It requires the exact observed failed `python -c` start receipt and proves the official verifier never started. Its pinned gate file is an explicitly new reference copy outside `/app`; no old source-file path is invented.
- Every witnessed bookkeeping prefix is retained (48,418;48,590;48,676 bytes), with same regular-file inode/mode, plus the whole new before-verify prefix afterward. All other119 preservation records remain exact; paths count120.
- All original lineage/runtime/intent, completed prepare/prepublish, exact owned lease, actual UI completion, official three-probe and inactive post-status checks remain. Actual official receipt is saved before postchecks; no failure can trigger an automatic verification rerun.
- Not yet executed. Next is one official verify attempt through this separate reviewed receipt path. No repeat publish, prepare, prepublish, abort or source modification.


## Reviewed verification continuation invoked once — 2026-11-14 16:10:43 UTC

- Bootstrap downloaded both exact pinned reviewed sources, checking d072 frozen gate and f3eef7ec recovery runner digests; wrote new private regular files outside `/app` under `/tmp/mezan-pr1920-verify-reviewed-f3eef7ec` and invoked the distinct runner once.
- Terminal3 now shows running python. Final official receipt/exit is not yet observed; do not infer verification success or repeat the command.
- Narrow operating diagnosis reached only the two exact-name campaign catalogs in original configured DB, both empty; no owner chosen and no financial basis function called. Process environment did not override Mongo settings. No broader searches, provider calls, writes, or credentials printed. Live operating cause remains unproven.
- A separate candidate payroll-scope correction exists in scratch with synthetic regression evidence; it is not part of frozen M and has not been accepted, merged, or published.


## Publication officially verified; follow-up parity fix remains — 2026-11-14 16:24:47 UTC

- One actual official CLI verify completed exit0 from16:10:08.014524Z to16:22:18.751950Z (730.737s), with `verified:true`, three probes, exact sourceA/deploymentM/runtime identity and frontend artifact tree. First-probe manifest records582 checked frontend requests. Stdout SHA256 `f7bb0927efbd754ced07837146d9169f64794ffc262d5acf6a1b1849044e7dda`.
- Root read actual receipt/streams and recomputed hashes; prior phases remain byte/metadata identical. New reviewed continuation result completed16:22:18.957407Z. All119 other preservation records exact; specific metadata file only appended86bytes while all witnessed prefixes, inode and mode were retained.
- Official verification closed this lease. A fresh official status at16:23:12.874031UTC exited0 and returnedinactive. No abort or manual lease clearing.
- This certifies deploymentidentity, not full functional acceptance: live operating/net unknown prompted a source-proven executive-parity followup. New draft PR#1922, branch `codex/campaign-operating-executive-parity-20261114`, initial source `22832941d5d61d77ed5c6acb2b3a6565423ce675`.
- Initial source coverage/readiness passed, but independent review found a masked-invalid utility fallback boundary; a narrow correction is pending before freezing the final source. Old CI success will not substitute for the final source's gates.
