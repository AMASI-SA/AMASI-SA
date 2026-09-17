# Qoyod COD fee recovery — source checkpoint

Status: diagnostic preparation, pending review and deployment; live invoice
root causes and recovery remain unresolved. Do not present this PR as a proven
COD or provider-404 fix.

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

Next safe action: review/deploy the diagnostic source through a permitted
release surface, then inspect one actual saved 404 endpoint/time and one COD
totals breakdown before selecting either financial-path fix. The existing
automatic approval rejection for Emergent/code-server access still applies;
no alternative session, hidden browser state, or direct request workaround was
used. All production invoice/configuration state remains unchanged by this task.

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

Current deployment access blocker: opening an independent code-server page led
to a sign-in screen. A subsequent capability inspection was rejected by
automatic approval review, which interpreted the user's prohibition on Emergent
as covering this code-server interaction. No credentials were entered, no
terminal command was run, and no alternate Emergent session was used. Deployment
requires the user to clarify permission for the deployment/admin surface only,
or an authorized operator to perform the reviewed release. Do not bypass the
rejection. The user prohibition on using Emergent chat remains in force.

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
