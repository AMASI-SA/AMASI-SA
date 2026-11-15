# Operational provider statement reconciliation

## Scope and authority

- Task branch: `codex/provider-statement-reconciliation-20261115`.
- Verified production source base: `331b7917b3c01d064ee2a17affb4714f3a296331`.
- User approved implementation for Tabby, Tamara and Salla: upload directly inside
  Mezan 2 operational, detect the format, replace provisional fees with matched
  actual evidence, compare employee payment/refund records, and show discrepancies
  for explicit review and confirmation.
- Tabby unknown-plan provisional estimate is 9.1% including fee tax. It is not a
  contractual rate, does not apply to other providers, and cannot resolve missing
  payment or refund proof. Known valid pricing and actual evidence have priority.
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
