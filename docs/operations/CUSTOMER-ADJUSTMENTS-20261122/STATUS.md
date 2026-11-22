# CUSTOMER-ADJUSTMENTS-20261122

## Scope and authorization

Implement customer refund/collection confirmation on the operational workspace.
Preserve independently confirmed original payment despite an edited commercial
order total. Unconfirmed differences must not move money or share the ordinary
fee/error classification. Record actual bank/provider execution once, attach
optional evidence, and quote additional collection fees on the actual source.
No automatic rounding threshold or write-off has been approved.

## Publication authorization — 2026-11-22

The user explicitly requested publication ("طيب انشر") after reviewing the
completed implementation and its test results. Integration, merge and governed
publication of this change are now authorized; no test customer refund,
collection, bank transfer, backfill or other live financial write is authorized.

The completed feature source `eb7a9e002bbe723eba62223dbf446f070710a8bc`
passed all 12 observed workflows: Operational Backend **6,345 + 90 subtests**,
Frontend **1,448 / 92 suites**, Stock **5,525 + 97 subtests**, plus browser/mobile,
worker, security and readiness gates. Results and job links are in PR #2060.

Production advanced independently to `9b4fb716b430c32073b5ea7abaeaff62646d9633`
(PR #2062, external-carrier AWB reprint). Its source is
`23e9514b9e8a33bffb2363bb1d3afd792355ab2c` and intent-only commit is
`44c5e4867435d45a651881f0da101f3dcb338061`. The two changes have no filename
overlap. This source checkpoint integrates that production head without
changing the reviewed customer-money implementation or the newer shipping
behavior. A new v5 intent must bind the integrated source; the shipping intent
is retained as the source-base intent until that new intent is frozen.

The v5 full-history guard correctly rejects a merge from the older task base,
even when the final intent bytes match current production. The publication
source therefore uses the integrated tree with the current production head as
its sole parent on `codex/customer-adjustments-release-20261122`. The original
task branch and PR #2060 remain unchanged and preserve all implementation and
review history. No force update, protocol relaxation or old-intent reuse is
used. The new release PR records the exact integrated source SHA and CI.

At preparation time the Cloud Browser repeatedly recovered, timed out, and
reported an Emergent tab renderer crash. One documented reload reached the
Emergent loading page, but the next read reported the same renderer crash.
The actual shared `/app` lease and live runtime identity
have not yet been read. No production-branch merge, shared-workspace change,
release lease, publish click or live financial operation has been performed.
Continue source CI and obtain fresh `/app` guard status before any shared-workspace
change or release rehearsal. Browser trouble must not be treated as proof that
Emergent itself is unavailable, nor bypassed through undocumented controls.

Repository: `AMASI-SA/AMASI-SA`.
Implementation branch: `codex/customer-refunds-receivables-20261122` (PR #2060).
Release branch: `codex/customer-adjustments-release-20261122`.
Original implementation baseline: `c302943e330bcdd534a4a5697b8ef813dfb7cb6e`.
Integrated release baseline: `9b4fb716b430c32073b5ea7abaeaff62646d9633`.
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

Checkpoint 1 SHA: `f129f06286375edd1a92abb73a733f1076d0176e`.
Draft PR: <https://github.com/AMASI-SA/AMASI-SA/pull/2060>.

Checkpoint 1 CI exposed two adjacent source regressions: a foreign-currency
adjustment entered the new fallback, and an already accepted statement refund
lost its dedicated native-alias path. Both were fixed without weakening their
existing tests. The source fallback now remains SAR-only and the established
statement-refund alias guard is preserved. The integrated commit requires its
own CI run; the earlier checkpoint's failed runs are not a passing release gate.

## Integrated implementation — complete, awaiting final exact-commit CI

Integrated checkpoint 2: `9eed2a573a3b32e92e55b3e4696e3a3512b1bdb3`.
Its Frontend CI job passed **92 suites / 1,448 tests**. Backend and the wider
jobs were still running when the final small source-metadata follow-up was
prepared. Final results belong to the exact follow-up commit recorded in PR #2060.

Completed in the task working tree:

- Read-only adjustment inbox, separate refund/collection amounts and counts,
  and original paid proof retained for lookup when a terminal source reports zero.
- Terminal zero-paid source observations also refresh report-only adjustment
  metadata. Original captured facts, claim, pricing date and fee inputs remain
  authoritative; the source still returns no new payment candidate. Only the
  exact matching observation warning becomes informational on the platform card.
- Explicit accountant-recorded bank/original-provider refund execution, with
  same-case confirmation of an existing reservation and fresh proof checks.
- Separate customer collection page, partial receipts, actual destination,
  explicit new versus same-transaction fee quotation, and unknown-fee disclosure.
- Optional scoped receipt uploads, managed page/operation permissions, immutable
  pending commands, fresh authorization, and concurrent-retry protection.
- Manual receipt/refund bridges are replaced only when exact late evidence has
  an actual financial effect. Bank balance, statement, workspace ledger and
  daily activity do not repeat the same matched cash movement.
- Customer paid coverage remains independent of account-ledger matching, so a
  bank statement match does not reopen an already collected customer remainder.
- Existing known net survives an unresolved additional fee. Original gross
  remains separate from refund principal, commission credit and settlement.
- Frontend badges distinguish customer action from ordinary commission review;
  each list and confirmation workflow displays the server's financial basis.

The principal implementation files are `operational_customer_payment_basis`,
`operational_customer_adjustments`, `operational_customer_collection_effects`,
`operational_customer_provider_projection`, `operational_workspace_refunds`,
`operational_workspace_customer_collections` and `operational_workspace_refund_receipts`,
with their source, report, access, recovery and activity consumers. Frontend work
extends the existing refund page and adds `ApprovedCustomerCollections`,
`CustomerAdjustments` and scoped receipt handling. The exact file inventory is
the task branch diff against the reviewed baseline.

`ACCOUNTING.md` records the business decisions, synthetic examples, remaining
commission treatments and official Odoo references. No rounding tolerance,
automatic write-off, automatic invoice correction or actual provider refund is
introduced by this implementation.

### Local verification

- Source recognition and provider projection: **359 passed**, including the
  two regressions found by checkpoint 1 CI (9-file combined run).
- Refund/inbox/collection contracts: **73 passed** in one final run, covering
  17 execution, 6 inbox, 34 existing refund and 16 collection cases.
- Frontend: **149 passed in 10 suites**, in one combined run using the CI flag
  `--resetMocks=false`; no skips or failures. Package and lockfiles unchanged.
- Root collection rerun: **16 passed**. Counts above overlap; do not add them.
- Final terminal-source metadata follow-up: **154 passed** across the customer
  basis, payment currentness, statement reconciliation, configured fees and
  customer provider projection suites. A final unrelated-warning assertion was
  added and the full customer basis suite reran: **27 passed**. The regression
  verifies unchanged facts/claim/fees, idempotent metadata refresh, separate
  pending refund and actual-source debit only after execution.
- Root combined 20-file run: **580 passed**, with **4 setup failures** because
  the local real Mongo service is unavailable (`127.0.0.1:27305`). All four
  are real-Mongo cases in `test_operational_balance_daily_financial_movements`;
  their assertions still require the existing CI replica-set gate. The run
  exited 1 and is not reported as a fully passing local suite.
- `git diff --check` passes.

Frontend command:

```sh
CI=true python scripts/frontend_release_toolchain.py exec -- yarn --cwd frontend test --watchAll=false --runInBand --resetMocks=false --runTestsByPath src/pages/operational/ApprovedEntry.test.jsx src/pages/operational/SimpleOrderRefunds.test.jsx src/pages/operational/ApprovedCustomerCollections.test.jsx src/pages/operational/CustomerAdjustments.test.jsx src/pages/operational/simpleBalancesApi.test.js src/lib/webPageAccess.test.js src/pages/operational/ApprovedFinance.test.jsx src/pages/operational/ApprovedLedger.test.jsx src/pages/operational/CustomerReturns.test.jsx src/pages/operational/simpleBalancesPending.test.jsx
```

Backend refund/inbox/collection command (repository root):

```sh
PYTHONPATH=backend:backend/tests <test-python> -m pytest -q backend/tests/test_operational_workspace_refund_execution.py backend/tests/test_operational_customer_adjustments.py backend/tests/test_operational_workspace_refunds.py backend/tests/test_operational_workspace_customer_collections.py
```

The local isolated Mongo server could not start under the environment's process
restrictions. No escalation or production database substitution was used. The
existing GitHub CI job provisions its own real replica set; its explicit test
list and required-suite assertions now include the new financial and UI tests.

### Remaining validation and limitations

- Preserve this integrated milestone and inspect the CI results on that exact
  commit, including the real-Mongo backend and frontend jobs.
- Without line/version history, simultaneous product return and product
  addition cannot be reconstructed from their net total alone. Review the
  separate source evidence; do not infer customer debt from an executed refund.
- Commission estimates, missing payment terms, refund fee credits, historical
  opening/settlement overlaps and foreign-currency conversion retain their
  distinct accounting review. This patch is not blanket clearance of warnings.

Open PR #2023 is separate, unmerged owner-only Salla alert work. It has not been
merged or overwritten. Issue #1006 has reached GitHub's comment limit; the task
document and Draft PR provide the continuation record without an attempted
comment to that full issue.

## Next safe action

Read this document and root AGENTS.md, fetch the release branch, compare its
exact head with the release PR linked from PR #2060, then inspect that commit's
CI. Resolve any concrete failure on the release branch and retain the Draft PR
until the gates pass. The earlier feature CI is evidence for eb7a9e0 only.
Publication is authorized by the new section above. Finish integrated-source
CI, obtain the actual shared guard's inactive state, freeze the new v5 intent,
complete exact-B rehearsals, then merge and publish through the governed path.
Verify the live identity three times and close only this task's lease. Do not
create test financial operations. The exact checkpoint SHA is recorded in the
release PR and branch ref because this document cannot contain its own SHA.
