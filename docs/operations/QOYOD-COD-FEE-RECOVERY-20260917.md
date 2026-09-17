# Qoyod COD fee recovery — source checkpoint

## Current continuation: product lookup repair

Diagnostic PR #1066 is deployed and verified, with source
`f42b4917bef5c036197c236445bd1f5e5e02417f` and deployment
`5040e0adce41bfbecf60aa790b5e4ea529a0e0a8`. Platform reported deployment
finished at 2026-09-17 17:54 UTC. The guard passed three identity, hash and
frontend probes and closed the owned lease. Canonical evidence is Issue #1006
comment 5718921743. No paid platform chat was used.

Fresh production UI evidence identifies `GET /products` in two saved 404
failures. Read-only COD totals diagnosis also confirms a missing
`default_cod_fee_product_id` and omission of the explicit fee in a fee-bearing
sample. The historical COD source repair remains present. Customer data and
identifiers are intentionally excluded from this record.

Next source branch: `fix/qoyod-product-lookup-20260917`, based on deployment
`5040e0adce41bfbecf60aa790b5e4ea529a0e0a8`. The change repairs the lookup
mechanism: after filtered 404, or nonmatching rows indicating an ignored
filter, read a bounded unfiltered catalog and match exact SKU/reference.
Unknown response shapes, missing/repeated identities, inconsistent totals,
incomplete pagination, auth/throttle/server/network errors and any unfiltered
404 remain failures. No 404 is converted directly to absence. A completed
snapshot is local to the client and invalidated before every product POST,
including uncertain POST outcomes. Existing first-match behavior is retained.

Focused regression reproductions failed on the old implementation at the
product lookup seam. The payment freshness CI command now includes both
manual-client regression modules. Fresh verification and exact source SHA
are recorded in the next Issue #1006 checkpoint. Initial integration runs
lacked synthetic encryption/base-URL configuration; with ephemeral test-only
configuration, the selected payment/idempotency/wire suite passed 23 cases.
No live provider credentials or network calls were used by these tests.

The new product repair is not yet deployed. Actual unfiltered provider
behavior and live backlog recovery remain unverified. No financial retry,
invoice/receipt creation, COD mapping save or quarantine release has occurred
in this task. Verify a real existing COD service product before mapping it;
never use synthetic test IDs. Preserve the prior financial-retry approval
review restriction. Deployment/admin authorization persists; only paid agent
chat is forbidden. Refresh production, CI, ledger and shared lease before
preparing a fresh A/B intent; never reuse #1066's frozen intent for changed code.

## Historical diagnostic preparation record

The following entries describe the earlier preparation stages and are
superseded by the current continuation above where deployment status differs.

## Authorized diagnostic release preparation — 2026-09-17

The user explicitly clarified that deployment administration and terminal access
are authorized. Only the paid Emergent agent conversation is forbidden. This
resolves the earlier overly broad interpretation of that prohibition; do not
request the same deployment authorization again. No paid agent conversation was
used. The separate financial retry rejection remains subject to verified inputs
and its required handoff; deployment permission does not waive invoice safeguards.

All seven workflows passed for development PR #1059 at
`9845a433ef15395e9218b0ed253c2aff3cbe3b21`. A new isolated source candidate on
`release/qoyod-diagnostics-20260917` applies those same diagnostic changes to
reviewed Production base `516e86fafbe88647ce19d7ce200cfdf8f704372e` (PR #1065).
It preserves the intervening Snap30 and attribution changes. The old intent is
unchanged in source A; a fresh governed build must create a new intent-only B.
The original development PR remains the implementation and test record.

Fresh integration verification on this base: the eight-module payment workflow
suite plus COD source regression suite passed 75 tests; the settings, diagnosis,
and unsent-page Jest suites passed 19 tests. Both commands exited 0, and
`git diff --check` passed. This verifies diagnostic behavior and preserved
financial invariants in tests, not live invoice recovery.

Fresh shared-guard inspection found an active lease owned by
`codex-snap30-restore-20260917`, prepared at `2026-09-17T16:08:15.436044Z`,
deploying `99241021fdc8b38efba7eea2c68f3facd569ffbe`. The deployment UI showed
an in-progress build. PR #1065's owner also awaits that lease's closure for its
authorized deployment. This task has not changed `/app`, rehearsed on the shared
workspace, created or cleared a lease, or clicked publish. Prepare and verify
this isolated candidate while preserving both other tasks; refresh the ledger
and require an inactive guard before any shared deployment action.

## Sequential root-cause investigation — 2026-09-17

The user requested root fixes one at a time: first HTTP 404, then explicit COD
fees, then remaining amount/payment failures. Existing source evidence does not
yet identify the failing provider operation for the live generic 404 cases.
The message can originate in customer/product lookup or creation, invoice
creation/readback, or payment. Do not turn all 404 results into "not found" or
release quarantines based on that message alone.

- Fresh UI inspection found 99 generic 404 messages and 24 COD differences of
  approximately 5 SAR in 129 loaded unsent rows. This is a truncated subset,
  not a complete classification of the 190-order header backlog. A later header
  refresh showed 191. Counts have different scopes and must not be equated.
- A fresh status refresh showed last cycle `2026-09-17 15:50`, ready 1,
  scheduled resync 13, quarantined 173, duplicate 0, payment verification 0,
  and existing-in-Qoyod 3649 for that dashboard's range. These observations do
  not prove a successful invoice or full queue recovery.
- Settings reference-list diagnostics from August 12 identify 404 for
  categories, product units, and branches. They are historical configuration
  list failures, not evidence of the endpoint failing the current invoices.
- `order_source.py` at the July 20 repair and production source has the same
  blob `732fe0ed5b40493510e56d9bb3f6f9b1c17a491a`. The old COD source repair was
  not removed. A successful fee-free COD order cannot prove fee-bearing COD
  works. Missing fee mapping remains a hypothesis until read from production.
- Fresh fetched production commit was
  `8e85e772c70c2b49658f5c316ca1182bccbd73db`; Qoyod code is unchanged relative
  to this task's base. Other conversations' Snap source/release work is left
  untouched. No release intent is created or reused by this task.

### Added persisted-error visibility

The unsent read model previously discarded the saved provider endpoint and
timestamp. It now exposes an allowlisted operation, HTTP status, and failure
time from existing open quarantine/failed-lock records. Invoice IDs are replaced
by an endpoint template; unknown endpoint shapes, query strings, request bodies,
and response excerpts are not exposed. Mongo projections retrieve only the
needed diagnostic fields. The UI labels this as a historical saved failure;
opening it makes no new request or retry. The active queue wrapper hides old
failure details after invoice reconciliation and preserves send eligibility.

Verification of this addition: focused backend range/eligibility/memory tests
35 passed; exceptions-page Jest tests 8 passed; `git diff --check` passed.
Fixtures are synthetic. Tests assert unchanged quarantine/lock state, no new
invoice, safe field exposure, and suppression after reconciliation. Existing
Qoyod memory CI already includes all changed test modules.

CI follow-up: the first new source checkpoint passed the Qoyod memory workflow
but its payment workflow exposed test-order sensitivity in the new integration
test: the automatic-send bootstrap had already installed the queue wrapper,
and the test wrapped it again. The test now boots the normal package and calls
the installed public reader exactly once, clearing only the test query cache
between the two fixture states. The full eight-module payment workflow command
passes locally from `backend` (62 tests), and the focused range/eligibility/memory
group still passes (35). This correction changes tests only, not runtime logic.

Next safe action: complete the fresh diagnostic release through the authorized
admin surface after shared deployment ownership is released, then inspect one
actual saved 404 endpoint/time and one COD totals breakdown before selecting
either financial-path fix. No hidden browser state or direct request workaround
was used. All production invoice/configuration state remains unchanged by this task.

Update after source checkpoint `709918aa607486c89c8e21a99ae86973f67cd374`
(Draft PR #1059): all seven GitHub workflow runs passed, including CodeQL,
Security, Release Readiness, and the three Qoyod workflows. Source tree matched
the locally tested tree exactly. A fresh existing UI read-only payment check
returned `review` and a totals mismatch, confirming the live calculation still
fails; no invoice or database change was made. Actual COD mapping remains unread.

PR #1058 has since completed publication and guard verification according to
Issue #1006 comment 5714930182, with owned lease closed. Live activity shows the
historical analytics job as partial and another ordinary sync running; no job
was cancelled or restarted by this task.

Historical deployment access blocker, now resolved by explicit clarification:
opening an independent code-server page led
to a sign-in screen. A subsequent capability inspection was rejected by
automatic approval review, which interpreted the user's prohibition on Emergent
as covering this code-server interaction. No credentials were entered, no
terminal command was run at that point. The user subsequently clarified that
deployment/admin and terminal use are authorized; an existing authenticated
session was then used solely for a read-only guard status command in a separate
terminal. The user prohibition on paid Emergent chat remains in force.

Task branch: `fix/qoyod-cod-fee-recovery-20260917`.
Base: `836d36b831c1cd8aa9915f543f159b4e160ea438` on
`hotfix/prod-snap-meta-final`. The exact checkpoint SHA is recorded in Issue #1006.

## Established evidence

- The existing legacy COD-fee source repair is present at this base. Its 13
  focused backend regression tests pass; there is no evidence it was reverted.
- A synthetic builder reproduction with order total 230 SAR, items 200 SAR,
  shipping 25 SAR, and COD fee 5 SAR is refused with `totals_mismatch` when
  `default_cod_fee_product_id` is absent. Expected total is 225 SAR and the
  reason is `cod_configuration_gap`. Providing synthetic service product ID
  700 produces total 230 SAR, difference zero. No HTTP or DB writes are used.
- The settings page already carried `default_cod_fee_product_id` in its save
  payload but provided no input to inspect or correct it.
- The existing GET totals diagnosis endpoint reports the stored source amounts,
  mapping, inclusion of COD fees, and exact difference without invoice creation.
- The actual live COD mapping has NOT been read or verified. A missing mapping
  is a reproduced mechanism, not yet the proven cause of the live backlog.
  Existing quarantined errors may also predate the current calculation.

## Changes

- Expose the existing COD service-product mapping in Qoyod settings, accepting
  positive integer IDs and preserving current credential handling. No ID is
  assigned automatically, and a blank mapping remains allowed for fee-free orders.
- Add a per-order read-only totals diagnosis to unsent orders. Display source
  total, expected total, difference, explicit COD fee, product mapping, and
  inclusion status. Missing/error results are never displayed as monetary zero.
- Keep invoice sending, payment writes, quarantine release, duplicate checks,
  tax policy, totals tolerance, and backend arithmetic unchanged.
- Add UI regression coverage and include diagnosis tests in existing Qoyod CI.

## Verification

All fixtures use synthetic identifiers and amounts; no customer records or
credentials are included in this checkpoint.

- Focused Jest run of `QoyodSettings.credentials.test.jsx`,
  `QoyodTotalsDiagnosis.test.jsx`, and `QoyodUnsentOrders.test.jsx`: 18 passed,
  exit 0. This used an isolated Jest 29/React 19 dependency harness; repository
  CI and the governed full release build remain separate requirements.
- `python -m pytest -q backend/tests/test_order_engine_cod_fee_source.py`:
  13 passed, exit 0, with isolated test dependencies and bytecode disabled.
- Synthetic payload reproduction described above: absent mapping refuses the
  structural gap; mapped COD fee produces exact parity, without HTTP/DB writes.
- `git diff --check`: exit 0.
- No release intent, deployment lease, shared runtime, or production data changed.

## Blockers and next safe actions

1. Automatic approval review rejected a live retry confirmation because it
   could create a financial invoice with an unresolved amount mismatch. The
   confirmation was cancelled; no retry or invoice was executed. Do not evade
   this rejection through another client or route. Financial completion needs
   verified inputs and the required user handoff.
2. The current browser could not open the existing read-only API URL directly
   (`ERR_BLOCKED_BY_CLIENT`). No hidden state, credential extraction, or alternate
   request client was used to bypass it. The new visible UI provides diagnosis
   once properly deployed.
3. Historical coordination evidence: other conversations were operating on the same production deployment and
   analytics worker. Issue #1006 comment 5714733213 reports a new PR #1058
   publication in progress and owned active lease; comment 5714777079 reported
   an overlapping active historical analytics job. See the newer update above;
   do not assume either historical active state is still current.
4. Review this source-only PR and its CI. Before any production merge/rehearsal,
   refresh the ledger and actual Release Guard status, reconcile the current
   deployment and background job, and prepare a fresh governed A/B intent for
   this source if deployment is available. Do not reuse the old intent.
5. After verified deployment, use the read-only per-order diagnosis to establish
   the actual live COD mapping and amounts. If missing, verify the existing
   Qoyod service product identity before saving it; never guess an ID. Recheck
   exact totals and current invoice presence before any allowed recovery.
6. HTTP 404 and non-COD failures remain a separate unresolved part of the backlog.
   No full-queue completion, live COD recovery, or token refresh is claimed.

Production changed by this task: **no**. This checkpoint is not a deployment
candidate until review, CI, concurrency coordination, and Release Guard gates pass.
