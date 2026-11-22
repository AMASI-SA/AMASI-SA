# CUSTOMER-ADJUSTMENTS-20261122

## Scope and authorization

Implement customer refund/collection confirmation on the operational workspace.
Preserve independently confirmed original payment despite an edited commercial
order total. Unconfirmed differences must not move money or share the ordinary
fee/error classification. Record actual bank/provider execution once, attach
optional evidence, and quote additional collection fees on the actual source.
No automatic rounding threshold or write-off has been approved.

Repository: `AMASI-SA/AMASI-SA`.
Task branch: `codex/customer-refunds-receivables-20261122`.
Reviewed baseline: `c302943e330bcdd534a4a5697b8ef813dfb7cb6e`.
Production changed: **no**. No release lease, financial write, provider request,
merge, release intent or deployment was performed.

## Checkpoint 1 — WIP, not ready for merge

The first checkpoint contains only the verified original-payment basis vertical:

- New pure evidence helper `operational_customer_payment_basis.py`.
- Narrow operational normalization fallback in transaction evidence.
- Order-page recognition/history/coverage preserve proved paid principal when
  the commercial total changes; a refund hint is not execution evidence.
- Synthetic regressions cover an 0.80 refund observation and a 100.00 collection
  observation, source identity/currency/currentness and idempotent refresh.

Validation (isolated synthetic data):

```
PYTHONPATH=backend:backend/tests <test-python> -m pytest \
  backend/tests/test_operational_customer_payment_basis.py \
  backend/tests/test_operational_balance_order_page_recognition.py \
  backend/tests/test_operational_balance_native_payment_evidence.py \
  backend/tests/test_operational_balance_transaction_capture.py \
  backend/tests/test_operational_balance_partial_paid_observation.py \
  backend/tests/test_operational_balance_closed_foreign_tender.py -q
```

Result: **258 passed** (source vertical author). Independent focused rerun:
**10 passed**. Existing workspace refunds: **34 passed** in the integrated
working tree. New collection API tests: **11 passed** in the integrated working
tree; that implementation is still awaiting the next checkpoint.

## Remaining work

- Finish scoped refund/collection inbox and explicit execution registration.
- Complete provider principal/fee projections and identity-based late matches.
- Preserve customer coverage separately from account-ledger matching.
- Finish optional receipt and managed page permission integration.
- Complete frontend verification and cross-layer financial regressions.
- Review diffs independently, preserve the integrated checkpoint and open/update
  a Draft PR. This checkpoint is not a production release candidate.

Open PR #2023 is separate, unmerged owner-only Salla alert work. It has not been
merged or overwritten. Issue #1006 has reached GitHub's comment limit; the task
document and Draft PR provide the continuation record without an attempted
comment to that full issue.

## Next safe action

Resume this task branch, read this document and root AGENTS.md, verify the
remote checkpoint SHA, then complete the remaining integration and scoped tests.
Do not deploy or change live financial records. The exact checkpoint SHA is
recorded in the Draft PR and branch ref because this document cannot contain
its own commit SHA.
