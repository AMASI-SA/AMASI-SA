# OP-PROVIDER-REFUND-20261101

Status: WIP / review only. NOT release-ready; no production or financial-data writes.

## Source and scope

- Repository: AMASI-SA/AMASI-SA.
- Task branch: fix/operational-provider-refund-components-20261101.
- Pinned production baseline: 064ba0d0af06a29d947b63cf0c72a8da157264ed.
- Application/test checkpoint before this document: b5ff3b0ccac8c17d52d2fd2259ca391be8b69e78.
- Keep the separate product/SAR/status work in PR1678 untouched.
- Never reuse or close another release lease, modify /app, run production refresh/backfill, change provider policy records, or publish this checkpoint.

## Implemented slice

1. `backend/operational_provider_fees.py`: opt-in fee_model_version=2, explicit approved policy/evidence, independent percentage/fixed/refundable/nonrefundable components and VAT. Payment plan and effective-date selection. Executed refund reference replay, full/partial refunds and negative net exposure. Proven pre-capture cancellation events are distinct from refunds; generic Salla order status is not proof. The legacy policy calculator is preserved as version 1; no rates are hardcoded and no merchant policy is migrated automatically.
2. `backend/operational_balance_engine.py`: delegates provider_projection to the new module. Net diff otherwise preserves COD, supplier, shipping, salary, inventory and existing confirmed-obligation behavior.
3. `backend/operational_customer_returns.py`: preserve available capture and plan evidence in the stored refund snapshot; validate cumulative executed refunds; accept evidenced component policies; normalize a recorded post-capture refund separately from Salla's cancelled status so principal is not subtracted twice. Existing atomic state writer remains the only write surface; it has NOT been called on live data.
4. `backend/operational_provider_statement_review.py`: pure, read-only statement arithmetic, component/tax/payout-fee checks, account-scoped duplicate detection and optional comparison to an explicitly supplied bank amount. This function always returns posting_allowed=false. It is NOT a bank reconciliation writer or an importer.
5. Synthetic unit tests and two real-isolated-Mongo integration cases added to the existing operational CI glob. No customer data or real merchant terms are committed.

## Verification actually completed

Local Python 3.13; isolated source snapshot from the branch review artifact. The relevant engine and return-module base blobs were verified identical to production baseline above. Offline BSON dependency only; no network or database used by these unit checks.

```bash
PYTHONPATH=/mnt/data/provider-test-deps:backend:backend/tests \
python -B -m pytest --noconftest -q \
 backend/tests/test_operational_balance_provider_components.py \
 backend/tests/test_operational_balance_provider_statement_review.py \
 backend/tests/test_operational_balance_engine.py \
 backend/tests/test_operational_balance_sources.py \
 backend/tests/test_operational_open_cost_exposure.py --tb=short
```

Result: 136 passed, exit 0. Before implementation, regression checks failed at the missing component-policy behavior. Full CI and the new Mongo cases were not executed locally; there is no local Mongo service. Their results must be checked on the exact final remote SHA.

A private owner-supplied statement was also checked with the pure reviewer: all transaction rows plus the payout-fee row balanced to its reported payout. This is arithmetic evidence only. It does NOT establish bank arrival, opening-balance basis, live provider configuration, correct customer/account ownership or posted operational balances. Private source rows and financial amounts are intentionally not committed.

## Mandatory remaining gates / no readiness claim

- Complete full backend integration, Web, Security and CodeQL checks on the final remote SHA, including the two new Mongo scenarios; inspect the complete diff/readback hashes.
- Verify actual merchant fee agreements, plan identity, effective dates and policy approvals through an authorized configuration path. Version 2 is deliberately not auto-enabled.
- Complete and prove the real source mapping of provider capture/void/refund/plan evidence. Generic Salla cancelled/refunded status alone must not trigger a monetary event.
- End-to-end settlement fee credits, post-settlement refunds, bank movement linking and partial allocations are NOT implemented by the read-only statement reviewer. Prove no duplicate fee debit/credit and preserve existing confirmed facts before any activation.
- Opening balances may be gross or net. Do not assume, rewrite or replay old orders. Determine their basis from actual evidence; any financial correction needs a separately approved, auditable action.
- No live before/after balances, source-warning closure, provider settlement posting, tax invoice matching or bank matching has been demonstrated.
- Product cost SAR/status restrictions, carrier reassignment, missing source/salary/advertising evidence and related UI completeness remain separate scope; this checkpoint does not resolve them.

## Next safe action

Open Draft PR, verify current branch tree and run existing CI. Address observed failures without weakening source/evidence guards. Continue with read-only reconciliation design and approved policy/source mapping; do not merge, prepare a release, publish, or execute live financial adjustments from this checkpoint.
