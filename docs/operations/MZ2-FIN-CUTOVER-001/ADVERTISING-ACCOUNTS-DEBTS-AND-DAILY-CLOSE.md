# الحسابات الإعلانية والمديونيات والإغلاق اليومي

Operation: `MZ2-FIN-CUTOVER-001` — owner-approved design, **Draft implementation only**.

## Dependency and ownership checkpoint — 2026-09-22

Independent branch: `chatgpt/mz2-advertising-accounts-debts-20260922`.
Base source: `hotfix/prod-snap-meta-final` at `6365a042dfcb125e81e5e198ea1ff1537373ce51`.
This is **not stacked**. The sibling financial-accounts/opening-balances task has no implementation branch, PR or trusted Head. Its blocker is recorded in [Issue #1006, comment 5780414108](https://github.com/AMASI-SA/AMASI-SA/issues/1006#issuecomment-5780414108). It depends on unmerged #1124 (`5292a87a476a140ae8c3c78e88dfba7d8c83f035`). That is NOT a substitute sibling Head and is NOT copied here.

Reservation: [Issue #1006, comment 5780463019](https://github.com/AMASI-SA/AMASI-SA/issues/1006#issuecomment-5780463019).

Open accounting work inspected includes #1116/#1117 opening/daily movements, #1124 workspace, #1126 P03, #1128 reports and #1130 shipping. Changed-file lists of #1124/#1130 were read. Shared ownership exclusions are `accounting_module_contract.py`, `accounting_module_ledger.py`, `accounting_write_control.py`, `ledger_core.py`, opening services/UI, `accountingPages.js`, `AccountingWorkspace.jsx`, `services/accountingModule.js`, the shared accounting workflow and phase STATUS files. #1130 and its worktree are untouched; remote inspection does not establish another worker's uncommitted-file state.

**Allowed now:** pure executable contracts, design, synthetic tests and an isolated presentational UI. **Blocked:** production adapters, registered routes/endpoints/permissions/scheduler, persistence and shared financial integration. No financial-account/wallet/GL model is recreated. References to these entities remain opaque references owned by the sibling task. No permission is granted, including posting.

The existing router is `/integrations-v2?workspace=financial&page=...`; integrate `page=advertising-accounts` only after the dependency gate. The proposed `/accounting/advertising-accounts` may later be an alias if approved under that router. Neither route is registered by this Draft. The isolated component is not reachable in the existing application navigation.

## UI contract

Arabic RTL; monetary values are decimal strings displayed with English digits, never binary-float accounting. Unknown/incomplete money is `null` and displayed as unavailable, not 0. No default/demo account is injected into the application component.

Summary cards: today spend, month spend, advertising wallet balances, total debts, paid and remaining, overdue invoices, unclosed days, incomplete account bindings, unmatched movements. Wallet assets and platform liabilities are separate totals; never net them.

Account columns: provider, account name, external ID, currency, timezone, payment mode, linked wallet/payable, default bank/cash, last synchronization, last complete source day, last accounting close, data status, accounting status.

Tabs: overview; daily spend; wallet/top-ups; invoices/debts; payments; reconciliation; journal previews/attachments; audit. Wallet/top-up UI only displays a sibling-owned reference. Financial writes and posting are unavailable throughout this deliverable. Per-tab and action capability checks default to denied. Missing dependency and missing source data are separately visible.

## Linked-account identity and modes

Canonical identity is `(owner_id, ad_provider, external_account_id)`. `owner_id` comes from the existing persisted actor/organization resolver, not the request. Canonical provider values in this contract are `snapchat`, `meta`, `tiktok`, `google`; future adapters must explicitly normalize their existing source keys.

The browser may select only an opaque `linked_account_ref`. The future server adapter must enumerate the owner's **actual linked integrations**, resolve that reference and derive external ID, provider, name, currency and timezone from the source registry. A browser-supplied registry, owner, external ID or `verified=true` marker is not proof of linkage. Conflicting duplicate identities fail closed; do not silently choose one. No free external-ID creation input.

Exactly one payment mode: `prepaid_wallet`, `postpaid`, `direct_debit`. Exactly one accrual source: `daily_spend` or `invoice`. Wallet/payable/funding-source references are validated against the sibling's own owner-scoped catalog after integration. They are not new models. Both wallet asset and platform debt may exist for an owner; mode selection does not net historical balances.

## Daily close and source completeness

For each source account, compute the prior **local** date and its next-day 01:00 using its IANA timezone. Riyadh uses Asia/Riyadh; Los Angeles uses America/Los_Angeles with DST. Never infer a timezone from provider, or use New York for all Snapchat accounts.

Missing timezone: blocking code `BLOCKED_TIMEZONE_MISSING`, UI exception `MISSING_TIMEZONE`. Invalid/unavailable timezone database also fails closed. An ambiguous repeated 01:00 uses the first occurrence (`fold=0`); the economic-date key is identical during both occurrences, so the future durable scheduler must deduplicate. Compare instants in UTC, not naive local datetimes. The reference contract uses Python zoneinfo; [official fold/DST documentation](https://docs.python.org/3/library/zoneinfo.html#using-zoneinfo).

A scheduler integration is NOT installed here. Future catch-up must enumerate eligible missing dates from its durable watermark, not only yesterday. Source completeness must prove account, local date, timezone, covered interval, revision and synchronization outcome. A timestamp alone is insufficient. Missing amount or failed/incomplete synchronization is not zero. A documented complete source amount of zero is distinct and valid.

State path: `OPEN -> DATA_COMPLETE -> DRAFT_CREATED -> RECONCILED -> REVIEWED -> POSTED`. This Draft can describe POSTED source evidence but cannot transition to it or issue a write. Exceptions: `INCOMPLETE_DATA`, `MISSING_TIMEZONE`, `MISSING_FUNDING_SOURCE`, `MISSING_WALLET`, `INSUFFICIENT_WALLET_BALANCE`, `BANK_DIFFERENCE`, `LATE_SPEND_ADJUSTMENT`, `DUPLICATE_SOURCE`, `NEEDS_REVIEW`.

Unposted late source changes invalidate prior reconciliation/review and produce a replacement draft plan with before/after audit. Posted journals remain immutable; a separate adjustment draft references the original source/day and the new source revision. Its baseline must include already accounted adjustments. Same revision with different payload is a conflict, not an idempotent success. Replays and stale revisions must not silently alter accepted facts.

## FX and bank commissions

Store original amount/currency; whether the source supplied a ready SAR amount; that supplied amount; rate actually used; SAR value; FX source; immutable full day-settings snapshot and revision. The future adapter reads `/ads-manager/cost-settings` and normalizes its actual schema; this task does not guess or replace that schema.

If the source supplied SAR, use that SAR value directly and record conversion multiplier 1 for that supplied value. Preserve the original amount/currency separately. Never multiply the supplied SAR by FX again. If conversion is necessary, require an explicit positive rate and source; no silent fallback rate. Changing live FX or commission settings cannot rewrite a prior day's snapshot. A correction uses the historical snapshot unless a separately reviewed accounting adjustment explicitly handles the policy difference.

Bank commission is not included in the daily wallet expense. Only an actual top-up, settlement or bank/cash debit may carry its actual bank fee. Expense and bank commission remain separate semantic journal roles; reports may sum their values for fully loaded advertising cost without merging ledger accounts.

## Invoices and accrual exclusivity

Invoice draft fields: owner/provider/linked account identity; external invoice number; inclusive invoice period; original amount/currency; frozen FX/SAR; issue and due dates; paid/residual; lifecycle status; PDF or evidence reference; `api`, `upload` or documented `manual` source; spend-matching state; review/audit record. The server must own evidence references and enforce owner scope. File parsing/upload is not implemented in this isolated phase.

Uniqueness: `(owner_id, ad_provider, external_account_id, external_invoice_number)`. States: `draft`, `unpaid`, `partially_paid`, `paid`, `overdue`, `disputed`, `cancelled`. Imports/attachments create drafts only, regardless of a provider's paid flag. Cancellation/dispute does not erase posted evidence; reversal policy belongs to the existing core.

`daily_spend`: invoices reconcile to the already recognized daily expense, never recognize their full amount again. Example 10,000 SAR recognized, 10,050 SAR invoice: 50 SAR discrepancy only; a reviewed genuine discrepancy may produce an adjustment **draft**, not a posting. Incomplete daily coverage blocks matching instead of treating uncovered dates as zero.

`invoice`: daily rows remain non-accruing evidence; the reviewed invoice is the single proposed expense source. Changing accrual source is prospective and requires a dated snapshot; it must not reclassify historic periods or consume their expense keys a second time.

Matching requires an explicit set of covered daily source IDs and allocated amounts. Overlapping invoice periods are not enough to prove duplicate expenses or a match. Source allocation/recognition uniqueness and residuals must be enforced in the existing owner transaction at integration, not by this in-memory contract.

## Payment and journal-preview contracts

Support full/partial/multiple installments and one payment allocated across invoices. Every allocation names the invoice and amount in its currency; cap against its verified remaining amount. Carrying SAR comes from the invoice's frozen rate and remaining carrying amount; final allocation consumes the exact remaining carrying value to avoid rounding residue. Track actual bank/cash principal SAR separately from carrying SAR. Difference is an explicit FX gain/loss; actual bank fee is separate.

The actual funding reference, cash/bank movement reference and proof of payment are mandatory according to the future account evidence policy. This isolated reference contract uses the conservative policy requiring both a matched movement and evidence. Provider API status alone never proves payment. A plan does not execute the movement or settle an invoice.

Semantic previews only, resolved to existing GL account IDs after the dependency gate:

- Postpaid expense: debit advertising expense, credit platform payable.
- Prepaid consumption: debit advertising expense, credit advertising wallet.
- Direct debit: debit advertising expense, credit actual bank/cash only with matched debit evidence; actual fee separate.
- Settlement: debit platform payable at carrying SAR; debit actual bank fee; debit FX loss or credit FX gain; credit actual bank/cash for actual principal plus fee.

No GL writer/import, posting route, balance mutation, disbursement or standalone journal service exists in this task. Preview plans cannot be treated as executable journal payloads without revalidation by the existing core.

## Reconciliation and cutover

Show daily platform spend, wallet usage, invoice, actual bank/cash movement, commission, FX and existing journal evidence side by side. Preserve each original currency and SAR carrying/actual values. A nonzero difference stays visible and blocks review when policy requires. Null cannot be silently coerced to zero.

Precutover wallet assets and payable debts belong exclusively to the sibling opening-balances page. This page has no opening-balance input. No dynamic reading/copying of Mezan 1, legacy balances, prior GL or old advertising debts. Ad source facts must come from approved current integration adapters, not legacy accounting readers.

## Capability and future HTTP boundary

Proposed independent permission keys (not registered or granted here):

| Operation | Permission |
|---|---|
| Account page/read | `accounting.advertising.view` |
| Link/settings management | `accounting.advertising.settings.manage` |
| Daily spend | `accounting.advertising.daily.view` |
| Invoice management | `accounting.advertising.invoices.manage` |
| Debt review | `accounting.advertising.debts.review` |
| Payment recording | `accounting.advertising.payments.record` |
| Reconciliation | `accounting.advertising.reconcile` |
| Draft review | `accounting.advertising.drafts.review` |
| Journal posting | `accounting.advertising.journals.post` — **always disabled in this deliverable** |

After a trusted sibling Head, integrate with the existing persisted-actor owner resolver, permission registry and middleware; never trust UI permission lists for server authorization. Unknown/missing grants and missing owner linkage fail closed. Every owner-specific read and evidence lookup is owner-scoped. No owner/admin override enables posting here.

Proposed API resource under the existing accounting API namespace: advertising accounts, daily rows, invoice drafts, payment previews, reconciliation previews, audit and journal previews. Exact prefix is resolved from the trusted integration router, not invented now. Read permissions and write/review permissions remain separate. Integration returns 403 for unauthorized roles; unavailable dependency returns 423/503 as appropriate without fake data; prohibited posting is unavailable regardless of role. No endpoints are mounted by this Draft, so HTTP authorization acceptance is pending, not passed.

## Durable idempotency and concurrency obligations — blocked integration

Pure deterministic keys do not prove transactional safety. Future persistence must enforce account/invoice uniqueness above, one daily economic key, one payload per source revision, idempotency request key + payload digest, optimistic revision guards and allocation caps inside the existing owner-scoped transaction. Same-key/same-payload replay returns the original identifier; same-key/different-payload fails. Two concurrent final payments cannot both spend the same residual. A crash cannot leave a draft, evidence consumption or audit half-committed. Reuse P01 transaction, period-close and write-control gates; no second writer.

Real-Mongo tests must later exercise the actual shared adapter/transaction implementation: unique collisions, concurrent duplicate intake, concurrent different payments, review races, transaction rollback and cross-owner isolation. An in-memory test or key-computation test must not be reported as passing these persistence gates.

## Acceptance matrix and status

At this initial documentation checkpoint, executable implementation/tests are **not yet completed**. Subsequent commits and the task evidence file report exact commands/results. The 22 requested acceptance groups remain distinguished:

1–3 actual registry-only selection, identity dedup and payment-mode separation: pure contract/UI checks now; actual adapter acceptance blocked.
4–6 local 01:00 Riyadh/Los Angeles/DST and missing timezone: pure tests now; durable scheduler unregistered.
7–10 incomplete sync, immutable FX, no double FX and no duplicate commission: pure tests now; source adapter acceptance blocked.
11–17 invoice dedup, single accrual, installments/caps/actual source, discrepancy draft and immutable posted adjustment: pure preview contracts now; persistent application acceptance blocked.
18 endpoint authorization: permission contract tests now; actual HTTP tests blocked (no endpoints).
19 deterministic replay keys: pure tests now; database idempotency/concurrency blocked.
20 no legacy reads: static/import-boundary tests now; integration regression still required.
21 backend and isolated frontend tests now; actual Real Mongo and full application integration pending.
22 Release Readiness, Security Gate and CodeQL: inspect actual CI on the final Head; no inherited PASS claims and no gate bypass.

## Safe continuation

Obtain and independently verify the sibling's actual implementation PR/branch/full Head, APIs and file reservations. Do not use #1124 as an invented sibling implementation. After resolving its blocker, review and stack/retarget deliberately, wire opaque catalog/FX/source ports into existing services, register the page/permissions without posting grants, and run backend/frontend/real-Mongo/HTTP/browser gates. Do not silently move this Draft to a new base or enable runtime behavior.

**P01 remains IN_PROGRESS. P02/P03 are not opened, activated or completed. No Merge, Deploy, Preview/Production mutation, real financial write, journal posting, real opening balance or real debt entry is authorized/performed.**
