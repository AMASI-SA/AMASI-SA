# Financial center loading recovery — continuation

## Scope and authorization

Continue the owner's existing instruction to repair, merge and publish the
financial-center loading defect. The owner asked to resume from the prior
screenshots and inspect Mezan on 2026-11-14. No payment, settlement, delivery
confirmation, provider synchronization or other business-data write is a test.

## Recovered checkpoint

- Repository: AMASI-SA/AMASI-SA.
- Prior verified Production / source branch base:
  `23cc06da003c18c0f3fc6c2e749aad1326666166` (PR #1918).
- Prior handoff branch: `codex/financial-loading-handoff-20261114`, verified
  remote SHA `078e7fc8c3a146f0d94817fc2347bfcf33b24a17`.
- Prior source branch: `codex/financial-loading-recovery-20261114`; remote still
  points to the base above. The previous four frontend edits and backend
  regression were recovered without altering the original worktree.
- This continuation: `codex/financial-loading-resume-20261114`, isolated checkout.
- Issue #1006 has reached its comment limit. The task document and forthcoming
  PR remain the continuation record. Its last existing entries predate this task.

The prior verified live attempt remains historical evidence: a later refresh
returned HTTP 408 after 85,140.921 ms; reconciliation was cancelled after
34,098.679 ms. The saved-report GET then took 43,589.479 ms. This continuation
directly observed the financial report with snapshot time 14 November 2026,
12:51 Asia/Riyadh, and the explicit timeout/stale-report warning. That view does
not establish a newly successful calculation.

## Acceptance requirements

1. Remove measured repeated reconciliation work without changing financial
   rules, result values/order, nested provider rollback, whole-order rollback,
   exceptions, Decimal behavior, authorization or cancellation semantics.
2. One visible page opening or manual refresh is one bounded load attempt.
   Hiding/offline must end that attempt without automatically recalculating on
   return; an abort-insensitive pending request must retain ownership of its lane.
3. Pending or failed loads must show an explicit recoverable state and preserve
   saved report amounts and timestamps honestly.
4. Verify actual React tests, backend regression/parity and scaling, applicable
   integrated CI, then a fresh v5 source A / intent B / normal merge and governed
   publication. Finish with official verification and visible live acceptance.

## Current evidence and next action

Recovered standalone frontend regression passes; baseline starts 31 requests
across repeated interruptions and fails to terminate an unresponsive request.
The recovered backend regression fails on the baseline: 24 replayed orders copy
4,032 dictionary entries and 48 copy 16,128. This is synthetic operation-count
evidence, not a measured share of live delay.

Actual frontend verification passed under governed Node 22.23.2 / Yarn 1.22.22:
77 focused tests across three suites, and 733 operational workflow tests across
51 suites, both exit 0 with no failed, skipped or pending cases. Dependencies
were copied from an existing tree with identical package/lock bytes after one
offline cache miss; the pinned Yarn integrity check passed. No dependency
manifest changed. These checks mock networking and do not establish live speed.

## Integrated source candidate

Backend repair is complete. The four transaction maps record changed entries
with nested savepoints instead of copying the full history per order. Capture,
refund and shipping selection uses request-local indexes in original map order.
The two order-page observation callsites receive the same ordered fact subset;
standalone observation callers keep their original default. Malformed legacy
facts use the old traversal, including its lazy validation and first-error
behavior. Returned saved-state maps remain ordinary dictionaries.

Root also reran the final 77 financial/helper/auth frontend tests across three
suites, exit 0. An initial root invocation used the wrong working directory and
failed before loading tests; the corrected governed Yarn invocation ran all 77.

Root's fresh final backend selection: **240 passed, four explicitly deselected,
exit 0**. The four cases require isolated real Mongo; a prior attempt failed to
connect locally. Local Mongo 8.0.12 also aborts at CPU discovery even for
`--version`. These four must pass the official exact-source CI before merge:

- `test_real_refresh_persists_unknown_delivery_date_once_without_rewriting_sources_or_cash`
  (both carrier parameters).
- `test_actual_provider_payout_allocates_to_one_split_share_only_and_replay_is_idempotent`.
- `test_frozen_split_order_does_not_turn_into_a_second_bank_receipt_or_cod_collection`.

Independent review: **PASS**, 303 complete-state comparisons against immutable
base engine AND its original order-page helper; 10,000 randomized journal
operations; 13 cancellation scenarios; 59 repository cases, exit 0. Comparisons
cover values, recursive insertion order, exception type/message, Decimal flags,
input immutability and independent money totals. The review caught and the
candidate fixed a malformed-provider whole-order rollback discrepancy and a
remaining quadratic page-observation traversal before source freeze.

At 400 synthetic replayed orders, full-map copied entries fell from 1,120,000 to
zero. Actual string-prefix checks fell from 486,400 to 8,000 for capture-based
orders and from 806,800 to 9,200 for page-balance orders. Candidate checks grow
linearly across 50/100/200/400 orders. These are pure-function operation counts,
not a production latency claim. Timings under concurrent local load are retained
in the evidence but are not treated as a live speed guarantee.

The `verification/` directory retains the differential and boundary harnesses,
result summaries, source hashes and XML evidence. Root owns remote integration
and publication. No accounting rule, deadline/source limit, permission gate,
worker cadence, dependency manifest or existing release intent changed.

Production changed by this follow-up: **no**. No lease or publication started.
The verified frontend checkpoint was preserved remotely as
`b60c94fa4fb9683240883c45bfc891febf90a106` before backend integration.

Next safe action: create the reviewed source checkpoint and Draft PR, run every
applicable exact-source workflow, verify the four deferred Mongo cases in official
XML, then freeze a fresh v5 intent-only B. Require exact B CI, clean-clone adapter
rehearsal, tree-equivalent normal merge, original `/app` preservation checks and
build, prepare/prepublish, explicit deployment success and official three-probe
verification. Finally verify a fresh visible report calculation. Do not deploy
this source candidate with PR #1918's old release intent.

## Release continuation checkpoint — source A

Source A is now remotely recoverable at `5f7fce4fcf1b81f3fbe1093d0983184c281ca733`, tree
`b435301d907e20d8bd354cb48c83dec3959a999d`, with Draft PR #1919. The exact-A Mezan Release
Readiness run [43604085043](https://github.com/AMASI-SA/AMASI-SA/actions/runs/43604085043)
passed. A clean local clone produced two matching builds, official verification
and candidate freezing, all exit 0. Its intent is byte-for-byte identical to the
CI candidate (1304956 bytes; SHA-256 `bb3fcbc5858d2c79c95aa55551a0ad2d0393467ce218fe3fa57984cebe001467`).
The runtime identity is `rg5-9b6621b12a348a9d27542452b3d2040456e720478b9fd0dc3a2b82af27438605`.

Exact-A operational frontend passed 733 tests / 51 suites; synthetic browser
acceptance passed 9 scenarios; canonical-worker exclusion passed 4 tests.
Backend real-Mongo, eligibility and the remaining applicable workflows are
still pending. Source A has not been advanced to intent B. No Production change,
original-workspace source change, lease or publication has occurred.

This documentation-only continuation branch is separate from the release
branch. Never merge it into the frozen release history. Follow RELEASE_CHECKPOINT.json
for identities and next actions. The original /app has 120 untracked files and
one unsaved editor buffer, which must remain preserved.

## Release continuation checkpoint — reviewed intent B

Every one of the 11 applicable exact-A workflows passed. Independent official
artifact validation confirms 4,672 backend cases; 4,481 stock eligibility cases
plus 7 subtests; 733 frontend tests / 51 suites; 9 synthetic browser scenarios;
and 4 canonical-worker cases. All four locally deferred Mongo cases and all 25
new reconciliation regressions passed on both backend suites. See
`verification/sourceA-ci-independent.json` for artifact digests and identities.

The release branch now contains reviewed intent-only B
`b8f50264becf51d3f5aaf279e7cf2ddba1844193`, direct parent A, with tree
`14626f7c528626d422d6098800ac109bcba2984b`. A..B changes only the exact
reviewed intent. Local and remote trees match and the checkout is clean.
Exact-B workflows are in progress, including Readiness run 43604791836 and
both Host20/24 clean-clone adapter rehearsals. Production remains unchanged,
with no release lease or publication. The unsaved editor buffer is Untitled-1;
its dirty indicator remains present. The unrelated missing PR1906 temporary
JSON editor tab was left as found.

## Release continuation checkpoint — merged M

All applicable exact-B workflows passed, including CodeQL and both clean-clone
adapter rehearsals. The independent audit is `verification/intentB-ci-independent.json`.
PR #1919 was merged normally as `4c623c640fb63ac3c138fa4a9cabb20e2b39dca7`.
Root and the independent reviewer verified ordered parents J/B, exact B/M tree
`14626f7c528626d422d6098800ac109bcba2984b`, A ancestry and intent-only A..M diff.

Exact-M Readiness 43605488046, Security 43605488095 and CodeQL 43605488109 are
running. The original /app preflight at 14:31:46 UTC showed HEAD J, tracked clean,
120 nonignored untracked paths and inactive guard. Untitled-1 remains dirty.
Filled release runners are preserved in `release-runners/`; the gate deliberately
still needs the successful rehearsal's private evidence directory. The original
rehearsal may proceed in parallel with M checks, with no lease/publication until
all applicable M checks pass. Live Production remains on the previous release.

## Original preflight environment review

All exact-M push checks and independent lineage/adapter evidence passed; see
`verification/mergeM-ci-independent.json`. The original rehearsal stopped safely
before fetch/fast-forward/build at `/tmp/mezan-pr1919-original-9l31h7ck` because
`backend/__pycache__/` contained exactly two ignored generated .pyc files
(frontend_build_identity and release_protocol_v5, 120563 bytes). No source,
tracked file, untracked file or lease changed in that stopped attempt.

The separately reviewed quarantine helper pins that exact scope, J/branch,
inactive guard, clean tracked state and the saved120-path baseline; it preserves
all cache bytes/modes/inodes via one same-device atomic rename into a fresh
private /tmp directory, and verifies preservation afterward. It performs no
build, source edit, gate change, deletion or automatic restoration. Independent
static review PASS for SHA-256 cde071b8cf0b9aa5d38da95bcd7d1ebbb064c2e36e1f24783f8b89a7753ff191.
Root has not executed this helper yet. Successful quarantine must be followed
by a fresh unchanged original rehearsal; never reuse the failed preflight as a
successful build. Live Production remains unchanged and no lease exists.

## Same-device cache preservation adjustment

The first quarantine attempt stopped before mutation because /tmp and /app
have different devices. Its evidence remains at
`/tmp/mezan-pr1919-cache-quarantine-ct7vu6pc` with cache_quarantined=false.
The cache and original checkout are unchanged. Fresh UI inspection showed
/app/.release-preserved is an existing real root-owned0755 directory on the
same device as /app/backend. Exact __pycache__ payload paths and both pyc files
are ignored by existing rules; the generic parent is not ignored. V2 therefore
keeps all logs/manifests in /tmp and creates only an ignored cache payload under
a new private0700 child of that existing parent. It changes no ignore rule.
Root reviewed the narrow delta and all original preservation/guard checks stay
intact. Reviewed V2 SHA256:
`06e774209be58d3fc347f6645448c311bb3e7a9974f2b84f869828ea32310758`.
Execution has not yet occurred; a successful helper is required before a new
original build rehearsal. Old helper and failed evidence are retained.

## Original governed build in progress

Reviewed V2 cache preservation succeeded at14:46:37UTC, actual helperexit0.
Its evidence is `/tmp/mezan-pr1919-cache-quarantine-p8jfjitq`; payload retained
at `/app/.release-preserved/mezan-pr1919-cache-zi6cl1xa/backend/__pycache__`.
Both files'120563bytes, modes and inodes were preserved; the120-path baseline,
tracked checkout J and inactive guard were unchanged.

The unchanged original rehearsal then started a fresh build-mode run at
`/tmp/mezan-pr1919-original-72x801fd`. Do not assume completion from this
checkpoint. The gate has been filled with that exact directory and frozen;
its checks require the actual successful build evidence before preparing any
lease. No prepare/prepublish/publish has run. AllMCI gates passed already.

## Original build and prepare succeeded

Original `/app/frontend yarn build` completed with actualexit0 in
91.25982752995333seconds. Its materialized identity matches the reviewed intent;
original HEAD is M, tracked state is clean, and the same120untracked files were
preserved. The guard was inactive after rehearsal. Root read the actual
22-build result and identity/preservation/guard evidence, not just a summary.

Official prepare completed with actualexit0 in2.909684497048147seconds at
14:50:45.041309UTC. Lease ownership and exact source/deployment/runtime match
`codex-financial-loading-recovery-1919`; after-prepare preservation passed120.
The Emergent Republish dialog is open with Publish changes available. Its
currently published version remains279b7a3/100thpublish. Prepublish and publication
have not yet run. Next action is prepublish+preservation, then the authorized UI
publication immediately afterward. The prepared lease must not be duplicated.

## Authorized publication started

Official prepublish completed with actualexit0 at14:52:07.717482UTC,
ready_to_publish=true, matching owned lease and after-prepublish120-file
preservation. Root clicked Publish changes once in the already-open Emergent
management dialog. UI changed to disabled Publishing..., confirming the action
started. Observed at 2026-11-14 14:53:16 UTC. Do not repeat that click.
No newer success or public verification has yet been observed. The lease remains
owned by this release until official verification closes it.
