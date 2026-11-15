# Operational provider statement reconciliation

## Scope and authority

- Task branch: `codex/provider-statement-reconciliation-20261115`.
- Verified production source base: `331b7917b3c01d064ee2a17affb4714f3a296331`.
- User approved implementation for Tabby, Tamara and Salla: upload directly inside
  Mezan 2 operational, detect the format, replace provisional fees with matched
  actual evidence, compare employee payment/refund records, and show discrepancies
  for explicit review and confirmation.
- Latest owner direction supersedes the interim rounded inclusive fallback:
  calculate a source-derived **variable commission percentage before VAT**.
  Exclude native fixed charges and payout fees from that percentage. Show the
  fixed component and explicit or configured VAT separately. Each provider and
  native currency keep their own source profile; unknown components remain
  incomplete. Known valid pricing and actual evidence have priority.
- Production changed: **no**. No release lease, live financial mutation, source
  refresh, bank posting, deployment or publication was performed for this task.
- Issue #1006 has reached GitHub's 2,500-comment limit. This task document and the
  task PR body are the continuation record; do not retry a known-full ledger.

## Change contract

The new operational intake is separate from the legacy settlement importer.
Original source values, native currencies, signed fees and tax, and source-row
locators are preserved. A row with missing or conflicting evidence remains
reviewable; missing amounts never become zero. A statement is not proof of a
bank receipt. Existing employee or provider events must never be posted twice.

The implementation is split across non-overlapping write surfaces:

1. Root: strict intake and synthetic workbook contracts, integration, continuity.
2. Reconciliation service: owner-scoped immutable statements, fresh matching,
   explicit confirmation, atomic state/receipt/audit and route guards.
3. Fee projection: accepted event bridge, actual/estimated distinction, safe
   Tabby fallback and report presentation.
4. Frontend: operational sidebar/page, upload, history, comparison and confirmation
   with request lifetime/session fences.

All processing is triggered by an explicit upload or action. GETs do not write,
refresh sources, or initiate background work. Existing cutover/opening balances,
recorded refund effects, foreign currencies, and source identities remain gates.

## Verified milestone: strict intake

Implemented `backend/operational_provider_statement_intake.py` and its synthetic
XLSX tests. The adapter reuses existing header/date conventions while retaining
strict financial evidence independently of legacy display defaults. It detects
one supported provider/table, retains decimal strings and native currency,
separates payout fees and Salla wallet purchases, and marks ambiguous repeated
rows. Tamara capture principal comes from the event amount, not the order total.

Verification (exit 0):

```text
PYTHONPATH=backend:backend/tests PYTHON_DOTENV_DISABLED=1 \
  <isolated-python> -B -m pytest --noconftest -q \
  backend/tests/test_operational_balance_statement_intake.py --tb=short
21 passed
```

The new test initially failed collection because its implementation module did
not yet exist; this is evidence of an absent feature, not a reproduced old
accounting regression. Fresh execution after implementation passed all 21 cases.

The repository-pinned Node 22.23.2/Yarn 1.22.22 toolchain was verified. Frozen
frontend dependency installation completed with exit 0 and existing dependency
resolution/peer warnings. No lockfile or application dependency was changed.

## In progress / next safe action

- Complete and verify the connected service, fee projection and frontend slices.
- Run fresh combined tests and review the actual integrated diff.
- Exercise native XLSX upload through the authenticated operational API in an
  isolated synthetic database; test replay, overlap, stale approval, permission
  boundaries and existing refund aliases.
- Test actual fee substitution versus existing allocated fees and provisional
  estimates, including currency/partial/refund boundaries.
- Preserve a remote checkpoint and a reviewable PR before handoff.
- New-feature merge/deployment/publication is not part of the current checkpoint.

No customer records, uploaded merchant statements, credentials, environment
values, or real financial examples are included in this branch or its tests.

## Verified milestone: accepted fee projection

The first remotely preserved checkpoint is
`7ffde331d6c3b0f5417d91cd3adcf017b42e8645`, with Draft PR
https://github.com/AMASI-SA/AMASI-SA/pull/1938. Normal Git push did not have
credentials; the installed GitHub connector preserved the exact local tree,
which was verified equal before aligning the local task HEAD. No credential
probing or production change was used.

The next checkpoint adds the fee-only accepted-event bridge, explicit Tabby
average provenance and native-currency guard, retained evidence on refresh, and
actual/estimated provider-table breakdown. Actual fees replace the same covered
estimate. Duplicate, stale, partial-settlement and fixed-fee overlap stay under
review instead of being counted twice. The provider-specific evidence timestamp
does not change the saved source snapshot or profit period.

Root independently reran the combined intake/fee/presentation gate (exit 0):

```text
PYTHONPATH=backend:backend/tests PYTHON_DOTENV_DISABLED=1 \
  <isolated-python> -B -m pytest --noconftest --asyncio-mode=auto -q \
  backend/tests/test_operational_balance_statement_intake.py \
  backend/tests/test_operational_balance_statement_fees.py \
  backend/tests/test_operational_balance_configured_provider_fees.py \
  backend/tests/test_operational_balance_provider_known_subtotals.py \
  backend/tests/test_operational_balance_financial_position.py --tb=short
147 passed
```

The intake now has 30 synthetic cases, including ambiguous duplicate financial
headers, retained missing order references, native installment metadata and the
normalized-evidence storage budget. The dedicated fee suite has 34 cases.

Frontend's first affected gate reports 148 tests passed across four suites. The
frontend owner is completing the final period-bound request contract and a
small desktop/mobile browser fixture. The reconciliation owner is implementing
the narrow explicit missing-refund confirmation and its authenticated API
contracts. Those ongoing slices are not claimed complete by this checkpoint.

Next: finish and independently verify the missing-event writer, including
replay, external duplicate proof, full-day cutoff containment, refreshing an
approved refund, and preserving the old direct-debit compensation behavior.
Then run the integrated backend/frontend checks and update the Draft PR. No
merge, release intent, lease, live refresh or deployment has been performed.

## Latest preserved checkpoint and active owner clarification

The accepted-fee checkpoint is remotely preserved at
`5db8012236a770581515293d8ca9fbca1a38a702` (tree
`ae4d89594dcd00a835d121635dbc1ffc34f6557e`). The first save was stopped; after
the owner said to continue, the GitHub connector preserved the exact verified
tree and the local branch was aligned without discarding ongoing edits.

The owner then clarified that the average percentage must exclude VAT, payout
fees and the fixed per-transaction charge. The source variable and fixed amounts
must be retained independently. An explicitly estimated fixed amount per sale
can be derived from the source as a separate component when the plan is missing;
it must never be presented as an actual fixed charge. Do not infer a variable
percentage from a format that does not separate its fixed fees. This work is
still being integrated, and supersedes the previous inclusive-average design.

Root verified the original privately supplied workbook read-only with the
strict intake. Its data stays outside Git. A plain single-cell note footer was
incorrectly classified as an event; a failing synthetic regression established
the defect, and the narrow correction now skips only nonfinancial note rows.
New source-component tests also exposed the legacy Tamara header map reading the
percentage column as fee money. The new operational reader now requires the
exact money header, without changing the legacy importer.

Fresh intake verification: **41 passed**, exit 0. The connected service owner
has a synthetic authenticated XLSX upload/confirm/replay test and is completing
native refund alias linkage, order-specific holds, metadata-only history and
compact receipts. The frontend's updated focused suite most recently passed
158 cases. Browser fixture build succeeded; local browser launch hit a runtime
socket restriction, and a supported alternate UI channel is being checked.
These are progress results, not final integrated acceptance or deployment.
