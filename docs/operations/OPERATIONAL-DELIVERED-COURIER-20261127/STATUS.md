# Operational balances — post-cutover delivered courier fees (2026-11-27)

## Scope and owner policy
Mezan 2 **operational balances only**, not general ledger or Qoyod.
For iMile/SMSA and other positively identified approved couriers, a genuine
delivered shipment after the owner's operational cutover
(`2026-10-03 00:00 Asia/Riyadh`) earns **one** approved shipping obligation
against its courier. The order's creation may precede that cutover; for such
orders a distinct trustworthy delivery occurrence is mandatory. Reconcile on
source date, never generate artificial daily duplicates or retroactive customer
sales, supplier charges, bank receipts or COD remittances. Preserve all opening
balances, payouts, native postings and existing allocations.

## Evidence and bug
The owner provided Production UI screenshots showing iMile entries only through
2026-11-20, one manual payout 2026-11-21, and many later delivered orders,
while Salla's operational balance subreport shows saved as_of 2026-11-26 23:36
Riyadh. The latter proves a saved report time, not that every courier was
recognized. Screenshots/customer financials are not checked in.

In production base `ca9a80829c639fad7f22ed44ae685397854eb9ac`,
`operational_balance_sources.collect_sources` unconditionally skipped
`created <= start` when `uses_retained_deliveries(now)` was true (since
2026-11-01). The already audited dated old-order delivery-only path is then
unreachable despite delivery after the owner's opening. A narrow patch removes
that skip; it reuses the original strict `resolve_delivery_event`,
`_attach_delivery_sources`, prior-fee, tariff, native-overlap and CAS guards.
All other order cohorts and financial computations remain untouched.

## Remote task state
- Repository: `AMASI-SA/AMASI-SA`.
- Draft PR: #2146 against Production; branch
  `codex/operational-delivered-courier-cutover-20261127`.
- Source patch: `backend/operational_balance_sources.py`.
- Test suite: `backend/tests/test_operational_balance_delivered_courier_cutover.py`.
- Specific tests: old order proven delivered 2026-11-22 after Oct 3 cutover,
  original date, exactly one shipping fee with no replayed old sale, exclusion
  of pre-cutover delivery, no invented event date, old/new order replay and
  exact iMile/SMSA mapped courier identities. Explicit prior read-only balance
  preserved.
- Source/test checkpoint before this document: `90a5dcd0af40f81983a07305daee1f33f2c1d5ca`.
- Production changed by this task: **NO**.
- Applicable PR full CI runs started but results are **pending**. CI results
  must be checked against the exact final SHA.

## Critical remaining investigation
This is one proven *code-path* defect but does **not** establish that it caused
every missing Nov 21+ fee. Many later deliveries may be on orders created
after Oct 3 and already enter the retained path. Production owner-only
read-only analysis should group missing courier issues by source code and
inspect scheduler `/operational-balances?view=tools` status, tariff validity,
canonical Salla source, registered delivery status/date, courier identity,
native postings and any pooled/unallocated prior courier payments. In
particular `hold_prior_courier_event` conservatively blocks an unallocated
outgoing courier payment for a newly observed delivery. The 21 Nov iMile
manual payout is **not** proven unallocated; do not alter its allocation or
relax legacy financial guards from a screenshot alone. A newly issued
operational payable may not be duplicated, even if an older pooled payout
needs owner matching.

## Next safe action
1. Exact-head full Operational Balance Backend, Stock Eligibility, security
   and release readiness with tests actually selected and passing; review
   full-source diff and negative/frozen ledger behavior.
2. Verify source count and grouped issues for post-21-Nov iMile/SMSA deliveries
   in authenticated Production read-only UI, distinguish old-created vs new.
3. Decide whether a **separate**, evidence-backed new-order correction is
   necessary, and preserve proof on a different reviewed PR if so.
4. Release only with normal A/B/D immutable intent and original-host v5 guard
   if all finance/release gates pass. Then verify fresh saved operational
   snapshot, source completeness, no duplicate courier obligation or payment.
