# CUSTOMER-INVENTORY-UNIFIED-20261122

## Owner correction — return to unified release work

On 22 November 2026 the owner clarified that the page-slowness message was
sent to this conversation by mistake and instructed this task to ignore it.
The performance investigation is withdrawn from this task. Do not request a
Network capture, add a performance repair to this release, or make that
misdirected report a new release gate.

The valid result to use is the owner's execution of the publication-inspection
code: original guard `active:false`, tracked-clean
`hotfix/prod-snap-meta-final` at
`06763ffd6e3c3f7c861769b61d2c4e6b9b2d5f35`, inherited intent source
`063611ff163c90d80cdbf96446a28584fe429846`.
`target_source_in_intent:false` confirms that unified A2
`4e6c8a61bd1566e061a4f312e797ce239b582475` is not the staged release.
The inspection did not close or change a lease; it observed an already inactive
guard. A fresh actual guard check remains required before future host changes.

PR #2074 and its exact-source proof are unchanged. No final B, merge or
publication of unified A2 occurred. Continue the original unified release
scope under the owner's instructions; no performance follow-up is pending.
This evidence branch is documentation only and must not be deployed.

## Verified integration checkpoint — publication deferred

This is a post-source evidence record. **Do not deploy this evidence branch.**
The verified source integration point remains Draft [PR #2074](https://github.com/AMASI-SA/AMASI-SA/pull/2074) on
`codex/customer-inventory-unified-r2-20261122`. The owner's latest direction
is to finish combining and verifying these scopes and defer publication until
further operations join one release.

- Exact source A2: `4e6c8a61bd1566e061a4f312e797ce239b582475`.
- Exact source tree: `8f99772552fba3df9a14f55952648aa5d0ed5408`.
- Reviewed current Production M/J, the sole parent:
  `06763ffd6e3c3f7c861769b61d2c4e6b9b2d5f35`.
- Final remote Production check still matches M; PR #2074 is open, draft,
  unmerged, mergeable and clean. Source and inherited intent are unchanged.
- Complete source ranges from PR #2065 and #2067 are retained. Those PRs stay
  open and unmerged with publication holds. Prior unified PR #2073 was closed
  without merging after its source was carried onto M; its branch and proof
  remain preserved.

### Exact-A2 acceptance

All **17 workflows passed**. The full source-bound record and artifacts are
in [EVIDENCE-4e6c8a61.json](EVIDENCE-4e6c8a61.json).

| Check | Result |
|---|---|
| Operational Backend, real isolated Mongo | 6,502 passed +90 subtests; zero failures/errors/skips |
| Durable demand store/child/HTTP/builders | 93 passed; zero failures/errors/skips |
| Shipping summary/calculator | 16 passed +18 subtests |
| Operational Frontend | 1,620 passed in 103 suites |
| Browser acceptance | 33 passed (14+12+7) |
| Stock eligibility/transactions | 5,525 passed +97 subtests; zero failures/errors/skips |
| Canonical worker | 4 passed |
| G47, Security Gate, both CodeQL languages and other applicable workflows | Passed |
| Governed source build and candidate | Two identical passes; original external candidate validator passed |

Counts overlap and must not be added as unique tests. All three unified
supplier/customer/demand regressions executed on the CI real replica set.
They preserve bank 969.20, supplier 30, the original 930 historical cash seal
and provider gross 200 / fees 4.60 / net 195.40 while refund 0.80 and collection 50
remain unconfirmed. Read-only demand projections do not alter money; stale
source specs are rejected.

The inventory browser artifact was downloaded and its ZIP hash verified.
All 58 source hashes match A2; seven original screenshots were reviewed with
no material overlap/clipping. Desktop at 70%, the single editor, compact drafts,
mobile layout and exact later receipt of 2 out of 4 units all passed, with zero payment calls.

Fresh demand benchmarks use synthetic Mongo/ASGI, not live/browser timing.
Home cold first-ready HTTP 200 was 1457.763 ms and ready p95 was 5.289 ms.
The register reduced report builds 20→1 (95%), with ready p95 6.597ms,
equal financial results and unchanged source. These measurements do not
assert Production latency.

### Source-build proof, not final publication identity

Readiness run [45049380655](https://github.com/AMASI-SA/AMASI-SA/actions/runs/45049380655)
generated candidate artifact 14763769992. Archive SHA256:
`8e1295ba78c61650d5f64e277b620187099e5c4b428de1162463ded462f9705f`.
Candidate JSON SHA256:
`762a168ddd6bbe86afd29dd6ed8b5add13d33c426d2edc1fbf4d934c772326ab`.

The unchanged original candidate validator passed in a separate clean exact-A2
clone, with candidate outside its checkout. It bound M/A2, all source manifests,
the two equal artifact/build-meta passes and runtime candidate
`rg5-2e0e3797d6ff3b955a3a23fd31487bd92721e70f316f3bdf34cab0d94c5d630f`.
This is **not a deployed identity or frozen B**. The Node 20/24 host dispatcher
contracts passed; full intent-aware adapter rehearsal jobs were intentionally
skipped for source A and must not be reported as executed.

### Continuation and publication hold

The integration work is complete and remotely recoverable. Add the remaining
owner-specified operations to the unified source after reviewing current
Production and the canonical [Issue #1006 record](https://github.com/AMASI-SA/AMASI-SA/issues/1006#issuecomment-6238987123).
Any source change requires a new exact-source candidate and verification;
do not reuse A1 or A2 artifacts for a different source.

No final B, Production merge by this task, shared `/app` mutation, Release
Guard lease, adapter rehearsal or publication occurred. No live business
test writes or paid Emergent chat. PR #2069's separate serial handoff is
preserved; this record does not assert its live outcome. The earlier browser
connection/certificate observations are retained below for future access
recovery, but the present publication hold is the owner's explicit direction.

After the final combined scope is complete and the publication hold ends,
follow unchanged AGENTS.md: fresh actual inactive guard; new intent-only B;
exact Node 20/24 and official shared adapter proofs; preserved current
Production and unrelated host files; prepare/prepublish immediately before
one publication; newer explicit Deployment Succeeded; original three-probe verification with
matching identity/files/build/JSON MIME and final inactive guard.

## Frozen A2 source-status snapshot

The text below is the source-time preparation snapshot. The verified
post-source section above and the canonical ledger supersede its pending-CI
instructions.


## Current r2 integration after concurrent Production advance

The owner's publication deferral remains in force. Continue integrating the
two approved scopes and preserve their source until further operations join;
do not create a final B, merge Production, rehearse on the shared host, take a
lease or publish this task.

During final verification of the first unified source, another authorized
task merged demand-only operational reads PR #2069 into Production:

- New reviewed base M/J: `06763ffd6e3c3f7c861769b61d2c4e6b9b2d5f35`.
- New base tree: `057c273506c237af2035c7976cc7fe8e9aacaeb2`.
- Current integration branch: `codex/customer-inventory-unified-r2-20261122`.
- Previous combined A1: `1679bb225460688c94a5d60f4b407cedef3fc502`, tree
  `cfe2530dfbc3b7c7c0d77f8f7a5aafa9162a3b11`, preserved in Draft PR #2073.

PR #2069's ledger records its own serial shared publication handoff. This
task does not touch that handoff or infer its live outcome. Its current
Production source is now the base to preserve, not an extra unmerged scope.
AGENTS.md is unchanged. The complete old-J..A1 source delta is reapplied onto
a fresh branch from M, retaining M's exact intent bytes. Neither old candidate
intent is imported. A new source checkpoint has M as sole parent so the full
intent-path history remains empty. Its exact SHA/PR are recorded in Issue #1006
and the new PR after creation; do not use A1's candidate for r2.

Fourteen paths overlap with the demand-read Production changes. Actual
conflicts were resolved in the operational workflow and five frontend files
(ApprovedDirectory, ApprovedEntry tests, ApprovedFinance, ApprovedLedger and
ApprovedOperations). Preserve the demand-only snapshot/read hooks and their
tests alongside customer confirmations and all inventory behavior. The
workflow keeps both test collections and both completeness guards; no test is
removed to resolve a conflict. Independent review of backend automatic merges
and demand context/builders found no missing financial or permission data.

### r2 local verification and integration regressions

- Root final seven-suite backend run: **115 passed**, exit0, 19.59 seconds,
  using the unchanged isolated command below with `--noconftest` and
  `--asyncio-mode=auto`. This is isolated local evidence, not a real-Mongo claim.
- Governed Node22.23.2/Yarn1.22.22 frontend run: **272 tests /19 suites passed**,
  exit0, 10.299 seconds, `--resetMocks=false`. Includes demand/loading/lifecycle,
  entry/finance/ledger/tools, client APIs, customer recovery, options/purchases,
  partial receiving, workflow and command storage.
- Four added UI regressions cover customer links with empty demand bootstrap
  resources, unchanged capabilities, snapshot time and separate adjustment
  badges on provider cards/ledger, without loading unrelated account lists.
- The unified backend file now has **3 tests**, all explicitly required by CI.
  Its new demand case rejects a stale source spec after financial commands,
  retains bank969.20 and supplier30, and displays pending refund0.80 and
  collection50 through the actual demand resource/provider-ledger builders.
  Two confirmed100 payments retain gross200/fees4.60/net195.40; read-only
  display does not execute either pending amount or alter stored state.
- Independent CI union review confirms all test paths, globs, required suites,
  demand benchmarks and no-skip guards from M and A1 are retained. The three
  other demand-modified workflows and AGENTS remain exactly M. Final staged
  whitespace checks pass and no unresolved index stages remain.
- The source delta has **97 paths**: all94 paths from A1 and three additional
  adjacent frontend test files. The inventory implementation and harness stay
  complete. Both input standalone PRs and all frozen identities are preserved.

### Preserved first-source evidence; not r2 acceptance

All **17 workflows passed** for A1, including Operational Backend **6,459 +90
subtests**, Frontend **1,557 /98 suites**, Stock **5,525 +97 subtests**, all
**33 browser scenarios**, G47, Security Gate, both CodeQL languages and Release
Readiness. The two new supplier/customer financial interleaving tests ran on
CI's real isolated Mongo replica set; XML has no skips/failures/errors.
Seven original inventory images were reviewed, all57 harness source hashes
matched A1, and receipt-only acceptance made no payment call. Counts overlap.

A1 readiness run `45047228015` generated candidate artifact `14764018079`;
archive SHA256 `d6bebcb5e56fe2b73ed712fb81d123a2b29eb625a85834cc1807374931018340`.
The original candidate validator passed outside a separate clean A1 checkout,
including two equal governed builds. Candidate runtime
`rg5-d87cdf54baba47a7166459dd423596d81995acfd3ec653a4db35ca7138d51b97`
was never frozen into B or deployed. Browser artifact `14763484646` from run
`45047228010` has SHA256
`23850d1321af70e757372fd71af182d8263ea2664314811d833592fd1968c846`.

**Next safe action:** checkpoint the verified r2 integration and run fresh
r2 source CI/build/browser proofs, update the bounded coordination
section and retain the integration PR for the remaining owner-specified work.
The old source results below remain historical evidence, not proof for r2.

## Historical first-source checkpoint

## Authorization and publication ownership

The owner explicitly authorized integration, review, merge and one governed
publication of customer refund/collection confirmation plus inventory options,
purchasing and later receiving to https://mezansalla.com, Emergent project
`salla-analytics`. This task owns preparation and publication of that combined
scope. Standalone publication from PR #2065 or #2067 is superseded.
This coordination statement is not a Release Guard lease.

**Latest owner direction:** continue combining and verifying the two scopes,
but defer publication because further operations will join the same release.
This supersedes the earlier immediate-publication sequence. Keep the combined
source remotely recoverable; do not freeze a final intent, merge Production,
claim a lease or publish this partial release while the remaining scopes are
being collected. Source validation may continue. The final expanded source
will require a new exact-source candidate and its own release verification.

Repository: `AMASI-SA/AMASI-SA`.
Integration branch: `codex/customer-inventory-unified-20261122`.
Reviewed Production J: `9b4fb716b430c32073b5ea7abaeaff62646d9633`.
Customer source: `4c308b19bb5d972bea5fff092bc0efbe9b70b29f` (PR #2067).
Inventory source A3: `5d09108e6d33ed95b7b2946cdb5a382a49aefae7` (PR #2065).
Inventory standalone B: `9de88ea18b194d9d6b3e964642ae4e06e58a42e1`, excluded.
The exact new source SHA and PR URL are recorded in the branch/PR and the
bounded CUSTOMER-INVENTORY-UNIFIED-20261122 section of Issue #1006 comment
6238987123; this source document cannot contain its own commit SHA.

## Source integration

Both complete binary source deltas from J were applied to a fresh branch from
J with three-way index application. They applied cleanly. The new source has
J as its sole parent, retains J's exact release-intent bytes, and imports
neither standalone intent commit. Preserve source A* and intent-only B*
without squash, rebase or amendment after intent freeze.

The original deltas contain 51 customer paths and 45 inventory paths, with
four shared paths: `.github/workflows/operational-balance-web.yml`,
`backend/operational_balance_routes.py`, `backend/operational_balance_service.py`
and `backend/operational_financial_activity.py`. Their combined changes are
retained. Inventory's `backend/operational_atomic.py` is retained as well.
Additional unified changes are this status, supersession notices in both
source status documents, and a two-case integration regression included in
the operational CI command and required-suite assertions.

Independent source reviews found no semantic collision in shared money,
snapshot or receipt handling. All 23 inventory frontend files and six harness
files match A3 exactly; all 19 customer frontend files match their source
exactly. Dependencies and lockfiles are unchanged. All 14 changed frontend
test files and the original nine new backend suites remain collected by CI.

## Behaviors and limits preserved

- Independently confirmed original customer payment remains recognized when
  an order total changes. Pending refund/collection differences are separate
  from ordinary commission warnings and do not move money until confirmation.
  Execution changes the actual selected bank/provider once, preserves paid
  coverage after late evidence and applies the actual collection fee basis.
- Stable inventory option IDs, dependent-value cleanup and distinct optional
  blank/no/explicit deferral semantics remain intact. Required physical
  options cannot be deferred. The purchase form keeps one editor, compact
  newest-first editable drafts, adjacent metadata and the desktop 70% layout.
- Supplier obligation, physical receiving and payment are separate. Saved
  unreceived invoices enter the later-receiving queue immediately. Partial
  receipts retain original invoice evidence and cannot repeat on retry.
- Allocating an old supplier payment creates no new cash movement and does
  not deduct the supplier total twice. Historical cash seals remain tied to
  their validated attribution ancestry; missing evidence never backfills a
  historical balance from today's state.
- Existing stock valuation and dispensing controls remain unchanged. No
  customer-order stock consumption is enabled and no historical invoice is
  marked received without evidence. Unrelated PRs remain outside this scope.

## Verification at source checkpoint

The isolated new integration suite passes **2 tests**, exit 0:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=backend:backend/tests <test-python> -m pytest -q backend/tests/test_customer_inventory_unified_activity.py
```

Its independent money assertions exercise a supplier invoice 100, old bank
payment 70, customer collection 40 and refund 0.80 followed by allocation 50
of the old payment. The bank remains 969.20, supplier net payable remains 30,
invoice settled/outstanding remains 50/50 and the old bank snapshot remains
930. Late matched evidence and immutable retries do not repeat cash or reopen
customer remainder. A damaged allocation ancestry copy leaves the old
supplier snapshot unavailable while retaining valid customer cash snapshots.
All data is isolated and synthetic; no live financial operation was used.

The workflow is valid YAML, the changed test lists are complete, and
`git diff --check` passes. A root combined run of the new integration suite,
inventory options and the five customer suites passed **114 tests**, exit 0,
using `--noconftest --asyncio-mode=auto`. An initial broader local attempt
including real-Mongo purchase suites was interrupted after failure markers
(exit 130); it is not passing evidence. A direct read-only ping confirmed
the isolated test Mongo endpoint at 127.0.0.1:27305 is unavailable with
ServerSelectionTimeoutError / connection refused. The unchanged CI job
provides the required real replica set; no production database substitution,
skip or timeout relaxation is used. Earlier source CI results in PR #2065 and #2067
are reference evidence only. This combined source requires its own full CI,
real-Mongo contracts, frontend suites, 33 browser scenarios including seven
inventory scenarios with source-bound screenshots, Stock/G47/security checks,
and governed clean A/B build/candidate validation. Node20 and Node24 adapter
rehearsals require the newly frozen combined intent B*.

## Release state and exact next safe action

Production changed by this task: **no**. No production merge, shared `/app`
source change, Release Guard lease, adapter rehearsal or publication has
occurred. No combined intent B* exists at this source checkpoint.

The historical ARAMEX section reports an active lease, but PR #2069 records
a later actual inactive observation at 2026-11-22 16:14:07 UTC. These records
do not establish the current guard state. Obtain a fresh successful actual
`cd /app; python scripts/production_release_guard.py status` observation of
`active:false` before changing shared `/app` or beginning a release rehearsal.
Never clear a foreign lease. The management browser has suffered connection
timeouts; no fresh terminal/guard result has been obtained for this task yet.
Native browser fallback subsequently displayed NET::ERR_CERT_AUTHORITY_INVALID
for app.emergent.sh; no security warning was bypassed. Management access was
left unchanged when the owner deferred publication. These observations do not
prove the Emergent service itself is unavailable.

Finish exact-source CI and review the new candidate outside the worktree.
Keep the source PR as the integration checkpoint for the remaining operations;
do not execute publication steps while the owner's deferral is in effect.
After the remaining approved scopes are integrated and their exact new source
is verified, and only after the inactive-guard prerequisite, freeze a new intent-only B* and
complete its Node20/24 clean-clone rehearsal gates. Recheck Production J and
other coordination work, merge without changing the governed source tree,
safely update `/app` preserving unrelated files, and run the official adapter
entry point. After verified rehearsal only, prepare the owned lease,
prepublish immediately before one Manage Publishing / Re-publish changes
action, observe a newer Deployment Succeeded, and run the original guard
verify command for all three exact-identity probes. Completion requires
verified:true, matching files/build/JSON MIME and final active:false.

Use management UI and terminal only; no paid Emergent chat. Live checks are
read-only. Record final PR, A*/B*/deployment SHA, CI/artifact links, result and
remaining blockers in the same bounded Issue #1006 coordination section.
