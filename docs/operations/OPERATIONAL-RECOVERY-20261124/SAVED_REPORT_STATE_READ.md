# Saved report input without historical audit materialization

## Scope

This change affects only `GET /api/operational-balances/reports`. It follows the
report-local fee-profile optimization on task branch
`codex/financial-report-fee-memo-20261124`, based on production D
`0ffa962d3cfda3eb49b5750c7bdc2f932a645d4d`.

The route previously used the full operational-state reader. That reader loads
and validates historical audit pages, even though `report()` subsequently
excludes audit history from the returned financial projection. It also fetched
top-level write histories that are not financial report inputs.

The route now uses the existing `read_financial_position_state` reader. That
reader checks the saved owner/revision, financial and issue packets and audit
manifest, while omitting historical audit page hydration and top-level audit
and idempotency histories. The complete financial report, coverage annotation,
optional position amounts, canonical order labels, initial authorization,
final authorization and visible-request cancellation remain unchanged.

`GET /audit`, mutation readers, other API routes and the financial engine are
unchanged. An absent historical audit page still makes the explicit audit read
fail; it no longer prevents a financial report whose saved packets and audit
manifest are valid. Corrupt financial packets or an invalid audit manifest
still reject the report with HTTP503.

## Fresh verification

The regression was executed before the source change. All four full-output
comparison cases failed specifically because the old route still accessed
`operational_balance_audit_pages_v1`. Response values already matched, proving
that those reads did not contribute financial output.

After the change, **60 tests passed in21.03s**. The independent reviewer reran
the exact command and obtained **60 passed in21.56s**, exit status0, with
unchanged source/test hashes.

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=backend:backend/tests \
  /workspace/scratch/56d90f85c1cc/test-venv/bin/python -B -m pytest \
  backend/tests/test_operational_saved_reports_state.py \
  backend/tests/test_operational_financial_position_state.py \
  backend/tests/test_operational_balance_visible_report_reads.py \
  backend/tests/test_operational_balance_active_read_lifetime.py \
  -q --tb=short -p no:cacheprovider
```

The new tests compare the whole HTTP response against the real previous reader
on packed and unpacked rich snapshots, with both position-amount modes. This
includes issues, facts, labels and all detail fields. They assert zero audit
page reads, unchanged database contents, correct authenticated owner filtering,
explicit audit failure on a missing page, packet/manifest rejection, initial
and final authorization and joined cancellation of snapshot decoding. Existing
ASGI disconnect tests also verify that report CPU stops before HTTP finishes.

Reviewed SHA-256 values:

- Route: `a415e1118027cf46cce4618361cd4cf1aa01070c9dcda08d66db4e8ca5dadfe9`
- New test: `f71f8f4906f7fdc1b3a1eb62a536cc504451a3892496b2e75d776a3c7e07269f`

## Status and next action

No production operation, database write, push, merge or deployment was
performed by this worker. Root owns combined verification, the remote
checkpoint and governed release. This change proves removal of unnecessary
reads, not a production latency percentage. Root must measure the legacy
specialized report and navigation after deployment, alongside the ten
financial-position views. No cache, deadline or resource budget was increased.
