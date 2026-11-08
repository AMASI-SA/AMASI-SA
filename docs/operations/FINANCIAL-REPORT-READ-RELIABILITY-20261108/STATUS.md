# Financial report read reliability — 2026-11-08

Status: implemented and locally verified; draft review checkpoint, not deployed.
Base: de76aed339be775e2878071c92063d0ab9df06df (production source observed by coordinator).
Branch: codex/financial-report-read-reliability-20261108.
Tracking: Issue #1006; coordinator owns integration, release guard and V5 release.

## Observed defect and scope
The coordinator observed /operational-balances loading for several minutes and a later context refresh failure removing the displayed report. Context reads overlapped every 30 seconds; report background failures also replaced a successfully loaded snapshot with a generic error. This patch changes only OperationalBalances.jsx and its regression tests. No financial calculations, balances, APIs, permission grants, database changes or provider definitions change.

## Implemented
- One outstanding context read at a time; ignore completions from obsolete generations and unmounted components.
- Transient network, 408, 429 and 5xx failures retain the last successful report with an explicit Arabic stale warning and its original as_of timestamp.
- Context refresh failure disables navigation into write workflows and hides the wallet write form. Report refresh failure also hides its wallet write form.
- Nontransient failures, including 401 and 403, discard the cached report/context. Changed scope remounts the report; revoked reports permission removes it.
- An explicit child-observed scope change clears the old context immediately, ignores its pending response and queues one fresh context read without overlapping requests.
- Initial report failure provides a retry action. Successful refresh clears the stale warning.

## Verification
Red before source change: 3 failing regression tests (overlapping context reads; context 503 discards report; report 502 discards report), 68 passing.
Final: 149 tests passed across OperationalBalances, OperationalBalancesScopeRead, SalaryStart, CustomerWalletBalance and InventoryPurchases; 5 suites passed. git diff --check passed.
Tests cover pending reads, transient recovery, 401/403, same-scope permission revocation, changed scopes, stale pending response after explicit scope change, unmount, original timestamp preservation and disabling cached-scope write controls.
Used Jest with jsdom and Babel env/react presets from the preinstalled frontend dependency bundle; no dependency or configuration files changed.

## Remaining acceptance
Coordinator must run integrated final-head/build checks, review the active release guard and V5 protocol, and verify the production UI after an authorized deployment. This patch does not prove backend query latency or financial-table completeness and does not remove financial warnings. No /app access, live financial mutation, merge or deployment was performed by this task.

## Follow-up stale-scope write audit
InventoryPurchases mode=reports never exposes purchase/payment forms: entry/paying are false (lines 23-24), save explicitly rejects mode=reports (line 37), and persisted commands cannot offer a retry (line 62). Existing tests cover read-only mode even with move permission and persisted-command recovery. OperationalCostExposure only invokes GET costExposure (component lines 37,58; api.js line 61). DomainDetails only downloads GET receiptContent (component lines 25-33; api.js line 59); all other contents are report presentation. No further write gates are needed in these children.
Extended report 401/403 regressions: manual retry remains empty while pending, a subsequent transient 503 cannot resurrect the unauthorized snapshot, and only a new successful response renders data. Follow-up validation: 157 tests passed across 6 suites, adding OperationalCostExposure; original 149 tests still pass. Source implementation is unchanged by this review.
