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

## Implemented source

- Owner-scoped local policy API with a full ID map, variant protection, definition fingerprint, CAS and same-command replay.
- Shared inline editor in stock entry and purchase entry; text fields have the same role choices. Side additions have a neutral summary instead of stock inputs.
- Side values cannot enter new stock configurations. Physical and customization values retain required/visibility rules. Explicit review removes an obsolete deferral when a field becomes physical.
- Product policy revision travels with new commands. Stale commands are rejected before writing; completed command replay remains exact. Unapplied legacy-shaped purchase commands cannot bypass a policy.
- Open draft values keep quantity/cost/tax/location; other saved draft rows require explicit review. Definitively rejected standalone policy commands can be reviewed safely; unknown failures retain their command.
- Classified cached Salla options are reused consistently for purchases while explicit canonical definitions remain authoritative.
- Linked receiving uses each invoice line's frozen configuration definition, including pre-release metadata, rather than a later product policy. Historical keys, records and financial values remain unchanged.
- Native purchase definition hashes include policy semantics only when a policy exists; no-policy fingerprints remain byte-compatible.
- Existing supplier service conflicts are identified at policy save. A narrow read-only supplier-plan projection also suppresses informational service bindings added later or converted from components, while keeping order answers, original costs, product-level services and already materialized pieces.

## Verification and remaining work

This is a recoverable **WIP source checkpoint**, not release approval. Core regressions were observed failing first (13 backend and 4 frontend cases) and subsequently passed. Latest local core run: **82 passed**. Frontend owned suites after independent-review repairs: **133 passed across 6 suites**; the two additional core UI suites previously passed 24 cases and are being rerun for the combined source. New supplier projection tests: **20 passed** before the numeric-product-ID follow-up. Expanded API mock feedback: **34 passed** (not real Mongo). The retained XML/JSON files identify their actual run, and are not a frozen source/build proof.

Independent reviews identified and resolved stale text deferral, dirty-navigation state, rejected standalone-command recovery, cached purchase metadata, and legacy-shaped purchase bypass. Supplier projection closes the later-service-binding gap without altering financial writers.

Local Mongo 8 could not start because its allocator could not read CPU information; Mongo 7 also stopped at initialization with an environment `Operation not permitted` error. Do not label the API mock tests as real Mongo evidence. The committed CI selector runs the policy, receiving, purchasing, native configuration and supplier regressions with the existing isolated real Mongo 8 job; those exact-source results remain pending.

Next: complete focused verification, checkpoint the coherent source, open a source-only PR, inspect exact-source CI including real receiving/atomic behavior, and record the final review/CI outcome here and in Issue1006. This task has not changed `release/release-intent-v5.json`, shared `/app`, release leases, live business records or publishing controls.

## Coordination

Root owns core configuration, historical receiving compatibility and integration. Delegated work owns backend policy/API, service-binding conflict detection, and frontend editor integration on distinct paths. Exact checkpoint SHAs and verification outcomes are recorded in Issue1006 after pushes.
