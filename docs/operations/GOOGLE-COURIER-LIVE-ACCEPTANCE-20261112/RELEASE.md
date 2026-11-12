# PR1890 release evidence: Google and store-courier live acceptance

## Current release outcome: verified; Google accepted; courier still open

The owner observed the newer green `Deployment finished Nov 12, 04:02 AM`
(2026, Asia/Riyadh), published version `d73f584`. This is the actual UI wording,
not a claim that the literal `Deployment Succeeded` appeared. The official
guard completed three probes with `verified:true`; its subsequent status was
`active:false`.

| Verified production field | Value |
|---|---|
| Source A | `da0d2e2fb82a7848bcd398ca292ba93e1a880681` |
| Deployment M | `0af4a3da57072a4995a2ebd0ca76720d17a80ee6` |
| Runtime release ID | `rg5-8c4fb8eb704af7c47020a751629bd3af71672fbf26ff02bdbde8b9d969f9ffcc` |
| Boot identity | `2026-11-12T01:02:06.982284+00:00` |
| Frontend artifact tree SHA256 | `330125593ccc698dd5e6492dd1841c198042d9800a9cbe2ee9fbc9ae73ddc480` |
| Official verification | `checks:3`, `verified:true` |
| Following guard status | `active:false` |

**Actual Google acceptance PASS:** November 11 saved reads show available
**96.48 SAR**, marked incomplete, in both the yellow card and executive Google
SPEND cell. Financial totals and profit remain incomplete. No forced sync or
advertising account change was performed.

**Actual courier acceptance FAIL / OPEN:** both reported refresh trials still
return the generic failure; no print preview or physical printing success is
established. Fresh provider data passed a pure replay of all five inspected
stages. Subsequent sanitized owner-DTO evidence identifies a remaining saved
recipient comparison gap: matching names and the same Saudi phone identity
use nine-digit buyer versus `966`-prefixed twelve-digit receiver formats, which
the literal comparison rejects.

The follow-up branch `codex/courier-recipient-identity-20261112` starts from M
above and is **not published**. PR1890 source A/B remains immutable. This
documentation-only branch must **never** be deployed. The sections below are
preserved historical release checkpoints; their earlier pending/unverified
wording is superseded by this outcome. No live order identifiers or customer
data are included.

## Previous checkpoint: publication in progress (historical)

**Publication is IN PROGRESS. Reviewed source and intent are merged, all 28
applicable latest B workflows and the actual shared adapter passed, and own
prepare/prepublish succeeded. Runtime verification and live acceptance remain
OPEN.** This document records the independently verified CI evidence and the
release owner's shared-workspace and publication updates on 2026-11-12. It
supplements the source checkpoint in [STATUS.md](STATUS.md).

The release owner reported successful prepare/prepublish for actor
`codex-google-courier-acceptance-20261112`, source A
`da0d2e2fb82a7848bcd398ca292ba93e1a880681`, deployment M2
`0af4a3da57072a4995a2ebd0ca76720d17a80ee6` and runtime
`rg5-8c4fb8eb704af7c47020a751629bd3af71672fbf26ff02bdbde8b9d969f9ffcc`,
with `ready_to_publish:true`. At approximately 2026-11-12 00:53:58 UTC,
Publish changes was clicked once. The UI displayed `Publishing...` with the
action disabled; the previous published version was `0ef7c77`.
No newer deployment completion, three-probe verify or live acceptance success
is established by this in-progress state.

Repository: [AMASI-SA/AMASI-SA](https://github.com/AMASI-SA/AMASI-SA).
Source task branch: codex/google-courier-live-acceptance-20261112.
PR: [1890](https://github.com/AMASI-SA/AMASI-SA/pull/1890).
Canonical continuation ledger:
[Issue 1006](https://github.com/AMASI-SA/AMASI-SA/issues/1006).

**Documentation-only branch: codex/google-courier-live-evidence-20261112.
Never use this branch or a documentation checkpoint made from it as a
deployment candidate.** The release owner controls publication, further
evidence updates and Git checkpoints. This evidence writer did not change
runtime source, Git refs, shared /app, leases or publication state.

## Immutable source, intent and merge identities

| Role | Commit | Root tree |
|---|---|---|
| Previously reviewed Production base J / M | 803e87f72d9661f9b961587bbc0cededfd1f3c90 | ac5225a4cb70b150ee8383305fb5b5da3c3a8a25 |
| Source A | da0d2e2fb82a7848bcd398ca292ba93e1a880681 | 85ad6add632b1a4f43701c8d6484d3c8a0da44d7 |
| Intent-only B | 205a040ca557618d5f6f445d7c54592eaf7cec36 | 066dbee7956fe29e91e1acdae9ff77b7ef2645c2 |
| PR1890 merge M2 | 0af4a3da57072a4995a2ebd0ca76720d17a80ee6 | 066dbee7956fe29e91e1acdae9ff77b7ef2645c2 |

B has A as its sole parent. M2 has J and B as parents, and its complete tree
equals B. The A-to-B content diff changes only
release/release-intent-v5.json. J-to-A neither changes that file nor includes
an intervening commit changing it. The release owner reported that the
official Git contract and strict reviewed-intent loader passed before the
shared handoff; independent Git reads confirmed the parent/tree relationships
above.

| Bound value | Exact evidence |
|---|---|
| Intent schema / protocol | 2 / 5 |
| Intent bytes | 1268690 |
| Intent SHA256 | 607306cef5e94f563cf9262aa2e9b3e198f914730c3b6f1649cfb3243c02ffd8 |
| Runtime release ID | rg5-8c4fb8eb704af7c47020a751629bd3af71672fbf26ff02bdbde8b9d969f9ffcc |
| Frontend artifact tree SHA256 | 330125593ccc698dd5e6492dd1841c198042d9800a9cbe2ee9fbc9ae73ddc480 |
| build-meta.json bytes | 463180 |
| build-meta.json SHA256 | 24041ffbdf37d519cba262b35929937db03c8f0e55e71f17398493885ff7d1e6 |
| Reproducibility proof bytes | 1521 |
| Reproducibility proof SHA256 | e1b864f4e3dffedf5c4c15704b86b0f833ebcfc0c6a2a7366ced8087852c45a0 |
| Governed toolchain | Node 22.23.2 / Yarn 1.22.22 / Vite 8.2.4 |
| Reviewed public client origin | https://mezansalla.com |
| Public origin value SHA256 | d2e055b74392bfdfcf1d64af1e88a91ab7029a49729a7ac53870bf48bfe4e5ff |

The two clean frontend builds produced identical artifact and build-metadata
hashes; retained pass 2 is bound in the intent. These are candidate/rehearsal
identities, not a claim that the live runtime now serves them.

## Source scope and independent reviews

J-to-A changes thirteen files: one courier runtime helper and its test file,
four frontend runtime files and four frontend test files, and three task
status documents. Package/lock files, release workflows, the adapter and the
backend Google financial readers are unchanged in this follow-up.

The courier changes accept exactly empty optional carrier aliases as absent
and trim outer ASCII spaces only from native history actor display names
after checking string type and raw length at most 300. Required identity,
carrier, recipient, exact transition-note, complete-list/history, final
provider/local source, cancellation and authorization checks remain strict.
An accepted recovery still returns an ID-free order QR; it does not reopen an
archived shipment or change Salla.

The Google frontend changes preserve existing known-subtotal quality fields
and display a finite positive known amount explicitly as incomplete. Both
visible spend surfaces require the selected period to match. Canonical null
spend, total costs, profit, CPA/ROAS, backend account selection and source
coverage remain unchanged. Complete zero retains its ordinary numeric path.

The independent source review found no blocker. Its frozen courier hashes
matched before and after the independent focused run:

| File | SHA256 |
|---|---|
| backend/order_engine/store_courier_history_recovery.py | 30dcee74f03fc684ed2824eef60cf2406da0d66f206e5bc51c6bfb187eef59bf |
| backend/tests/test_courier_history_identity_recovery.py | 28032943306ceb110a3f2178c9d89d0b19ccfc720ebc0dc9b8b3b03ff542526e |
| backend/tests/test_courier_history_recovery_races.py, unchanged | 2af1a36a4824f87b404bdd0b32a34a1f201e52bcec4a7e948bbdae7a6bbb1ac6 |

Source-checkpoint test evidence, with full commands and red/green details in
[STATUS.md](STATUS.md):

- Courier actor-format regression: 6 failed /24 passed before the repair;
  the same 30 selected cases passed after it.
- Final courier history/race suites: 132 passed, zero skipped; writer exit 0
  in 9.09s and independent exit 0 in 9.21s. Both public refresh routes, no
  provider POST, immutable source/tombstones, invalid aliases/actor IDs,
  strict material text, recipient and late material/authorization changes
  are covered.
- Generic extractor/recovery/explicit-completion integration: 58 passed,
  zero skipped.
- Frontend final focused suites: 151 passed across four suites, zero skipped,
  independently repeated. Adjacent card-refresh, cart-freeze and merge
  suites: 28 passed across three suites; implementation verification totals
  179 tests in seven suites.
- The source scope and whitespace check passed. No completed local test was
  rerun merely to prepare this release record.

The source candidate was independently inspected with the repository's
official manifest, frontend-source, critical-hash, public-environment and
runtime-identity validators, using the downloaded candidate in memory.
All 1149 frontend records were checked, including the eight changed UI
runtime/test files. Full Git-derived backend/control manifests matched.

| Manifest | Bound files | Delta from J | Manifest/tree evidence |
|---|---:|---|---|
| Backend runtime package | 1175 | modified 1; added 0; deleted 0; tombstones 0 | manifest SHA256 3fa73a4a278d29782d9602b64915c21757ac84e40c03b33c9733618068bda08e; tree SHA256 9f68c66441ecdf3af4da862650a08b280594bd7fbfa4df85362e011035bc4ed3 |
| Frontend source | 1149 | Full source manifest verified | Git tree fa272eafe8c62904d661dd0badf9407efbbd5664; tree SHA256 fa1e33760973aa7cc8637582792176869d38e016cd5d562558230614af409b17 |
| Release control source | 273 | modified 0; added 0; deleted 0; tombstones 0 | manifest SHA256 d287510fb936b9898469874b9110a3d5d0cfd009514355a03c0b7227ddf443c3; tree SHA256 75a7c75ef23c6700e8514873c7c9b6f19c837ebbbcd18385139a37841b505de2 |

## Source-A candidate artifact

[Readiness 43127572970](https://github.com/AMASI-SA/AMASI-SA/actions/runs/43127572970)
completed successfully for A. Frontend job 130883442144 completed the two
clean builds, the official candidate loader and manifest comparisons, artifact
upload and clean-worktree assertion.

- Artifact 13903592596: release-v5-reviewed-intent-candidate.
- ZIP size 367299 bytes; SHA256
  5f39d62067f029972fb36a7b86201e703960614f8807a0c44b78cdfed19eb0a3.
- Its sole member is release-intent-v5.json, with the exact size/hash in the
  identity table above.
- The artifact metadata explicitly binds A. The independently downloaded ZIP
  digest matched GitHub's digest; its JSON bytes match the tracked B/M2 intent.
- Candidate retention is two days. B's generated-output and evidence
  artifacts below have fourteen-day retention.

A's B-only clean-clone jobs were correctly inapplicable. They were subsequently
required and executed successfully on exact B.

## All latest exact-B workflow results

The final independent commit-scoped inventory returned 32 run records.
Selecting the newest relevant run per workflow yields 29 workflows:
**28 success, one expected branch-only skip, no pending or failed gate.**
Every successful row below reached a terminal success state for B.

| Workflow | Latest run | Result |
|---|---|---|
| Ads Auto Sync 5 Minutes V2 | [43128265458](https://github.com/AMASI-SA/AMASI-SA/actions/runs/43128265458) | success |
| Background workers and two-hour Qoyod cycles | [43128265464](https://github.com/AMASI-SA/AMASI-SA/actions/runs/43128265464) | success |
| CodeQL | [43128265627](https://github.com/AMASI-SA/AMASI-SA/actions/runs/43128265627) | success |
| Dashboard card cadence and catalogue admission | [43128265357](https://github.com/AMASI-SA/AMASI-SA/actions/runs/43128265357) | success |
| G47 Focused Integration | [43128265558](https://github.com/AMASI-SA/AMASI-SA/actions/runs/43128265558) | success |
| GA4 Realtime Dashboard | [43128265543](https://github.com/AMASI-SA/AMASI-SA/actions/runs/43128265543) | success |
| Google Ads full management | [43128265549](https://github.com/AMASI-SA/AMASI-SA/actions/runs/43128265549) | success |
| Mezan Release Readiness | [43128265276](https://github.com/AMASI-SA/AMASI-SA/actions/runs/43128265276) | success |
| MZ2 Track F native shipping | [43128265644](https://github.com/AMASI-SA/AMASI-SA/actions/runs/43128265644) | success |
| Operational Balance Web | [43128265367](https://github.com/AMASI-SA/AMASI-SA/actions/runs/43128265367) | success |
| Operational Stock Eligibility | [43128265735](https://github.com/AMASI-SA/AMASI-SA/actions/runs/43128265735) | success |
| Page and operational read performance | [43128265599](https://github.com/AMASI-SA/AMASI-SA/actions/runs/43128265599) | success |
| Production Campaign AI Decision Intelligence V3 | [43128265612](https://github.com/AMASI-SA/AMASI-SA/actions/runs/43128265612) | success |
| Production Campaign AI Decision V3 Extended | [43128265647](https://github.com/AMASI-SA/AMASI-SA/actions/runs/43128265647) | success |
| Production Dashboard Ads Inline Breakdown | [43128265608](https://github.com/AMASI-SA/AMASI-SA/actions/runs/43128265608) | success |
| Production Dashboard Executive Four Platform Date V1 | [43128265531](https://github.com/AMASI-SA/AMASI-SA/actions/runs/43128265531) | success |
| Production Dashboard Four Platform Spend V1 | [43128265666](https://github.com/AMASI-SA/AMASI-SA/actions/runs/43128265666) | success |
| Production Dashboard Platform Spend Hourly V1 | [43128265311](https://github.com/AMASI-SA/AMASI-SA/actions/runs/43128265311) | success |
| Production Dashboard Profit Date Race V1 | [43128265592](https://github.com/AMASI-SA/AMASI-SA/actions/runs/43128265592) | success |
| Production Dashboard V2 Ad FX And Payment Fees | [43128265524](https://github.com/AMASI-SA/AMASI-SA/actions/runs/43128265524) | success |
| Production Dashboard V2 Salla Ads Executive | [43128265660](https://github.com/AMASI-SA/AMASI-SA/actions/runs/43128265660) | success |
| Qoyod Payment Freshness | [43128266804](https://github.com/AMASI-SA/AMASI-SA/actions/runs/43128266804) | success |
| Security Gate | [43128265388](https://github.com/AMASI-SA/AMASI-SA/actions/runs/43128265388) | success |
| Shipping Print Boundary | [43128265350](https://github.com/AMASI-SA/AMASI-SA/actions/runs/43128265350) | success |
| Snapchat Direct Active Publication | [43128405749](https://github.com/AMASI-SA/AMASI-SA/actions/runs/43128405749) | success |
| Snapchat Reporting V2 Shadow | [43128405834](https://github.com/AMASI-SA/AMASI-SA/actions/runs/43128405834) | success |
| Snapchat Settings Management V2 | [43128405955](https://github.com/AMASI-SA/AMASI-SA/actions/runs/43128405955) | success |
| Sold product cost details | [43128265330](https://github.com/AMASI-SA/AMASI-SA/actions/runs/43128265330) | success |

The sole latest skipped workflow is
[TikTok Creator Release Candidate 43128265613](https://github.com/AMASI-SA/AMASI-SA/actions/runs/43128265613).
Its job explicitly requires the separate branch
codex/tiktok-content-ci-20261011; this PR does not meet that condition.
The intent path triggered its workflow, but its branch-specific candidate job
is outside PR1890's scope.

Marking the already frozen PR ready for review created newer same-B runs.
These earlier records were retained and were not misreported as current
successful checks:

| Earlier run | Earlier terminal result | Successful replacement |
|---|---|---|
| Snapchat Direct Active Publication 43128265604 | success, superseded | 43128405749 |
| Snapchat Reporting V2 Shadow 43128265339 | success, superseded | 43128405834 |
| Snapchat Settings Management V2 43128265510 | cancelled, superseded | 43128405955 |

The Settings workflow explicitly uses cancel-in-progress for the same PR and
also triggers on ready_for_review. The cancelled run is not counted as passed.
No reviewer initiated a rerun or changed workflow/run metadata.

## Exact-B Readiness and clean-clone evidence

[Readiness 43128265276](https://github.com/AMASI-SA/AMASI-SA/actions/runs/43128265276)
completed successfully. Its logs identify deployment B, source A and base J
exactly as listed above.

| Job | Job ID | Result |
|---|---:|---|
| Backend preflight | 130885757483 | success |
| Host Node 20.20.2 engine/dispatcher | 130885757074 | success |
| Host Node 24.19.0 engine/dispatcher | 130885757460 | success |
| Frontend build using reviewed intent | 130885757392 | success |
| Host Node 20.20.2 clean-clone adapter rehearsal | 130886242863 | success; no skipped step |
| Host Node 24.19.0 clean-clone adapter rehearsal | 130886242629 | success; no skipped step |

Both clean-clone jobs proved absence of inherited generated inputs, exercised
the package adapter without Git metadata, verified the governed artifact,
runtime identity and isolated package boundaries, and served exact canonical
and cache-busted build metadata with JSON MIME through the governed runtime.
Their frontend artifact and release ID match the identity table.

The frontend candidate freeze/upload steps were correctly skipped because B
already contains a reviewed intent. The manual-redeploy handoff job 130887522191
is push-only and was correctly skipped on this PR event. Neither skip removes
a required rehearsal or print test.

All four retained artifacts below explicitly bind head B and were unexpired
when inspected:

| Artifact name | Artifact ID | ZIP bytes | ZIP SHA256 |
|---|---:|---:|---|
| release-v5-clean-clone-evidence-host-20.20.2 | 13904146494 | 45542 | 7501de34bcfa52b183032563ab4cb318ff0d7c7305d88abe524ac6e2dae1559f |
| release-v5-clean-clone-evidence-host-24.19.0 | 13904456534 | 45538 | ff70574ffb9a186fed3db4db8ad77092d504a6418329fc72516cd949d001fa38 |
| release-v5-generated-outputs-host-20.20.2 | 13903962253 | 2930401 | f707a21ca80f1db844e96fb991cfacf7051289cdd3edfb00b3eb857ec0e8554d |
| release-v5-generated-outputs-host-24.19.0 | 13904870809 | 2930370 | e6b4386e1b706556c07697ccc5766318fa523f31bbde755baa3c3b0970988d0b |

## Shipping, stock and operational test evidence

Shipping Print Boundary run 43128265350, job 130885829391, checked out exact B
and completed:

- Courier print latency and identity regressions: 26 passed in 5.00s.
- Courier identity, explicit completion, history and race recovery:
  190 passed in 10.61s.
- Broad shipping/replacement/concurrency/transaction regressions:
  596 passed, one expected skip, in 38.21s.

The only pytest skip was the memory variant of
test_real_transaction_failure_leaves_no_partial_label at
backend/tests/test_shipping_print_boundary.py:229. That test is parametrized
over memory and mongo, and intentionally requires real Mongo for rollback.
The isolated replica set was available; there was no Mongo-configuration skip
or other skipped pytest case. The Mongo variant therefore ran in the broad
suite. The alternate workflow step for a HEAD preceding snapshot bootstrap
was inapplicable because snapshot bootstrap is present.

Operational Stock Eligibility run 43128265735, job 130885759223, passed
3067 tests plus 7 subtests in 248.99s. Its XML audit explicitly reported
3067 executed cases with zero skips/failures/errors.
Artifact 13903763511 retains the exact-B stock evidence.

Operational Balance Web run 43128265367 completed both jobs:

- Backend 130885757701: 3158 passed in 206.70s, then 16 passed plus 18 subtests
  in 0.37s.
- Frontend 130885757523: 40 suites and 462 tests passed.
- Both missing/skipped-test rejection steps succeeded.

These CI results establish the tested contracts in isolated environments.
They are not evidence of a completed live publication or physical printing.

## Shared-workspace handoff and remaining acceptance

The release owner's shared preflight found guard active:false, the Production
branch at J, zero tracked or staged changes, 120 untracked platform files to
preserve, and no root backend bytecode files. The owner subsequently confirmed
that shared /app was fast-forwarded to M2 with tree B and all 120 unrelated
platform files' content and modes preserved. The guard remained inactive.

The actual configured package adapter completed through /app/frontend yarn
build with the exact approved public origin and bytecode writing disabled.
Yarn reported `Done in 65.79s.` and the conditional success command printed
the exact A and runtime identity above. The final adapter JSON reported
`built:true`, protocol5, and the expected frontend artifact tree. The owner's
execution log is /tmp/mezan-pr1890-adapter-20261112.log. The following official
status was inactive. No lease or publication had started at that pre-prepare
checkpoint; the subsequent successful prepare/prepublish and single publish
action are recorded in the current continuation above.

The sanitized actual environment was Linux aarch64, host Node24.19.0,
Yarn1.22.22 and Python3.11.18, with cwd /app/frontend and Git present.
The governed build used the intent-pinned Node22.23.2 toolchain. Adapter evidence
still marks the outer platform install/build argv and snapshot timing as
unobserved; the configured entry point was exercised, not inferred as an
observation of Emergent's later Cloud Build internals.

The isolated frontend package contained1436files, tree
667f232ad3928e0c88c3c342293224b107a91cbffe9d21053f24382c345f8872,
with artifact and JSON/MIME contract verified. The isolated backend contained
1176files, treeeb385b18e561533c02138eff21afc827dde05cb0e45f6c5f16680acd6453dbd6;
identity and health verified with no Git or frontend sibling. Its generated
release_identity.json was425444bytes, SHA256
32bb88455bad6c3518026651d17279d252eeca5d758f5dc79558f876ca4a8e3e.
These observed adapter results were added by the release owner after the
independent CI writer's checkpoint.

| Release stage | State at this record |
|---|---|
| PR1890 merge and official Git contract | Complete; M2 tree equals reviewed B |
| Shared /app fast-forward | Complete at M2/tree B; all 120 platform files preserved |
| Actual shared adapter rehearsal against the tracked intent | Passed in65.79s; exact source/runtime/artifact and isolated package proofs |
| Own prepare/prepublish | Passed for the recorded actor and A/M2/runtime; ready_to_publish:true |
| Follow-up publication | IN PROGRESS after one click at approximately 2026-11-12 00:53:58 UTC; UI Publishing... |
| New live runtime identity / official three-probe verify | OPEN; no newer completion or verify result recorded |
| Inactive status after the new verify | Pending |
| Both affected courier previews after the follow-up | Open; no success claimed |
| Same-period Google known-partial display acceptance | Open; no success claimed |
| Physical-paper printing | No success claimed |

The latest completed live-verification evidence remains the preceding PR1887
record described in STATUS.md. No new Production runtime change is established
by this document. Financial PR1889 and unrelated Android/platform work remain
outside this release's source scope.

The next safe steps belong to the release owner: wait for the publication
already started, without repeating the publish action, then run the official
three-probe verification. After exact identity and inactive-status checks,
repeat the authorized courier previews and the
November 11 Google comparison. Keep any incomplete Google account coverage
explicit; do not convert it into an observed zero or a complete profit.

Later evidence must append the exact newer publishing UI wording, official
verification/lease closure and separate live acceptance
outcomes before claiming completion. This documentation-only branch must
never replace M2 as the deployment input.

