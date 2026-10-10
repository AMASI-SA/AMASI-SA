# Custody UAT and pending movement restart RCA

Result: scoped custody financial execution PASS; pending restart display FAIL.
Stopped at this actual product finding under the user's UAT rule. No source fix.

Exact installed native product 294f5ee84ffb07c8abb177d5b0871dd97308c991;
native branch HEAD6c6ac9f1f430f51a0ee28b0a116fc0caefe85975 adds documentation
only. APK1.0.11/14 SHA256
fc81d6f2045ac5c58c556c74ac68852cd43c24b9fd8b1b1219ff9b370adb1828.
Android15/API35 x86_64 emulator-5554. Backend source unchanged ef26adea;
companion HEADfcd4ee106 before this documentation checkpoint.
Local API8135, database operational_balance_device_uat_20261005, synthetic only.

## Reproduction and observed result

1. Synthetic setup funded employee-ob-owner custody60 from test bank through
   the operational API (fixture setup, not a native funding UI test).
2. Actual app: Daily movements -> Custody -> employee -> Pay supplier from custody
   -> test supplier -> amount20 -> Save.
3. Fixture allowed actual server commit200, then returned deliberate503 once.
4. Force-stopped/relaunched app; opened Daily movements again.
5. Pending banner correctly shows outgoing20; disabled form incorrectly shows
   source bank, blank party/account, rather than the saved custody and supplier.

The503 is intentional failure injection, not the bug. The inconsistent restored
form is the bug. Pending command was not cleared or retried after finding it.
No loss of the durable financial payload is asserted.

Read-only API proof: exactly one custody supplier movement20,
id d34f03348dbd1b85bb10ccdbeff6adf8df7e212c32e05333baa070066331d871,
party supplier/ob-supplier, source employee_custody/employee-ob-owner.
Bank915-60=855; after supplier payment bank remains855. Cash remains395.
Custody funded60, spent20, remaining40. No second bank debit.

## Cause and minimal repair direction

Native OperationalMovementsScreen.tsx line65 resets draft to emptyDraft(); line72
restores only pending and page='pending'. Lines183ff render inputs from the empty
draft. The banner uses pending; submitMovement uses pending rather than creating
a new payload (submission.ts). Display and immutable retry command therefore
disagree. Confidence high; moderate UX severity for a financial confirmation.
The defect predates purchase-form simplification; restored-draft code was not
changed in that patch. No evidence of a new backend financial regression.

Repair direction: show an immutable, readable pending-operation summary using
the actual saved payload, including source/party/currency/date/amount. Do not
reconstruct or replace its request identity. Re-test process death + lost ack,
same request retry, revoked grants/owner scope, and exactly one movement. No fix
implemented in this checkpoint.

## Evidence and remaining work

D:/codex-evidence/operational-custody-20261007 contains before.json,
fixture-funding.json, custody-pay-entry.png, custody-lost-response.png,
custody-restored.png/XML and restored-readback.json. Fixture trace in
D:/codex-evidence/operational-inventory-20261007/device-trace.jsonl records
upstream200/returned503 at 2026-10-07T21:00:30Z. Fault control has reset to {}.
The app remains on the pending operation; preserve it for repair/retry testing.

Custody-to-cash40, other category edge cases and whole-app acceptance are not
complete. No regression suites rerun: no product edits. No Merge, Deploy,
Prepare/Prepublish, Accounting or Production writes. Production writes=0.
