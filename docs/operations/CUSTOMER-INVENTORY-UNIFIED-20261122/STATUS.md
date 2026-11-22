# CUSTOMER-INVENTORY-UNIFIED-20261122

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
