# Operational inventory variants — isolated candidate

User acceptance example: one product, silver10 and gold20 in the same purchase invoice. Each option retains its independent purchased, accepted-return and remaining quantities.

Canonical contract: read only `mezan_products_v2.variants` (`id`, normalized `display_name`/`name`/`selections`, `sku`, `image`). Product ID is retained; optional `variant_id` identifies the stock line. Components and historical base-product invoices remain compatible. No raw-provider or Legacy fallback and no Accounting inventory writer. Missing, duplicate, malformed or incomplete variant metadata is rejected, not converted to generic stock. Text customization without a canonical stock variant is not invented as stock identity.

The same tuple identity is used in purchase validation, stock aggregation, accepted supplier returns, proportional fixed credits, both UIs and request fingerprints. Null optional variant fields are removed before hashing so old pending requests can replay. Original invoices without a variant remain unallocated; no migration guesses their color.

Verification at code checkpoint:

- Before implementation, the new integration suite failed13 cases (catalog omitted variants, duplicate-base rejection and unsafe fallback).
- Focused real isolated Mongo/API suite:23 PASS after implementation, including silver10/gold20, return silver2 -> silver8/gold20, concurrent retry, tax/gross credit, invalid/foreign/duplicate option, oversized return, historical stock and old HTTP request replay.
- Full operational backend suite305 PASS before five additional malformed-metadata cases; final rerun follows.
- Web:102 PASS /8 suites, including actual React save/retry/read/return.
- Native inventory31 and supplier15 PASS; full native verifier chain checked separately.
- Actual updated APK/emulator variant UAT is pending at this checkpoint; do not interpret unit tests as device acceptance.

No dependencies, release files, Accounting, Production writes, merge or deployment. PR1263 is unchanged. Work remains in Draft PR1271 and companion native Draft PR257.
