# Aramex current-status prepaid shipping recognition

## Active checkpoint — implementation in progress

The owner requested that an order created after operational accounting starts,
currently assigned to Aramex and shipped through Aramex, incur the approved
prepaid shipping charge when its authoritative Salla state is completed, out
for delivery, or delivered. An unavailable execution timestamp must not hold
this explicitly authorized cohort.

The original session authorizes continuation through implementation, review,
merge, publication and live reconciliation. The current instruction refines
the accounting rule. No new external messaging is authorized or needed.

- Task branch: `codex/aramex-status-fee-20261118`.
- Base J: `68035e55018d5b75bdd6b9e0ab6000d7528a956f`.
- Predecessor PR1987 is published, officially verified in three rounds, and its
  lease is inactive according to the final PR handoff.
- No source A, intent B, merge M or deployment for this task is frozen yet.
- This checkpoint is not a production candidate and must not be published.

## Accounting contract

- Creation is strictly after the configured operational opening and no later
  than the source/report observation. Current customized status takes priority.
- Current canonical order, owner, current Aramex carrier and all present
  shipment/waybill references must agree. Missing label fields alone do not
  invalidate this explicit post-opening order-status policy.
- Existing provable execution dates retain their original route and tariff.
  Otherwise the new basis is `aramex_status_created_after_start`; its first
  `recorded_at` is the accounting recognition time and selects the approved
  tariff/cash binding then effective. Actual `completed_at` stays absent.
- Deterministic fact, charge and payment IDs remain per order. Existing
  charges, allocated forecasts, native fees and payouts must prevent a second
  debit. Later status transitions cannot reprice or repeat the recorded charge.
- Fee recognition is independent of actual delivery proof and customer COD
  collection. Completed/in-transit charges must not inflate delivered counts.
- Historical orders, other carriers, existing confirmed facts and the separate
  historical pending-count investigation retain their existing behavior.

## Work and evidence

- Backend prepaid eligibility/reducer implementation and its independent review
  are in progress in isolated task files.
- Reporting now keeps `fee_recognition_basis`, `fee_recorded_at` and
  `fee_observed_order_status` separate from delivery recognition fields.
- The prepaid expense-paid summary includes the new recognition event; courier
  delivery counts exclude it unless a separate delivery fact exists.
- New report regressions first reproduced four failures on the prior code.
  The focused report, existing prepaid-position, party-coverage and financial
  position suites now pass: **53 tests**.
- Frontend labels/export are being updated and checked separately. Governed
  Node 22.23.2 / Yarn 1.22.22 dependencies installed with the frozen lockfile.

## Remaining work

1. Complete the producer/reducer and integrated boundary, identity, tariff,
   wallet, duplicate and concurrent-refresh regressions.
2. Independent patch review and focused frontend checks, followed by required
   full CI with real Mongo and security gates on the exact source candidate.
3. Freeze official v5 candidate A/B on a fresh production base. Preserve the
   ordinary merge ancestry and all release gates.
4. On the shared host, inspect guard status before touching `/app`; perform the
   reviewed adapter rehearsal, fresh prepare/prepublish, one authorized Publish
   and unchanged official three-round verification.
5. Perform the authorized explicit live recalculation and compare the same
   period's Aramex fee/payment/pending counts using private evidence outside Git.

No customer/order records, business totals, credentials or private exports are
included in this repository checkpoint.
