# Balances, period statements and receipt reviews

## Scope and authorization

The owner asked to implement the approved compact balances workflow, period
statements and independent preparation/printing permissions (19 November 2026,
Riyadh). Source changes and a recoverable task PR are in scope. This checkpoint
does not authorize a new Production release or live financial transactions.

- Repository: `AMASI-SA/AMASI-SA`
- Task branch: `codex/balances-statements-compact-20261119`
- Verified Production baseline: `80b43ca2f4699fecd895f34a24f77fc997234417`
- Companion: `AMASI-SA/amasi-mobile`, based on operational branch
  `feature/operational-order-compensation-20261101` at
  `a8a7a55e22b4f7430c7619bb792c5d7d540b4f43`.
- Production changed: **no**. No Release Guard lease, intent, merge, deployment,
  APK build, live grant update or live financial write is part of this checkpoint.
- Continuation: this task document and its task PR. Repository issue #1006 is
  already at its comment limit, as documented in the preceding bulk-access task.

## Acceptance contract

| Requirement | Source behavior / evidence needed |
| --- | --- |
| Compact layout | Entity/type in header, small Add, compact summary, five-row table; mobile visual inspection. |
| One-step Back | Detail/proof/history/report first, then original filtered page; web behavior plus native source checks. Physical device acceptance is separate. |
| Daily salary/delivery history | Only the latest proven automatic event in main ledger; manual money remains; current month detail and prior monthly totals; count deliveries rather than fee components. |
| Running balances | Compute from all canonical movements before filtering/paging/compaction; signed green/red display; no invented balances for incomplete or restricted history. |
| Period statement | Explicit visible request, date validation, every period movement, prior opening and period-end closing, currency and preparer; no background refresh or second balance store. |
| Permissions | New resource `report`/`print` default false; intersect movement scope; own-only is limited activity; no actual balances without full balance and movement rights; fresh server check on print. |
| Bulk permissions | Current resource snapshot with per-resource exceptions; preserve existing grants, operations and versioned save; future resources receive no wildcard. |
| Reviews | Separate pending/approved/rejected queues; driver and bank scope, five-row pages, authenticated bound proof and explicit matching; canonical transactional writer with fresh policy checks on every attempt. |
| Financial integrity | Direct bank/POS payment is informational on the courier ledger; fee remains independent; unconfirmed COD stays outside confirmed balance. No extra credit to the courier. |

## Implemented source checkpoint

Backend changes are in the workspace/access modules and new
`operational_workspace_projection.py` and `operational_balance_reviews.py`.
The review service accepts a trusted internal authorization callback so scoped
workspace review can retain its existing transactional evidence, idempotency,
phase and monetary rules. Legacy callers retain their original accountant gate.

Web changes include the compact workspace, resource permissions editor, explicit
statement/print view and scoped receipt-review component. Print is a new
server-authorized read before opening the system print dialog. This does not
claim to prevent operating-system screenshots or printing already visible data.

## Statement design confirmed by the owner

The report uses the balances table: description, for-party, for-us, balance after
movement. A fifth note column appears only if the selected period contains an
original nonempty movement note. Date/time is inside the description cell.
The header names the account type and account/person and explicitly prints
`الفترة من DD/MM/YYYY إلى DD/MM/YYYY`. The footer includes period totals for both
sides, opening and closing (including opening), with signed green/red values and
side labels. Restricted scopes never gain balance information from this design.

## Verification checkpoints

- Fresh integrated backend run: **199 passed in 34.62 seconds** across workspace,
  statements, access, refunds, receipt-review scope, existing noncash review
  evidence and packed-state preservation. Synthetic isolated data only.
- Fresh integrated web run: **1080 passed in 65 suites**, including the complete
  operational UI collection and adjacent navigation, shipping, supplier, employee
  and spreadsheet safety contracts.
- The source-bound real-component preview built successfully (101 modules).
- Seven scoped review HTTP tests include exact-amount, bank-bound candidate
  pagination and reviewer metadata. Candidate selection does not import or
  classify anything; the existing writer revalidates original statement bytes.
- Three new real-Mongo acceptance cases are collected for the scoped callback:
  bank/POS exactly-once approval and rollback when the grant is revoked before
  commit. They require a disposable replica set and are pending remote CI.
- Manual mobile-width inspection verified compact layout and no 320px body
  overflow. A Back discrepancy seen in the iframe is still being investigated;
  actual BrowserRouter tests pass. No physical phone acceptance is claimed.
- Independent implementation review and native companion verification are in
  progress. These results are not a Production-release authorization.

## Data limits retained deliberately

Running balances reflect the currently approved saved ledger and its business
dates, not an archived view of values before a historical source was corrected.
Incomplete/unreconciled records do not get invented historical balances. A period
before the operational start is refused. Undated monetary refunds remain blocked.
Default legacy workspace notes indistinguishable from generated text are hidden;
other original notes remain separate from descriptive labels.

## Next safe steps

Finish independent review and the native companion checks, complete browser
inspection, and run the remotely saved Draft PR gates on its exact source SHA. Preserve release intent and unrelated files. Do not publish this WIP source.
