# Operational Balance Android UAT — executed subset PASS, acceptance partial

Native HEAD `05a62fff3ed50be24f6e5cd8f1c91c0c8a06ff42`, tree
`71a25926210a4a320f166cc269bc098c282e25c1`; product backend source
`eb4ddb7fd57c13bc9d14f585929f0b74a7f8bc38`. No product changes for this run.
Emulator AMASI-R5-Isolated, Android15/API35/x86_64. Package
`com.amasi.sa.operationalpreview`, version1.0.11/code14, embedded OTA disabled.
Installed APK hash matches the build:
`e89bda291f02a2a5dbcb251a8c439c62476b4f7193b77ab6a801441e2e12b39c`.

Environment recovered with cold boot without snapshots and software GPU rendering.
Only this AVD config changed, backed up locally. No data wipe. User explicitly
dedicated the emulator before the functional run. Test-driver BACK navigation
and stale node lookups are not classified as product failures.

## Environment and evidence limits

API loopback8135 over ADB reverse; local test database
`operational_balance_device_uat_20261005`. Synthetic users and authentication;
this does not validate Production login/MFA. Owner's general Orders landing page
returns404 because the fixture exposes only auth and Operational Balance routes;
Operational pages work. No general Orders functionality was tested or modified.
Fixture enforces loopback connections, blocks accounting imports and restricts
runtime mutation to operational collections. Observed writes were only states
and operation claims; accounting import attempts=0. This is not device-wide
packet capture. No Production connections were intentionally made; Production
writes=0. Frozen #1263 and both product source trees remain unchanged.

Evidence root: `D:/codex-evidence/operational-device-20261007/`.
Each named UI evidence has PNG and XML; logs are device-trace.jsonl, financial
assertions uat-final-proof.json, readback uat-readback.json. No credentials in
this report. Temporary screenshots containing non-test login inputs were removed;
those credentials were not used by the agent.

## Scenario results

34 checks PASS / 0 observed product FAIL in this executed matrix. Check9 is a
direct local API test; the other33 use the actual native app in the emulator.

| # | Scenario | Result / evidence |
|---|---|---|
|1|Synthetic owner login and Operational page access|PASS; uat-owner, uat-returns (general Orders404 limitation above)|
|2|Staff with both grants login/read/write|PASS; uat-both-after-login, uat-menu|
|3|Write-only login, UI and saved movement|PASS; uat-write-menu, uat-write-save|
|4|Reports-only login, reports visible, write hidden|PASS; uat-readonly-menu|
|5|Neither grant: functions absent|PASS; uat-none|
|6|Reports-only write deep link rejected|PASS; uat-readonly-deeplink|
|7|Write-only reports deep link rejected|PASS; uat-write-deeplink|
|8|Neither grant write deep link rejected|PASS; uat-none-deeplink|
|9|Direct API permission rejection|PASS; four403 assertions in uat-api-denials.json|
|10|Incoming30 from Salla without receipt|PASS; uat-entry, uat-incoming-result|
|11|Outgoing20 supplier without receipt|PASS; uat-retry-result, persisted movement|
|12|Cash-only supplier payment5|PASS; uat-cash-save, uat-doublepress-result|
|13|Zero amount rejected, then corrected|PASS; uat-zero; no zero movement|
|14|Server saved then response503, manual retry|PASS; uat-lost-ack; one20 movement despite twoPOSTs|
|15|Loading locks inputs|PASS; uat-doublepress-loading XML enabled=false|
|16|Repeated save taps during delayed response|PASS; onePOST/one5 movement|
|17|Assigned staff bank selected automatically|PASS; uat-entry shows MZ2 test bank|
|18|Balances/readback match calculations|PASS; uat-report1510, uat-final-proof.json|
|19|Movement history displayed|PASS; uat-history, five unique movement IDs|
|20|Force-stop/relaunch preserves saved state|PASS; uat-reopen|
|21|Foreground reports refresh/readback|PASS; uat-foreground and okhttp GET logs|
|22|RTL, three-column cards, header/menu center hit|PASS; visually inspected uat-cards and successful center tap|
|23|Partial return pending, no bank deduction|PASS; uat-return-pending; liquidity1517|
|24|Confirm actual bank refund15|PASS; uat-refund-result; liquidity1502 before contribution|
|25|Exchange original order7002, quote17.25, contribution10|PASS; uat-exchange-result|
|26|Replacement supplier invoice50 net+7.50 tax|PASS; uat-invoice-result: expected0, confirmed57.50, no bank payment|
|27|Repeated report refresh causes no duplicate|PASS; final unique movement count5 remains unchanged|
|28|Unsaved amount survives background/foreground|PASS; uat-draft-foreground shows19; discarded via reload without saving|
|29|Employee bank payment1|PASS; uat-category-0-before/after, persisted outgoing|
|30|Operating expense1|PASS; uat-category-1-before/after; operating expenses paid1|
|31|Advertising prepaid wallet funding1|PASS; uat-category-2-before/after, persisted outgoing|
|32|Shipping company outgoing1|PASS; uat-category-3-before/after; default direction outgoing|
|33|Courier outgoing1|PASS; uat-category-4-before/after; default direction outgoing|
|34|Owner withdrawal1 separate from expenses|PASS; uat-category-5-before/after; owner withdrawals1, expenses remain1|

Seven additional navigation-only checks PASS: employee, expense, advertising,
shipping company, courier, owner withdrawal and custody entity/form screens.
Evidence uat-card-smoke.json and uat-smoke-0..6-*. Six categories subsequently
received actual save coverage in checks29-34; custody remains navigation-only.

Financial reconciliation: baseline1500 +30 incoming -20 supplier +7 incoming
-15 refund +10 contribution -5 cash supplier =1507 after the core checks.
Six further bank outgoings of1 each result in1501, bank1006 and cash495.
Eleven unique movement rows (refund represented in its dedicated return case).
Core assertions are preserved in uat-core-proof.json; latest in uat-final-proof.json.
Supplier replacement confirmed57.50; shipping17.25 remains expected until the
separate operational completion event. No accounting journal created.

## Remaining before full application acceptance

- Custody supplier/cash transfers remain navigation-only in this run (synthetic
  custody baseline0); both directions and wider financial edge cases per category
  still need coverage. The existing categories above are not an exhaustive matrix.
- Shipping completion and platform-refund variants, accepted supplier physical
  return/discount, and the other documented continuation controls.
- Inventory/custody against stock needs the approved independent operational
  receipt/value contract; existing Accounting writers cannot be used.
- Offline/server restart plus pending outbox across process death, session expiry
  and grant revocation during an in-flight case mutation are not device-tested here.
- Actual production authentication/MFA and physical Samsung acceptance were not
  tested; this run uses approved isolated emulator and synthetic auth only.

Usability observation: swiping within the multiline bank-message field can scroll
that field instead of the page; swiping the page margin reveals Save. The test
driver was adjusted, with no app change. This is not a failed save operation.

No FINAL_REVIEW_PASS or whole-app acceptance is asserted. No merge, deployment,
Prepare/Prepublish, dependency change or Production financial write.
