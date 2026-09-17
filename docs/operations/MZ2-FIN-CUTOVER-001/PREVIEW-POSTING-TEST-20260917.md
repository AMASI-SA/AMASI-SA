# P01 Preview posting acceptance proposal — 2026-09-17

Status: PREPARED, NOT AUTHORIZED OR EXECUTED.
Scope: isolated Preview only; existing reviewed Salla statement 6743152 stays unchanged.

## Evidence
- Runtime test command: PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/app/backend /root/.venv/bin/python -B -m pytest /app/backend/tests/test_mz2_settlements_p01.py -q -p no:cacheprovider
- Result: 7 passed, one multipart deprecation warning, exit 0. Tests use fake in-memory collections and mocked posting; they do not establish successful browser posting.
- Previous read-only ledger preflight: real imported statement requires 58538.94 SAR provider receivable; available 0 SAR.
- P01 explicitly forbids opening balances and cutover activation; scenario6 is an explicitly authorized test record.
- Inspected bnpl/ledger_bridge.py accepts only tabby/tamara/emkan; it cannot be reused to recognize Salla transactions. This is not a complete audit of all production sale-recognition paths.

## Concrete proposed test
Owner approval must explicitly cover creation, posting and reversal of synthetic accounting fixtures in Preview.
1. Recheck Preview host, local database, current tenant and inactive release lease. Snapshot baseline ledger and reviewed statement, keep customer data private.
2. Use unique reference PREVIEW-P01-POST-TEST-20260917, clearly labelled synthetic throughout; no real Salla order reference, no provider calls, no legacy values or opening balance.
3. Prepare balanced synthetic fixture: debit Salla provider receivable 100.00 SAR, credit segregated test clearing 100.00 SAR. Use normal journal service and audit, never insert ledger rows directly. Confirm supported API/entry types and isolation before writing; do not mislabel fixture as real sale.
4. Separate synthetic statement: gross100, refunds0, commission2, commission VAT0.30, net97.70 SAR, other amounts0. Amounts are test parameters, not actual Salla tariff.
5. Browser draft -> match/submit -> review -> post. Expected settlement legs: bank debit97.70, fee debit2.00, fee VAT debit0.30, provider receivable credit100.00. Both sides100.00.
6. Verify one group, bank snapshot, linked fixture document, correct tenant, audit and replay prevention. A mock/unit pass cannot close browser scenario6.
7. Reverse the synthetic settlement then synthetic receivable fixture through supported audited reversal paths. Preserve history; no deletion. Check affected balances return exactly to baseline and original statement6743152 remains reviewed and unchanged.
8. No Production, Salla, Qoyod writes or phase advancement. If normal test isolation or reversal is unavailable, stop before writing and report the concrete gap.

## Remaining production requirement
Automatic post-cutover recognition and independently verified opening balances belong to the approved cutover plan. Do not backfill imported historical sales or grant P01 permission to later phases. Legacy-free Production acceptance remains open.
