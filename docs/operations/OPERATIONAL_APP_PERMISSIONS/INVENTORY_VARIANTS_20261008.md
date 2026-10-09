# Operational inventory variants — isolated candidate

User acceptance example: one product, silver10 and gold20 in the same purchase invoice. Each option retains its independent purchased, accepted-return and remaining quantities.

Canonical contract: read only `mezan_products_v2.variants` (`id`, normalized `display_name`/`name`/`selections`, `sku`, `image`). Product ID is retained; optional `variant_id` identifies the stock line. Components and historical base-product invoices remain compatible. No raw-provider or Legacy fallback and no Accounting inventory writer. Missing, duplicate, malformed or incomplete variant metadata is rejected, not converted to generic stock. Text customization without a canonical stock variant is not invented as stock identity.

The same tuple identity is used in purchase validation, stock aggregation, accepted supplier returns, proportional fixed credits, both UIs and request fingerprints. Null optional variant fields are removed before hashing so old pending requests can replay. Original invoices without a variant remain unallocated; no migration guesses their color.

Verification at code checkpoint:

- Before implementation, the new integration suite failed13 cases (catalog omitted variants, duplicate-base rejection and unsafe fallback).
- Focused real isolated Mongo/API suite:23 PASS after implementation, including silver10/gold20, return silver2 -> silver8/gold20, concurrent retry, tax/gross credit, invalid/foreign/duplicate option, oversized return, historical stock and old HTTP request replay.
- Full operational backend suite:310 PASS /0 FAIL /0 SKIP.
- Web:102 PASS /8 suites, including actual React save/retry/read/return.
- Native inventory31 and supplier15 PASS; full native verifier chain checked separately.
- Actual updated APK/emulator variant purchase and accepted-return UAT PASS; see completion evidence below.

No dependencies, release files, Accounting, Production writes, merge or deployment. PR1263 is unchanged. Work remains in Draft PR1271 and companion native Draft PR257.

## Isolated completion evidence

Tested product source: backend/Web `e5741e9bb49549524b6bdc1d401650fbead7c6b0` (tree `7b0cf02ab14288b71af3898230d561d6a15e2f37`); native `ce187eb2c71c325cc2b419708ea1d39ae5a5fca8` (tree `0baa38d7142b28174a5a7256bb3ae242d5752393`). Subsequent checkpoint changes are documentation only.

- Web actual purchase WEB-COLORS-1008: silver10/gold20, net500/tax75/gross575. Injected503 after commit, retry retained the command and produced exactly one invoice.
- Actual emulator5560, Android15/API35/x86_64: APP-COLORS-1008 saved silver10/gold20. Accepted supplier return RETURN-SILVER-1008 returned silver2 only. Invoice UI and API showed silver8/gold20 remaining, gross575/outstanding552, one return. Web invoice remained unchanged.
- Force-stop/reopen restored purchase reports; persisted readback retained exactly one invoice per separate Web/app purchase and one accepted return. Aggregate20 silver/40 gold reflects the two deliberately distinct test invoices; remaining18/40 after silver2 return.
- Bank855/cash410 unchanged by invoice and return. Test API localhost8135 via ADB reverse; synthetic DB operational_balance_device_uat_20261005. No Production or Accounting writer.
- APK1.0.11/code14 SHA256 `ccf0f2fbc889d21982c038e03e69ca1749e3e986811f158e5aae97acd9b0967d`; built successfully from exact native source above. Physical phone not tested.
- Evidence directory: D:/codex-evidence/operational-standalone-20261008. See variants-invoice-final.png, variants-reopened.png, variants-return-readback.json, variants-reopened-readback.json and variants-final-assertions.json. APK/build proof in emulator-uat-variants.
- Local final suites: backend310, Web102, native inventory31/supplier15 plus full typecheck/verifier chain PASS. Six remote backend-head workflows completed success; these are not substitutes for the local operational suites.

Limitations: whole Web reports UAT was not claimed because the local harness gives its Web token a mobile principal, which correctly denies audit access. One post-restart UIAutomator dump returned null root; subsequent reports capture and persisted API readback succeeded. Cold launch took8.6s; no performance acceptance claimed. This verifies the variant slice, not final release acceptance for the entire app.

No merge, deploy, Prepare/Prepublish, dependencies or release identity changes. Production writes=0. Next safe action: review this isolated variant slice; continue remaining app acceptance separately.
