# Closed 404 recovery — review and rollout

The closed cohort contains 199 references. Two previously verified orders are
excluded from all financial actions; 197 remain. The full private cohort file
is imported in the UI. The server validates its exact count and SHA-256 digest;
an altered, shortened, expanded or duplicate-containing list cannot activate.
Only hashes of the cohort and the two completed references are tracked here.

## What changes

The ordinary worker permanently excludes open 404 quarantines. The new worker
lane re-evaluates this reviewed cohort without globally releasing quarantines.
Preparation is local bookkeeping only. Deployment and page rendering cannot
activate it. Activation binds the cohort, exclusions, dates and verified runtime
release identity. Existing armed/unified-worker settings must also permit work.

Each tick processes one order. Full Salla refresh precedes evaluation; totals
come from the refreshed Salla source, not the merged historical table value.
Salla is refreshed again at the sender boundary and the existing sender refuses
if its resolved total differs. COD, missing SKU, changed eligibility and payment
failures receive explicit per-order outcomes. They are not silently marked done.

A fresh complete provider invoice scan preserves duplicate references. Exactly
one matching, fully paid invoice with matching total is reconciled locally.
Existing unpaid invoices are blocked; this recovery never automatically enters
the payment-only retry branch. New sends retain the existing invoice/payment
idempotency locks, mappings and amount guards. Completion requires independent
provider readback plus the correct local accounting marker.

The `qoyod_404_attempts` primary key is tenant/reference, independent of campaign.
Claims have no TTL and are never automatically deleted or reclaimed. Scheduling
leases expire after ten minutes; a surviving cursor is audited read-only, never
sent again. An unknown result pauses the campaign. A paused interrupted attempt
can be audited after its scheduling lease expires, including after a process
restart. An absent invoice after an attempted send is not permission to resend.

The status endpoint and UI list every reference, outcome, reason and invoice.
Counters are derived from these rows. `review_complete` means all rows have a
recorded disposition; it does not mean every row succeeded. The remaining count
includes blocked/review/unknown rows until their accounting evidence is verified.

## Validation and limits

- Core tests: `python backend/tests/test_recovery_404_isolated.py`.
- HTTP/Mongo/worker/provider-adapter tests:
  `python backend/tests/test_recovery_404_integration.py`.
- Actual sender regression tests include `test_recovery_sender_boundary.py`,
  `test_qoyod_manual_send_plan_b.py`, actual-total, fail-closed client and product
  lookup tests. Use synthetic encryption configuration and `QOYOD_API_BASE` on
  the reserved `.invalid` domain; do not load production environment files.
- Real React/DOM test: `node frontend/scripts/test-qoyod-404-ui.cjs`, with React,
  react-dom, jsdom and esbuild installed in the selected `UI_TEST_MODULES` folder.
- Integration Mongo and providers are substitutes, not live services. A complete
  fresh provider scan may be slow. Unknown response schemas/pagination/settlement
  stop processing; no absence or payment completion is inferred from timeouts.
- Qoyod native unlabelled monetary values use the tenant's existing SAR ledger
  contract. Explicit non-SAR currency is rejected, not converted.

## Required production handoff

Review corrections: control-plane POSTs require an authenticated actor with an
explicit owner role and matching campaign ownership. Employee `created_by`
membership is not write authority; existing GET visibility is unchanged.
Status exposes server-computed `can_audit` and `audit_block_reason`. A paused,
expired scheduling lease may be audited even with a stale busy bit. The audit
route rechecks eligibility and acquires its atomic lease; neither the UI flag
nor scheduling expiry releases a permanent financial attempt claim.

Fresh correction validation: 178 backend tests and 35 subtests, 8 existing page
tests and the real React DOM recovery test. These include owner-only mutations,
unchanged employee display access, expired/live lease controls, overlapping
audits, and no financial writes during audit. Full governed frontend build and
CI must be verified on the new commit, not inferred from the prior review.

1. Review source PR and CI. Merge source A only after review.
2. Follow repository AGENTS.md protocol v5: build reproducibly from A, freeze
   intent-only B, review/merge without squash or rebase. Check `/app` lease status
   before touching it. Complete the governed rehearsal, prepare and prepublish.
3. Publish only through Emergent management, never its paid conversation. Require
   a newer visible Deployment Succeeded and successful three-probe guard verify.
4. In Qoyod exceptions, import the original private cohort JSON and select
   **تجهيز النطاق للمراجعة — دون إرسال**. Check 199 total / 2 completed / 197
   remaining and the two excluded references. No financial action occurs here.
5. The user reviews and checks the confirmation, then presses
   **تفعيل التعافي التلقائي للنطاق المحدد** once, subject to automatic review.
   The agent must not invoke the activation endpoint, terminal or another path
   to bypass the earlier financial-action handoff restriction.
6. Observe every result. Pause stops new work; it cannot undo a request already
   in flight. If paused after timeout, use read-only audit; never resubmit it.
   Record verified successes, existing-invoice reconciliation and every unresolved
   reference/reason in the private report and aggregate continuation in Issue #1006.

This change is not permission to deploy or activate. At the review checkpoint,
production remains unchanged and the 197 remaining orders have not been processed.
