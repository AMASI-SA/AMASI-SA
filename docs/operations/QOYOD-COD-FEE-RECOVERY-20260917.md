# Qoyod COD fee recovery — source checkpoint

Status: partial repair, pending review and deployment; live invoices unresolved.

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
3. Other conversations are operating on the same production deployment and
   analytics worker. Issue #1006 comment 5714733213 reports a new PR #1058
   publication in progress and owned active lease; comment 5714777079 reports
   an overlapping active historical analytics job. Do not alter either task.
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
