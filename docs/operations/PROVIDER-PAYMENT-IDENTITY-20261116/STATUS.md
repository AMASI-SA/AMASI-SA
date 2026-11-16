# Provider cumulative payment display identity

## Scope and source

- Task: `PROVIDER-PAYMENT-IDENTITY-20261116`.
- Repository: `AMASI-SA/AMASI-SA`.
- Task branch: `codex/provider-payment-identity-20261116`.
- Verified production baseline: `4cdc10931f49124a4715153f220e0adf274e8bcc`.
- Implementation checkpoint: `c01386652526b2f65c3c47947cf0db3942a47e3d` (local only).
- Current state: local implementation and verification complete; user explicitly authorized push, merge and production publication after checks on 2026-11-16. Remote publication is now in progress.
- Production changed: **no**. No live financial writes, synchronization, backfill, merge, deployment or release lease.

## Change contract

An old unsplit provider claim can coexist with a newer, cumulative same-provider
split payment observation that failed the posting identity gate. The report used
different keys for these two representations, added both principal/refund
amounts, and counted the order only once. The unaccepted observation could also
add a second complete tariff estimate to the raw fee total.

The projection now folds principal and refund display contributions for a
strictly matched, unclaimed, same-provider SAR alias. It preserves original
record and allocation identities, saved claims, valuation records, accepted
fees, settlements and known net. The unaccepted observation retains its review
state and cannot contribute a second full fee estimate. Its uncovered fee scope
remains incomplete.

The matching guard excludes an independently accepted split claim or split
fact, multiple providers, customer-wallet tenders and foreign-currency/FX
representations. Those require their existing evidence and review paths; this
patch does not migrate or authorize them.

The financial-position GET rebuilds the report from its saved snapshot. No
background task, persistent cache change or source refresh is introduced.

## Final local verification

- Existing pure financial/provider baseline: **87 passed**, exit 0.
- Synthetic public `report()` reproduction before change: old principal 100,
  new cumulative principal 150, one order. It incorrectly returned gross 250
  and raw fees 31.05, while known fees were 12.65 and known net was 87.35.
  The gross assertion failed as intended, exit 1.
- Same reproduction after change: gross 150, one order, known fees 12.65 and
  known net 87.35 preserved; full fees unavailable and incomplete while the new
  representation is pending. Read replay was identical and input state was
  unchanged, exit 0.
- Independent regression file: **24 cases**, including four observed numeric
  payment patterns with synthetic identifiers/tariffs and a positive Tabby case.
- Final integrated unique-file run: **255 distinct tests passed**, exit 0,
  no failures, errors or skips; 4.90 seconds. It includes the new alias tests,
  provider known subtotals, fee display, provider SAR valuation, closed foreign
  tenders, financial position, configured provider fees, statement fees,
  provider components and partial-paid observations.
- Sensitivity check loaded the unchanged production-baseline summary into an
  isolated Python process without modifying the checkout. The new regression
  suite returned **12 intended failures and 12 passes**, exit 1. All four
  numeric payment patterns demonstrated the old duplicated principal.
- Independent source review found no material correctness issue in the bounded
  native-SAR alias rule; its separate execution of the original 20 regression
  cases passed, exit 0. The four later numerical cases also passed the root's
  final integrated run.
- `git diff --check`: exit 0.

Final command (each test file once):

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHON_DOTENV_DISABLED=1 PYTHONPATH=backend:backend/tests \
  .venv/bin/python -B -m pytest --noconftest -p no:cacheprovider -q --tb=short \
  backend/tests/test_operational_balance_provider_payment_alias.py \
  backend/tests/test_operational_balance_provider_known_subtotals.py \
  backend/tests/test_operational_balance_provider_fee_display.py \
  backend/tests/test_operational_balance_provider_sar.py \
  backend/tests/test_operational_balance_closed_foreign_tender.py \
  backend/tests/test_operational_balance_financial_position.py \
  backend/tests/test_operational_balance_configured_provider_fees.py \
  backend/tests/test_operational_balance_statement_fees.py \
  backend/tests/test_operational_balance_provider_components.py \
  backend/tests/test_operational_balance_partial_paid_observation.py
```

## Authorization and next action

Automatic approval review rejected `git push` to the dedicated task branch. Its
reason was unestablished explicit authorization for external source disclosure
and repository mutation, and an unestablished trusted destination. No connector
fallback or other remote write was attempted after rejection.

The user subsequently explicitly approved uploading this fix to
`AMASI-SA/AMASI-SA`, merging it, and publishing it to Mezan after successful
checks. A fresh repository read confirmed push/admin access and the expected
destination. Fresh Git fetch and independent review both still resolve the
production branch to the baseline above; no upstream integration is required.

Next: push the dedicated task branch, prepare the PR and complete the
applicable GitHub CI gates. The local runtime is Python 3.12;
operational CI uses Python 3.11 and a dedicated localhost Mongo replica set.
Those CI/HTTP/live checks have not run for this patch.

Any authorized later production publish must use the repository's protocol-v5
source/intent/rehearsal/verification sequence. No release intent was frozen,
no lease was opened and no production data or deployment was changed.

Issue #1006 is already at its comment limit. This task document and the task PR
provide the recoverable handoff; no financial source payloads or merchant order
identifiers are committed.
