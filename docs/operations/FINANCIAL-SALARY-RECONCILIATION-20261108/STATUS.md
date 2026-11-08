# Salary cutoff reconciliation — isolated source checkpoint

Base: PR #1827, `af1cd721c148ae5d5036d0ebbf746c35f5ea1056`, retaining its exact-day diagnostic evidence and tests. Production ancestry: `de76aed339be775e2878071c92063d0ab9df06df`.

## Cause and behavior

Daily cumulative rounding restarts at the accrual cutoff. Moving the cutoff from November 2 to November 1 can change already frozen daily amounts by one halala even with identical salary revisions. Ignoring a cent difference or replacing frozen rows would obscure obligations and payments.

This change offers a separate, explicit owner review only when every blocking conflict is reproduced exactly by the old cutoff using unchanged full salary candidates. Preview includes exact recorded/proposed rows and gross increases, decreases and net by currency. Its hash binds the review, source scope, salary obligations and paid state. Existing clients remain blocked on conflicts. The owner must separately confirm opening salary exclusion and review of these adjustments.

After a valid approval, normal reconciliation materializes deterministic `salary_cutover_adjustment` obligations. Original salary days, original obligations, allocations, opening balances and bank movements remain unchanged. No payroll payment or bank debit is created. Repeated requests and worker retries create one decision and one obligation per reviewed day. Changed salary revisions, incomplete contracts, corrupt obligations or changed paid state still block. Profit includes the signed independent adjustments and rejects missing/corrupt adjustment evidence.

## Verification

- Backend salary, context, consistency, cutoff, salary-profit and period suites: **135 passed**.
- Salary-start React suite: **34 passed** (including explicit two-checkbox review, network retry payload and malformed preview cases).
- Real isolated MongoDB 8.0.12 replica set: **12 passed**, including existing salary approval tests and new concurrent approval/worker, stale source/payment/opening, authorization, HTTP replay and profit evidence tests.
- Month conservation covers 28/29/30/31-day months and three salary amounts; no tolerance and no lost/duplicated days.
- Existing pytest/Pydantic deprecation warnings remain; no test disabled or guard weakened.

## Production status and required evidence

**Source prepared only; not merged, deployed, or financially applied.** No `/app` changes or release rehearsal. Supervisor owns integration and release.

Earlier authenticated preview showed 16 missing days (SAR 1,456.66), 40 exact cutoff-rounding conflicts, and one incomplete employee salary source. These are a historical read, not a new live certification. The missing-day subtotal excludes any reviewed adjustment net and must not be presented as the total financial effect.

Before any live approval, obtain the missing employee's valid effective salary history and review evidence that opening balances exclude the newly accrued day. Generate a fresh full preview to review gross/net adjustment impact and paid allocations. This checkpoint does not confirm those facts, change the opening/cutoff, or execute a historical financial backfill. All unresolved source conflicts remain visible.
