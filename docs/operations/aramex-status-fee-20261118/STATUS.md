# Aramex current-status prepaid shipping recognition

## Active checkpoint — source complete, release gates pending

The owner requested that an order created after operational accounting starts,
currently assigned to Aramex and shipped through Aramex, incur the approved
prepaid shipping charge when its authoritative Salla state is completed, out
for delivery, or delivered. An unavailable execution timestamp must not hold
this explicitly authorized cohort.

The original session authorizes continuation through implementation, review,
merge, publication and live reconciliation. The current instruction refines
the accounting rule. No new external messaging is authorized or needed.

- Task branch: `codex/aramex-status-fee-20261118`.
- Draft PR: https://github.com/AMASI-SA/AMASI-SA/pull/1988.
- Base J: `68035e55018d5b75bdd6b9e0ab6000d7528a956f`.
- Predecessor PR1987 is published, officially verified in three rounds, and its
  lease is inactive according to the final PR handoff.
- The commit containing this checkpoint is the completed source candidate A.
  Its exact GitHub SHA is recorded in PR1988 before candidate intent review.
- Intent B, merge M and deployment remain pending. Do not publish source A;
  complete the reviewed v5 intent-only handoff and official release gates.

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
  charges, allocated forecasts, native fees and order-linked payouts must prevent a second
  debit. Later status transitions cannot reprice or repeat the recorded charge.
- Fee recognition is independent of actual delivery proof and customer COD
  collection. Completed/in-transit charges must not inflate delivered counts.
- Historical orders, other carriers, existing confirmed facts and the separate
  historical pending-count investigation retain their existing behavior.

## Work and evidence

- Backend prepaid eligibility/reducer and single-pass present-reference conflict
  index are complete. Canonical order status, exact approved Aramex ownership,
  optional label agreement and frozen reference protection are retained.
- Existing dated events retain their original route. The new status route is
  enabled only by the specific missing-completion-time error; invalid or
  contradictory actual evidence does not silently fall back.
- Reporting now keeps `fee_recognition_basis`, `fee_recorded_at` and
  `fee_observed_order_status` separate from delivery recognition fields.
- The prepaid expense-paid summary includes the new recognition event; courier
  delivery counts exclude it unless a separate delivery fact exists.
- The courier table exposes its existing distinct `confirmed_orders` count as
  "طلبات محتسبة الأجرة" alongside independently proved deliveries. A fully paid
  prepaid fee therefore remains visibly counted even with zero payable balance.
- New report regressions first reproduced four failures on the prior code.
  The focused report, existing prepaid-position, party-coverage and financial
  position suites now pass: **53 tests**.
- Frontend fee time/status, independent delivery basis, approved cash account,
  export fields and neutral prepaid subtotal pass **149 tests / 3 suites**.
  Governed Node 22.23.2 / Yarn 1.22.22 dependencies installed with the frozen
  lockfile. Independent review found no blocking report/frontend issue.
- Fresh read-only inspection of the shared host confirmed base J, no tracked
  changes and `active:false`; no shared source mutation or publication occurred.
- Final integrated backend slice: **490 passed, zero failures/skips**, including
  source-to-ledger policy, all three undated statuses, optional/full labels,
  approved name/code variants, strict dates, source/identity conflicts, tariffs,
  foreign customer currency, native overlap, immutable replay and existing
  prepaid/completion/capture/position/admission behavior. This includes the
  earlier 53-report slice; the counts must not be added.
- Two concurrent explicit refreshes for each new status produce one wallet
  movement; the idle canonical worker produces none. Six new source-to-ledger
  regressions first failed on the old code before the implementation.
- Independent final code/acceptance review found no unresolved blocking issue.
  Review corrections closed omitted raw references, cross-order raw/current
  reference conflicts, contradictory/legitimate Aramex name variants and
  approved name-only/code-only representation mismatches.
- Local Mongo was not running on `127.0.0.1:27305`: ten additional real-Mongo
  review cases failed at connection setup before application assertions. No
  waiver or skip was added. The official real-Mongo CI gate must pass.

## Remaining work

1. Required full CI with real Mongo and security gates on the exact candidate.
2. Freeze official v5 candidate A/B on a fresh production base. Preserve the
   ordinary merge ancestry and all release gates.
3. On the shared host, inspect guard status before touching `/app`; perform the
   reviewed adapter rehearsal, fresh prepare/prepublish, one authorized Publish
   and unchanged official three-round verification.
4. Perform the authorized explicit live recalculation and compare the same
   period's Aramex fee/payment/pending counts using private evidence outside Git.

No customer/order records, business totals, credentials or private exports are
included in this repository checkpoint.
