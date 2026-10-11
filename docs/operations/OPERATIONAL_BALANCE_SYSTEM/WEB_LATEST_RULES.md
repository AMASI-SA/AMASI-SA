# Web/API latest rules — ready for owner review

Supersedes the remaining permission/receipt items in WEB_FIRST.md. Android remains paused and the frozen APK unchanged.

## Implemented

Operational access follows active account membership, with no special grants. Tenant boundaries, disabled-account checks, revalidation during writes and actor audit remain. The navigation entry is available to signed-in users; unrelated Accounting grants are untouched. Members share the owner's operational history.

Manual daily Web movements no longer show or upload a receipt. A platform's actual settlement fee is still restricted to incoming provider settlement and still passes through the existing allocation rules; an upload is no longer a prerequisite. Historical receipt APIs remain for compatibility and distinct evidence-specific contracts.

New bank-transfer orders in reviewed/in-progress credit their full amount once using the canonical Salla order identity, selected-bank string and the existing confirmed mz2_bank_transfer_bindings → mz2_financial_accounts contract. The operational adapter reads both directly without importing bank accounting receipt/writer modules. Missing routing produces an issue. Amount/destination changes preserve the first credit and surface a conflict. Prior manual incoming order movements are checked before automatic creation. Incoming manual duplication for bank-transfer orders is rejected. CAS plus durable global order-operation claims cover concurrent refresh and owner changes.

## Fresh evidence

- Backend: 149 PASS, 0 FAIL, 23.16s; all test_operational_balance*.py with --noconftest, isolated localhost Mongo databases.
- Frontend: 43 PASS, 0 FAIL, four suites, 6.894s.
- New bank-order cases: both Arabic/English statuses; no receipt; concurrent refresh; transition/retry; missing binding; ineligible status; prestart order; changed amount; later delivery/cancellation; owner reassignment; duplicate manual incoming rejection.
- Existing MZ2-only/Legacy-denial, COD, supplier, shipping, salary, custody, advertising, recurring and financial parity regression included in the backend suite.
- Stale role-denial assertions updated because the owner explicitly removed operational grants. The hard source allowlist gained only the verified MZ2 binding collection. Tenant/disabled-account rejection tests retained.

Actual browser on http://127.0.0.1:5178/operational-preview.html and API http://127.0.0.1:8135/api, database operational_balance_device_uat_20261005:

1. Existing baseline10000 and supplier outgoing50 retained.
2. Synthetic order WEB-BANK-300, bank transfer, reviewed: automatic incoming300 appeared in movement history; report liquidity10250.
3. Status changed to in_progress: credit count remained1 after worker refresh.
4. UI has no upload field. Manual outgoing25, reference WEB-NO-RECEIPT-001, saved successfully and was reread in history without a receipt.
5. Final API read: three movements, one order credit, one new manual reference, liquidity10225 SAR, no issues, non-null reconciliation timestamp. The report visibly shows its latest update after reconciliation; no fabricated timestamp was added.

The preview uses synthetic authentication; production sign-in was not exercised. This is Web review evidence, not Device UAT or a final system-review PASS. No Android build/device operation, merge, Prepare, Prepublish, deploy, Accounting writer or Production write. Production unchanged by this task; production financial writes=0.
