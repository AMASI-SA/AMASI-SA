# Standalone Operational Balance / Web checkpoint

This is an implementation checkpoint, not release approval or full Android UAT acceptance.

## Exact product identities

- Native PR257: ca62dc2ac5915cd7273836aa08a49d39b920e9e7; tree d2816d2375e933ad810700ce6be8469fd3a1cb68.
- Backend/Web PR1271: b933b3a7e957bd36b9a373eb00f93eb5cdab1bf6; tree f4ff1606d128390e0fd24355401e372d4315f4f5.
- Subsequent checkpoint changes are documentation and the manager-role test only; no APK/product source change.
- PR1263 and Production were not modified by this task.

## Implementation

Separate operational package/router/session storage, with shared operational APIs and existing independent write/read grants. No employee Orders/Fulfillment route tree in this variant. Default employee configuration preserved. OTA disabled in test variant.

Completed supplier gross discounts and accepted quantity returns, custody inventory-invoice settlement, Web category cards, native refunded-return shipping continuation and linking an existing exchange contribution. No Accounting writer, inventory initialization or second financial backend.

## Fresh tests

- Operational Backend: 287 passed, 0 failed.
- Web: 99 passed, 8 suites, 0 failed.
- Native complete typecheck/verifier chain passed; permissions22, inventory25, cases21, supplier13, standalone7.
- Native GitHub CI run37819003571 passed on native product HEAD.
- Backend runtime CI run37819278934 exposed stale manager test: it expected both operational grants to be implied by manager. Approved contract explicitly excludes that implication. Test now checks all four explicit grant combinations while preserving every employee-app grant and existing owner/disabled-owner assertions. Same eight runtime suites:63 passed locally. Product permissions were not weakened or changed.

## Actual Web UI and independent readback

Synthetic DB operational_balance_device_uat_20261005; API127.0.0.1:8135/api; browser5178. Harness uses actual product components and operational router with synthetic mobile actors; this is not a Production shell/auth test.

- Fixed supplier discount3.50: injected503 after persistence; retry confirmed exactly one discount.
- Accepted return one product:22.00 gross; invoice adjusted80.50 ->77.00 ->55.00, paid30.00, remaining25.00. Two adjustments total. Bank/cash unchanged.
- Paid remaining25.00 from custody: custody40 ->15, supplier outstanding0; bank855/cash395 unchanged.
- Moved custody15 to cash: custody0, cash410, bank855; no new expense.
- Order7002 actual refund10.00: bank845. Shipping17.25 initially pending; later completed through reopened case. Refund fields immutable, no second bank debit. Reload/readback preserved one case and completed shipping.
- Missing refund date rejected before save; then valid date saved. Browser automation datetime fill required a keyboard commit; no product change made for automation behavior.
- Local evidence: D:/codex-evidence/operational-standalone-20261008; readback JSONs and verified-readback.txt. No synthetic record is a Production record.

## Android build and remaining acceptance

APK Mezan-Operational-ca62dc2-x86_64.apk; SHA256 ebf5b6691d2cf45312d606422f933889fc616b1585141d3b181657d6a6a27c69. Version1.0.11/code14; package com.amasi.sa.mezanoperationalpreview; loopback8135 via adb reverse. Built from native product HEAD above, existing toolchain/dependencies.

Installed successfully on dedicated Mezan-Operational-UAT emulator5560, API35/x86_64. Existing emulator5554 was being used by another task and was left untouched. New emulator used existing installed system image, software GPU, independent data.

Full standalone Device UAT remains UNVERIFIED. Current supported UI control exposes browser only, no native app surface; no alternate UI-control bypass was used after that boundary was established. Command launch timed out at18048ms, then activity became resumed and rendered129 frames. Initial gfx sample had124 janky frames; cold first boot/software rendering with another emulator is not sufficient to assign root cause or claim improved performance. Physical-device/warm runtime performance and complete standalone login/permission/form/retry/deep-link/foreground UI matrix still require acceptance. Previous integrated-app UAT is not relabeled as standalone UAT.

## Release boundary

No merge, Prepare, Prepublish, release intent/artifact/lease change or deployment. Production writes=0. Security attestation and final governed release checks remain separate prerequisites and were not declared cleared by these local results.
