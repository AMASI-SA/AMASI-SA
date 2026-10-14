# Web first — Android paused

User decision: complete and approve Mezan Web UI/API before returning to Android.

Frozen Android artifact: AMASI-Operational-Preview-641537f-x86_64.apk, versionCode 14 / versionName 1.0.11; source HEAD 641537fc45961b31f268eae2bedd4c3356c20bfa, TREE 94ec30ba7a9b7cbe8317d18f1ecde7211ee3100b. SHA256 freshly rechecked: 57300f6040900dd6bf9eb78108facd5f4f410d3d307a57b93787bb791dbbc48b. It is only a UAT candidate. No new build or device operation in this Web phase.

The source-only top SafeArea change begun before the pause is preserved in the native working tree and evidence/android-safe-area-paused.patch. It is not built, device-verified, or included in the frozen APK. Do not resume it until Web/API approval.

Earlier APK-only records saying no Android test ran are historical: subsequent actual UAT found header overlap and fixture limitations. Device acceptance remains incomplete, now paused by user.

## Web change and fresh verification

Daily movements previously displayed a save confirmation without a saved movement list. Added read-only MovementHistory using the existing authenticated movements endpoint, refreshed after successful save, on page reopen, and with an explicit refresh button. Displays human names, direction, amount/currency, source, reference, receipt presence and timestamp; internal identities are not rendered. Failed reads display an error, not an empty success; old unmounted session responses cannot replace current rows. No backend, financial engine, accounting or native source change in this phase.

Frontend regression: 42 PASS / 0 FAIL, four suites, 9.469 seconds. Command: react-scripts test --watch=false --runInBand --testMatch **/OperationalBalances.test.jsx **/DomainDetails.test.jsx **/MezanV2NavigationShell.employees.test.jsx **/MovementHistory.test.jsx --resetMocks=false. Prior backend 140 PASS is historical, not rerun for this UI-only addition.

Actual local browser checks at http://127.0.0.1:5178/operational-preview.html, API http://127.0.0.1:8135/api, synthetic database operational_balance_device_uat_20261005:

- Opening bank balance 10000 saved; form cleared and count became 1.
- Finish activated the operational system; report displayed 10000 actual liquidity.
- Supplier outgoing payment 50 saved with reference WEB-UAT-20261005-001.
- Reload and history refresh displayed exactly one saved movement.
- Empty movement save rejected with missing-fields message.
- Independent API read: active, opening_count 1, movement_count 1, reference count 1, actual liquidity 9950 SAR. Supplier advance 50 is separate from liquidity; no invoice obligation was invented.

## Remaining acceptance

Independent owner/staff Web fixture authentication, full role/domain/receipt browser scenarios, and owner approval of Web UI/API remain pending. Current browser harness uses a synthetic owner dependency, so it does not prove production authentication integration. Report timestamp currently displays unavailable; evaluate the read contract before claiming full reporting acceptance. No full Web/API completion or final review PASS is claimed by this checkpoint.

Production unchanged by this task; production financial writes 0. No merge, Prepare, Prepublish, deploy, Accounting writer or Legacy fallback.
