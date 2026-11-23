# Inventory side options — 2026-11-23

## State

WIP source implementation, not merged, frozen, or deployed. Production is unchanged by this task.

- Repository: `AMASI-SA/AMASI-SA`
- Base: `c2d244a3349013c06f3fb7824070bfa12bd0bfe0`
- Task branch: `codex/inventory-side-options-20261123`
- Prior customer/inventory release PR2074 is complete. This work does not reopen its lease or replace another release.

## Authorized behavior

At inventory entry and purchase entry, classify each product option (including text) by its stable option ID:

1. `physical`: a specification of the stored piece.
2. `customization`: preparation that can be fixed now or explicitly deferred under the existing rules.
3. `informational`: a customer side addition such as a gift-card answer, gift text, note, or side packaging. Its answer is not collected in the stock configuration and cannot create stock deferral or another stock identity.

Store a local product policy separately from provider options. Preserve order answers, visibility dependencies, accounting/cost evidence, historical invoice descriptions and receiving identities. Keep variant dimensions physical. Do not activate customer-order stock dispensing or change valuation gates. A classification conflicting with an existing supplier service requires a clear correction, not silent removal of a cost/resource binding.

## Implementation contract

- Local `mezan_inventory_option_policy` field on `mezan_products_v2`: full stable-ID roles map, revision, actor and timestamp.
- Owner-scoped GET/PUT `/operational-balances/inventory-products/{product_id}/option-roles` with catalog fingerprint and revision comparison.
- A shared inline classification editor; no writes to Salla definitions.
- Informational parents cannot control stock/preparation children without the relationship being explicitly resolved.
- Reuse the stored classification in later entries. Preserve in-flight draft values and require review when definitions change.
- Preserve historical receiving through immutable invoice evidence rather than reinterpreting saved configurations under a later policy.

## Verification and remaining work

Repository inspection complete. Current tests are not yet run; isolated dependencies are being prepared. Implement and test the API, configuration projection, UI, stale-edit handling, variant protection, conditional fields, and historical receipt compatibility. Then review the complete source diff and create a source-only PR with evidence. Do not touch `release/release-intent-v5.json`, shared `/app`, a release lease, live business records, or publishing controls.

## Coordination

Root owns core configuration, historical receiving compatibility and integration. Delegated work owns backend policy/API, service-binding conflict detection, and frontend editor integration on distinct paths. Exact checkpoint SHAs and verification outcomes are recorded in Issue1006 after pushes.
