# Confirmed refund entitlement before payment — MZ2 P01

## Review finding and scope

The reviewed #1102 source A `c758f14301a2f59b398c5eee65ba2488634de2a9` / B `54d6bc83a68d5140744d58b33807613fdb6ca05f` did **not** accrue an unpaid refund liability. `create_case` inserted `recognized=False` without a journal; `post_bank_payment` debited revenue/tax and credited bank/provider when the daily payment was approved. Despite the earlier module description, entitlement and execution were not separate posted events. This would put a confirmed January return in February if paid then.

Correction candidate #1103 starts from #1102 source A and preserves its frozen B. #1102 already contained the complete accepted #1099 P01 source and settlement fixture repair, plus the owner write-pause controls, durable replay, and latest Qoyod #1100 baseline. It replaced #1099 as a release candidate, not as an additional deployment dependency. #1103 now replaces #1102's refund-timing rule and contains its other source changes. Only the final newly verified #1103 A/B should be considered for separate release authorization; do not deploy the three candidates sequentially or reuse old intent identities.

P01 remains IN_PROGRESS, P02 LOCKED. Existing Preview acceptance/protected settlements remain historical evidence for unaffected behavior. The old daily-payment-only refund timing acceptance is explicitly superseded by this correction. No Preview or Production journals are migrated or reposted. A legacy partially paid case is blocked from conversion in place and requires a separately reviewed residual case; historical tax already posted consumes the original sale's available refund amount/tax.

## Three distinct states

1. **Order/webhook observation:** notification authenticity, order cancellation status or a changed total does not alone confirm a customer entitlement or prove payment. The existing ingress maintains nonfinancial review drafts only. It never posts either journal. Later evidence cannot increase/decrease an already confirmed case or change its frozen tax; additional entitlement requires independent review and a separate case.
2. **Confirmed entitlement on a recognized sale:** an authorized accountant explicitly approves the case amount, effective timestamp with timezone, reason and evidence reference. Required permission: `accounting.refunds.recognize`, independent from draft creation and payment approval. The approver verifies the supporting credit note/return entitlement; storing a reference is not automatic verification by a provider. One evidence reference for the same original sale cannot accrue under two case IDs. The transaction posts the liability once, records the evidence and freezes the original-sale tax allocation. Total confirmed amounts plus historical refunds cannot exceed the original sale; cumulative rounding is retained.
3. **Actual payment:** existing `accounting.refunds.pay` approval requires a confirmed entitlement, sufficient remaining liability, actual execution channel/evidence and a payment timestamp not before entitlement. It posts only liability settlement. It cannot recognize revenue/tax again, even after tax policy changes. Provider payments reduce that provider's receivable; direct bank payments reduce the actual bank and leave the original provider receivable unchanged. Existing double-payment and statement-link guards remain.

The owner write pause applies to both approvals and all related UI writes. Reads and deferred webhook evidence remain available. Transactions serialize recognition/payment with pause; failures roll back the full group and markers.

## Exact isolated month-boundary example

These are synthetic test amounts and dates, **not a selected Production tax rate or cutover**. The original posted sale has gross 115, net 100 and tax 15 in its immutable snapshot. The example approves entitlement at `2020-01-31T18:00:00+03:00` and bank execution at `2020-02-02T10:00:00+03:00`.

| Accounting time | Debit | Credit |
| --- | --- | --- |
| 2 January: original recognized sale | Provider receivable 115 | Sales revenue 100; sales tax payable 15 |
| 31 January, confirmed return entitlement | Sales revenue reduction/returns 100; reversal of original sales tax 15 | Customer refund payable 115 |
| 2 February, actual bank payment | Customer refund payable 115 | Bank 115 |

At January month end the liability is 115 and cash has not moved. February clears it to zero and contains **no additional return or tax entry**. A provider-executed payment substitutes credit to the original provider receivable for credit to bank. Partial payments debit the same liability by the actual paid amount only. Current tax policy is not used to recalculate the return.

The ledger retains its real `created_at`/audit time. New refund entries store normalized UTC `metadata.accounting_at` separately: `2020-01-31T15:00:00.000000+00:00` and `2020-02-02T07:00:00.000000+00:00`. GET `/api/financial-provider-apps/accounting-module/customer-refunds/journal?from_at=...&to_at=...` uses timezone-aware half-open accounting periods, requires `accounting.journals_reports.view`, and returns the new entitlement/payment journals only. It is not a retroactive rewrite of old reports or historical journals.

**Cancellation before revenue recognition:** there is no posted sale to reverse, so this sales-refund path rejects it without revenue/tax/liability entries. If money was collected as an advance, returning that advance belongs to its separately evidenced advance/receipt workflow, not a fabricated sales return. That advance-refund workflow is not newly implemented here. A cancellation/return after completed recognized sale is reviewed against the actual posted sale and follows the confirmed-entitlement path above; a status label alone never posts it.

## Operation and validation

Create/review a refund draft, then use **اعتماد الاستحقاق وإثبات الالتزام** with explicit timestamp, reason and evidence reference. API: POST `/customer-refunds/{case_id}/recognize` with `amount`, `recognized_at`, `reason`, `evidence_ref`. Confirmed facts are immutable; a conflicting retry returns 409. A saved payment may arrive before approval of entitlement, but its approval is blocked until the entitlement exists. Payment time is entered independently with timezone.

Real isolated replica-set tests cover January accrual/February payment via the actual API and period reader, unchanged original tax after a policy change, duplicate/concurrent approval, cumulative original-amount limit, owner/tenant/pause denial, payment-before-entitlement denial, pre-recognition cancellation, nonfinancial webhooks, rollback after the first liability-journal leg, later conflicting evidence, all four execution providers, partial payments and statement-before/after-refund reconciliation. UI tests exercise the independent confirmation inputs/action. Exact final source SHA, counts, CI runs and new A/B clean-clone evidence are recorded in Issue #1006 after the source checkpoint.

## Remaining launch requirements and administration access

No authorized session has established the identity of the Production MongoDB or backup service. The available browser has no admin tabs. Preview and a shared `/app` shell cannot prove Production readiness. No Production topology/index/backup/restore claim is made.

The first identifiable application administration page is **Emergent → the project publishing `mezansalla.com` → Manage Publishing** to establish the production deployment/project mapping. That page by itself is not database or backup evidence. Needed next is the **actual linked MongoDB hosting provider's cluster/topology, database/indexes, and Backups/Restore administration** for that Production deployment, or a named read-only connection with confirmed environment mapping. The provider/cluster and its exact URL are currently unknown; do not assume MongoDB Atlas or invent an Emergent backup screen. The owner needs to identify that service/project or supply an authorized existing session, not secrets in chat.

Once available, read-only evidence must establish transaction/session support, the required existing unique indexes (including built-in `_id` identity constraints for entitlements), backup schedule/retention/last successful recovery point, and a successful restore into a separately authorized isolated destination. A backup filename or successful Preview tests are insufficient. Do not change Production indexes/configuration as part of these checks.

Owner decisions still needed: Production tax rate and effective date/time; exact cutover and timezone/owner scope; who receives the new entitlement-approval permission versus draft/payment permissions; named owner and incident coverage; activation window/sequence including pause/drain, controlled configuration, resume/backlog replay and reconciliation. If pre-revenue collected advances or legacy partially paid refunds exist, inventory and separately approve their handling before activation. No production defaults are inferred from tests. Independent authorization is still required for any merge/deployment/activation.
