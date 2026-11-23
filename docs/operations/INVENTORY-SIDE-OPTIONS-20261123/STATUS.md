# Inventory side options — 2026-11-23

## State

Source implementation and exact-source automated integration accepted. PR2084 is ready for review, unmerged and unpublished. Production is unchanged by this task.

- Repository: `AMASI-SA/AMASI-SA`
- Base: `c2d244a3349013c06f3fb7824070bfa12bd0bfe0`
- Task branch: `codex/inventory-side-options-20261123`
- Source checkpoint: `e412bc630de7d7e8eaf7dd03f6c1d09e59c18367`
- Source tree: `9e064dbfe47a42fac2bf745665babf0decd52602`
- Source PR: https://github.com/AMASI-SA/AMASI-SA/pull/2084
- Final evidence branch: `codex/inventory-side-options-evidence-20261123`. This branch adds only the final handoff and test/review evidence after the tested source. It is not a release source or deployment candidate.
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

## Verification

All 21 exact-source workflow runs succeeded. At the completed CI snapshot, 37 checks passed and 6 jobs were skipped by their explicit scope/release-intent conditions; no check failed or remained pending. The temporary CodeQL aggregate warning cleared when Python analysis completed.

The isolated MongoDB 8.0.12 Operational Balance Backend job passed **6,810 tests with 90 subtests**, followed by **106 demand-read tests** and **16 shipping tests with 18 subtests**. Downloaded artifact SHA-256 is `09c3615087f9f076bba43a19e3764645b057c61eacc9d0113b2cb9c9e601ae3f`. Its exact source and tree files match the checkpoint above. XML inspection found zero failed, errored or skipped cases; it includes all 34 role-policy API/legacy cases, 24 side-option cases, 7 service-conflict cases, 23 supplier-projection cases, 19 native purchase-contract cases, 29 linked-receipt cases and 29 payment-allocation cases.

The operational frontend passed **1,699 tests across 106 suites**, including all 158 cases in the eight directly affected suites. The sandboxed CI inventory purchase/later-receiving browser flow passed all **7 desktop/mobile scenarios**. This browser flow is regression evidence for purchase and receiving, not a dedicated visual review of the new role editor.

Adjacent real-storage suites also passed: Operational Stock Eligibility **5,531 tests with 97 subtests**; G47 lifecycle **331 tests with 127 subtests**, native inventory **65**, order cost **436**, and Salla projection **238**. Counts overlap across jobs and must not be added as a unique total. CodeQL, security, accounting, production preparation, frontend build and Node20/Node24 engine/dispatcher contracts passed.

Local exact-source evidence: **201 backend tests**, **158 frontend tests / 8 suites**, independent UI **34 / 3 suites**, and independent backend **73** all passed. The earlier 34 API mock cases are retained and clearly marked as mock feedback; the new real Mongo CI result supersedes that limitation for API acceptance. Earlier WIP XML/JSON files remain historical and are not substituted for the final e412 artifacts.

Two independent final reviewers accepted the source with no P1/P2 blockers. Earlier findings around stale text deferral, navigation state, rejected-command recovery, cached purchase metadata, legacy bypass and future supplier service bindings were repaired before this checkpoint. Review detail: `INDEPENDENT-REVIEW.md`. Exact CI links, artifact digests, scope and boundaries: `VERIFICATION.json` and `evidence/e412-*`.

### Reproducible commands and evidence

The exact CI selections, dependency versions, real Mongo fixture, source-recording step and zero-skip/presence gates are in `.github/workflows/operational-balance-web.yml` at the tested checkpoint. The execution is https://github.com/AMASI-SA/AMASI-SA/actions/runs/45196877340. G47, stock, security and neighboring workflow URLs are recorded in `VERIFICATION.json`.

Local backend: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=backend:backend/tests <isolated-venv>/bin/python -B -m pytest --noconftest -p no:cacheprovider -q` with test_inventory_option_configuration, test_inventory_side_options, test_purchase_configuration_contract, test_inventory_option_binding_conflicts, test_supplier_option_service_policy, test_preparation_piece_line_services, test_preparation_piece_operations, test_supplier_dispatch_customer_options_guard, test_product_inventory_rules and test_product_fulfillment_rules; exit 0, 201 cases, retained JUnit XML.

Local frontend: `python scripts/frontend_release_toolchain.py exec -- yarn --cwd frontend test --watchAll=false --runInBand --runTestsByPath` with InventoryOptionRoles, InventoryProductFields, inventoryOptionPolicy, inventoryProductConfiguration, StandaloneInventoryEntry, InventoryPurchaseDraft, inventoryPurchaseWorkflow and InventoryPurchases; exit 0, 158 cases, retained JSON.

## Limitations and exact next safe action

A bounded local Playwright/Chromium visual attempt stopped before rendering because the environment rejected socket creation with the Chromium sandbox enabled. No bypass was attempted and no new-editor screenshot pass is claimed. The source review and functional UI tests passed; this visual boundary is retained in the evidence.

The source is ready for review/integration. Before a separately scoped release, re-check the current production base and Issue1006, integrate other approved changes if any, and create the governed source A / intent-only B with fresh identical clean builds, candidate proof and clean-clone adapter rehearsal under AGENTS.md. The inherited intent cannot deploy this source. Node20/Node24 engine/dispatcher CI contracts do not replace those release rehearsals.

This task has not changed `release/release-intent-v5.json` anywhere in the full source interval, shared `/app`, release leases, live business records or publishing controls. No source/release A/B identity has been frozen; no merge or publication occurred; no publication reservation exists. Do not deploy the evidence branch. PR2074 remains complete; preserve every foreign task and lease.

## Coordination

Root owns core configuration, historical receiving compatibility and integration. Delegated work owns backend policy/API, service-binding conflict detection, and frontend editor integration on distinct paths. Exact checkpoint SHAs and verification outcomes are recorded in Issue1006 after pushes.
