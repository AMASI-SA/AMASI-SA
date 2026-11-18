# Direct and operational Ready — 19 November 2026

## Current release handoff — 2026-11-18 22:59 UTC

Canonical release PR: https://github.com/AMASI-SA/AMASI-SA/pull/2001.
Release branch: codex/operational-ready-release-20261119.
Source A: 213a284901fb49b8c79948669cf67cc37cabb409.
Intent-only B: 1418ea245eec1fad69bf959292480e46d0b72fb2.
Source tree: 6e36ac7239f030228cc793f66dbbbca6563296d3.
Deployment tree: 3ec825afd58535d4e00670dfdd1179734d72effe.
Reviewed production base J: 77d824a780df582b5c32f4dda87a959e35e1ed1e.
Runtime release: rg5-e8739283dad7f69c42c3f535cc32ea514fd505e8e65daedd82a785200b75d03c.
Intent SHA256: b852bc5798708a28b63d306955cb6dc9195d909c5b872c44fed02867f04890c1.

All twenty applicable exact-B CI runs passed, including G47, Stock Eligibility,
Review Completion, Operational Balance, both CodeQL languages, Security Gate,
and Mezan Release Readiness. Both Host Node20.20.2 and24.19.0 clean-clone adapter
rehearsals passed. Their downloaded ZIP SHA256 values were checked; each tar
matches the frozen intent, complete runtime identity, proof and all294 Frontend
artifact records. Independent source manifests matched all1242 Backend and307
release-control records. Latest focused local Ready/supplier tests:87 passed.
Supplier function/default progress AST preservation was independently verified.

No production merge, own lease, shared source mutation or publication has been
performed by this task. Production changed by this task: no. The last original
shared guard result supplied by the owner was ACTIVE for another conversation:
actor codex-aramex-refresh-2000-20261119, prepared2026-11-18T22:26:19.151229Z,
sourcec0ce20a1273574be833261218335a4b6e527d658, deployment77d824a780df582b5c32f4dda87a959e35e1ed1e,
release rg5-d3c749f37ff1300718cedce39b78a6277b440812946c7308fad91ec4d6bf3601.
Our isolated rehearsal stopped before fetch/clone because that lease was active.
Public health now serves that other release, but its owner still needs explicit
Deployment Succeeded plus original verify3/lease closure. The user was asked to
relay to that conversation. Never clear or reuse its lease.

Next safe action: obtain fresh original /app status active:false, confirm remote
production still matches the reviewed base, normal merge PR2001 with expected
head B and preserve its exact tree, then tracked-clean /app fast-forward, original
host yarn build adapter with PYTHONDONTWRITEBYTECODE=1 and the reviewed public
client origin, own prepare/prepublish, one authorized publication and mandatory
newer Deployment Succeeded plus official verify3/active:false. Then inspect an
in-progress affected order read-only through ADB. Do not mark a real order Ready
merely to test the fix. Supplier conditions and unrelated files remain preserved.

This final task record is maintained on the diagnostic branch so the immutable
A/B release chain stays unchanged. Diagnostic PR1999 is superseded and must not
be deployed. The PR2001 body is the current cross-branch continuation pointer.
Issue1006 is at its comment limit. No new source build or implementation work is
required unless remote production or the source tree has changed.

The owner requests that an unfinished internal operational item or product
directed to assembly can be marked Ready whenever its current Salla order is
in progress. Other Salla statuses block the action. Supplier-product Ready
conditions must remain unchanged. The owner explicitly authorizes publication.

Production base: `77d824a780df582b5c32f4dda87a959e35e1ed1e`.
Task branch: `codex/operational-ready-release-20261119`.
Preserved diagnostic branch: `codex/operational-ready-status-20261119` (PR 1999).

## Working checkpoint

Search and the transactional virtual-piece writer use the same current order
status predicate. Component/source reconciliation, approval proof, local stage,
payment and stage instructions cannot reject the direct/operational Ready fact.
Tenant scope, actor authorization, revision checks and idempotency are retained.

Direct stock consumption uses the existing service's pre-write deferral contract:
available accepted units are consumed once; missing business prerequisites are
recorded as deferred, and a mutation failure still aborts the owner transaction.
Operational items never consume inventory. Supplier Ready functions are not
modified. Saving Ready does not bypass independent whole-order completion and
shipment requirements; an order-level business hold leaves Ready saved.

Fresh focused local tests: 52 passed, zero skips. The prior implementation
reproduced false `can_mark_ready` for in-progress orders with source or review
holds. A broader local run found one obsolete source-string assertion after
321 passing tests and 24 passing subtests; that assertion was removed because
the new regression suite verifies behavior. The corrected broader run passed
322 tests and 24 subtests. Existing direct integration expectations are updated
for the explicitly requested contract; supplier expectations remain intact.

The first remotely recoverable source checkpoint is
`c7aebc86f72c2d9dfc1465000ff32c5916d2919b`, Draft PR 1999. Its governed Linux
candidate build passed, but real G47 CI exposed six failures. The candidate
artifact from that SHA is superseded and must not be published.

The follow-up reads coherent saved provider/root status even when an unrelated
invalid creation date prevents DTO mapping, and still rejects contradictory
or absent status evidence. Direct stock deferral retains the previously
accepted historical-plan consumption contract; the new optional stock-service
argument defaults to the existing strict reviewed-snapshot check for supplier
callers. Their functions and conditions are unchanged. The two historical
virtual-readiness expectations are updated for the owner's status-only rule.
The actual prebuilt/purchased stock and conflicting-status assertions are
retained. Latest local affected suite: 145 passed. Fresh exact-source real-Mongo
and full CI remain required before freezing any new intent.

Canonical follow-up source `3ce1aaba367476d750253662b0b96b1670228ea2`,
tree `0d0aa572e6c169031d307aa76adeddd88a025c83`, passed the governed candidate
build and 329 G47 cases plus 127 subtests. Two remaining G47 assertions expected
an actually supplier-receipted order to remain incomplete after every direct
and operational item became Ready. Existing supplier completion policy allows
that fully covered order to complete; those assertions now preserve that
policy. Stock remains deferred and no supplier Ready condition is changed.
The next checkpoint changes only this test and this status record; runtime
blobs remain identical. Its new source SHA requires a fresh candidate intent.
Latest focused local Ready/supplier consumption run: 87 passed, zero skips.

Source `67d5646cc119aa37ad2486636aa4ea311a00fbd1` passed the exact-source
G47 suite and governed candidate build. Review Completion exposed two actual
regressions: the early pending-order return prevented the existing local
preparation promotion after every direct/operational item was Ready and all
supplier pieces had been received. Keep the original promotion path whenever
its existing eligibility holds, even while individual supplier Ready work is
unfinished. Whole-order instructions still apply when all pieces are Ready;
business holds still save virtual Ready without completing the order. The
two existing real-Mongo failures remain the regression checks. Latest focused
local Ready/supplier-consumption run after this change: 87 passed, zero skips.

Production advanced through PR 2000 while this task was testing. Its thirteen
disjoint changed files form the current production baseline. An Operational
Balance job used the newer base workflow with the older head and could not find
its added tests. The 8fdad087 integration checkpoint retains those files, but
the original Release Guard correctly rejected its merge history because that
history changed the prior intent. No guard or workflow is weakened. Preserve
that diagnostic checkpoint, and start this release branch directly from
77d824a780df582b5c32f4dda87a959e35e1ed1e with the same reviewed runtime and tests.
Only the task record differs from 8fdad087. All earlier candidate intents are
superseded. Fresh exact-source CI and a fresh intent-only B remain required.

Windows test command (from backend, with dotenv disabled and PYTHONPATH set):
`python -B -m pytest --noconftest -p no:cacheprovider tests/test_operational_item_ready_policy.py -q --tb=short`.

The Windows browser-control tool refused further UI access because it could
not establish the browser URL. In-app browser operations also timed out. No
shared `/app` changes, release lease, merge or publication have occurred.
The owner provides authorized Emergent Terminal output: shared tracked files
are clean at production 77d824a780df582b5c32f4dda87a959e35e1ed1e, Node24.19.0,
Yarn1.22.22, and unrelated untracked files are retained. The shared guard was
inactive. Final commands will use that terminal route. Production changed by
this task: **no**.

Next: finish exact-source tests, preserve source A remotely, governed Linux
build/frozen intent-only B, exact-B CI, then shared inactive guard, adapter,
prepare/prepublish and one publication. Require a newer visible successful
deployment and the unchanged official three-probe verifier. Do not use a
GitHub merge or a publish-button state as deployment proof. Issue 1006 is at
its comment limit; retain continuation in this task document and task PR.


## Publication takeover checkpoint — 2026-11-19

The owner explicitly authorized this continuation to publish PR2001 urgently after the Aramex lease closed. Original shared guard status was freshly inactive; original Aramex verifier had completed verified:true/checks:3. Shared `/app` was tracked-clean at J with AGENTS.md blob 0e0bbff4d9625f9323affae84b620a0cf12eb209 and no Coordinator marker. SHA256 values for 120 unrelated untracked files were preserved.

Normal merge succeeded as c3c1cd26d93ab24d6e08003278b9910526055cc7. GitHub independently reports exact B tree 3ec825afd58535d4e00670dfdd1179734d72effe and parents J/B. Shared `/app` fetched and fast-forwarded to that merge; tree matches B, tracked status remains clean, and all 120 unrelated files retain their hashes. A/B and the frozen intent are unchanged. The original host `yarn build` adapter is being run with the reviewed public origin and PYTHONDONTWRITEBYTECODE=1; its log is /tmp/mezan-ready-2001-adapter.log, with exit status /tmp/mezan-ready-2001-adapter.exit.

Production source is merged; the public runtime has not been published by this continuation. No new lease exists yet. Next: confirm original adapter exit0 and identity/proof, prepare a new own lease, prepublish immediately before one authorized publication, then newer visible Deployment Succeeded, original verify three probes and status active:false. Finally inspect the Ready affordance on an in-progress order in the emulator without a fulfillment write. Preserve supplier policy and all unrelated work. PR2001 is the cross-branch continuation ledger because Issue1006 is full.


## Publication in progress — 2026-11-18 23:20 UTC

Original shared host adapter succeeded with exit0 in63.60s and exact source213a284901fb49b8c79948669cf67cc37cabb409/runtime rg5-e8739283dad7f69c42c3f535cc32ea514fd505e8e65daedd82a785200b75d03c. HostNode24.19.0 dispatches the governed toolchain as specified. The first adapter run correctly refused two generated Python bytecode files; those two files were preserved outside the source in /tmp/mezan-ready-2001-bytecode-backup, and the unchanged adapter succeeded on retry. No tracked source was changed. Successful log:/tmp/mezan-ready-2001-adapter-r2.log; exit file:/tmp/mezan-ready-2001-adapter-r2.exit.

Own v5 lease prepared at2026-11-18T23:16:40.561907+00:00, actor codex-ready-2001-publish-20261119, deployment c3c1cd26d93ab24d6e08003278b9910526055cc7. Original prepublish reported ready_to_publish:true twice, with the final run immediately before Publish changes. The final Publish changes control was submitted once; UI confirms Deployment Started Nov19 02:20AM Riyadh and Publishing. Merely opening the Republish confirmation did not publish. Current own lease must remain until official completion.

Next: observe newer explicit Deployment Succeeded, run unchanged original python -B scripts/production_release_guard.py verify --url https://mezansalla.com, retain verified:true/checks:3 and inactive status, then read-only Ready UI acceptance. No real order Ready mutation is authorized for testing. Android emulator is not exposed in the current Linux surface; no adb executable or reliable remote emulator target is available. Do not claim emulator acceptance from server-only or web evidence.


## Runtime publication observed — 2026-11-18 23:30 UTC

Emergent completed the new publication with short deployment ID 8583b5b, newer than Aramex dbe53a6. After a fresh page reload the visible event is green `Deployment finished`, dated Nov 19, 02:30 AM Riyadh. This UI does not expose the literal text `Deployment Succeeded`; do not claim that literal was captured. The latest published row is 8583b5b (the UI caps/relabels its visible ordinal at 100th). Runtime logs show Backend started successfully. This is UI publication evidence, not a substitute for official identity verification.

Next safe command, using this task's still-active own lease: `python -B scripts/production_release_guard.py verify --url https://mezansalla.com`. Preserve its complete output, require verified:true and checks:3, then original status active:false. No repeat publication is needed. Live Ready acceptance and unavailable Android emulator limitation remain as above.


## Official production verification passed — 2026-11-18 23:43 UTC

The unchanged original command `python -B scripts/production_release_guard.py verify --url https://mezansalla.com` exited 0. Complete local result is `/tmp/mezan-ready-2001-final-verify.json`; stderr is empty and exit file contains 0. It returned verified:true, checks:3, source_git_sha 213a284901fb49b8c79948669cf67cc37cabb409, deployment_git_sha c3c1cd26d93ab24d6e08003278b9910526055cc7, runtime rg5-e8739283dad7f69c42c3f535cc32ea514fd505e8e65daedd82a785200b75d03c, protocol5 and frontend artifact tree SHA256 585f75592e29fbc6183b15a9fa9d5a6baecdc28cdb7bef1c1729956bd27566f4. The official verifier enforced the intended identity, critical hashes and Frontend proof on all three consecutive probes.

Each probe checked 594 Frontend requests. `/build-meta.json` passed exact bytes and application/json MIME in canonical and cache-busted requests with both Accept application/json and */*. Observed backend boot times were 2026-11-18T23:29:56.806139+00:00 and 2026-11-18T23:29:52.954197+00:00, both newer than prepare. The matching own lease closed through successful verify. A fresh original `status` returned `{ "active": false }`, saved at `/tmp/mezan-ready-2001-final-status.json`.

Post-publication source/preservation check: shared HEAD and tree still equal c3c1cd26d93ab24d6e08003278b9910526055cc7 / 3ec825afd58535d4e00670dfdd1179734d72effe; tracked status is clean. All 120 baseline unrelated files remain present; 119 retain exact hashes. The sole changed file is platform metadata `.emergent/stripped_env_names` (baseline e32a93eb48b98254e75db38d16c803af693290bbbcb0ff07e06250b3c20f55d9; current 23676a8025f8080a468d15ab6a47eec66ef86f0326a4f83bda1356072c305351), observed after publication. This agent did not edit or restore that metadata file. No unrelated tracked file was changed, and generated bytecode remains preserved outside source.

Production changed: yes, publication 8583b5b is verified at the intended runtime identity. UI wording remains the observed green `Deployment finished` at Nov19 02:30AM Riyadh, not a captured literal `Deployment Succeeded`. Next safe action: read-only Ready affordance inspection of an in-progress order. Do not POST Ready. Android emulator is unavailable in this session; web evidence must be identified as web evidence. No release lease remains active.
