# Independent source acceptance

## Target

- Source: `e412bc630de7d7e8eaf7dd03f6c1d09e59c18367`
- Tree: `9e064dbfe47a42fac2bf745665babf0decd52602`
- Baseline: `c2d244a3349013c06f3fb7824070bfa12bd0bfe0`
- PR: https://github.com/AMASI-SA/AMASI-SA/pull/2084
- Review date: 2026-11-23

Two independent reviewers inspected the final immutable source after the earlier implementation reviews. Both reported no P1/P2 blocker in their assigned scope. They made no source, test, configuration, remote, release, or production changes. This acceptance is source-scoped; the exact-source CI result is recorded separately in VERIFICATION.json and STATUS.md.

## Frontend and API contract review

Accepted behaviors:

- Text and choice fields share the same three functional roles; known variant dimensions remain physical.
- Required informational questions/text appear in a neutral summary and do not become stock inputs or stock deferral. Stale informational values cannot be submitted as fixed stock specifications.
- The role editor saves a full stable-ID map with the reviewed catalog fingerprint and expected revision, validates the returned product/revision, and requires reload after a conflict.
- A side-addition parent cannot control a physical/customization child without correcting the classification.
- Changing a text field from customization to physical removes impossible deferred state during explicit review; a required physical field becomes editable and required.
- Navigation guards and editor cleanup release classification state correctly.
- Only a definitively rejected pre-write policy/preview command is cleared for review. Unknown conflicts or network outcomes retain their durable command.
- Quantity, unit cost, tax and location survive classification review. Other saved draft rows require reopening and saving before approval.
- Owner permissions are enforced both in the UI and at the API boundary.

Fresh independent execution used the governed frontend toolchain and the existing InventoryOptionRoles, StandaloneInventoryEntry and InventoryPurchaseDraft suites: **34 tests / 3 suites passed, exit 0, zero failures or pending tests**. The reviewer also inspected the root's actual combined artifact: **158 tests / 8 suites passed**.

Artifact: `evidence/e412-independent-ui.json`, SHA-256 `ef7eff2158dee6c80f3a5e36f6ae15c93ca785d51604b497dbf8dd62073242f2`.

## Backend and consumer review

Accepted behaviors:

- Local policy writes only the local product-policy field; provider definitions and customer answers remain intact.
- New configured and legacy-shaped inventory/purchase lines enforce the policy revision. Completed command replay precedes current-policy validation.
- Omitted revision fields preserve old command identities.
- Linked receiving uses the original invoice line's frozen option metadata and preserves current identity uniqueness, product type, variant, location, capacity and valuation checks.
- Native purchase hashes retain exact legacy bytes without a local policy and include effective semantics when a policy exists.
- Both new supplier-plan consumers exclude only informational option service bindings, including later bindings and component-to-service changes. Numeric product identifiers are handled without aliasing leading-zero IDs.
- Materials, direct costs, product-level services, customer answers, and existing materialized piece history are preserved.
- Owner authorization and complete-definition CAS distinguish absent and null fields. Concurrent identical saves converge without an extra revision increment.

Fresh independent execution: test_inventory_side_options, test_inventory_option_binding_conflicts, test_supplier_option_service_policy and test_purchase_configuration_contract: **73 passed in 4.07 seconds, exit 0**. Supplier projection fixtures use mongomock_motor; this run is not real Mongo transaction evidence.

## Browser limitation

A bounded attempt to render the new role editor on desktop/mobile used installed Playwright and Chromium with the sandbox enabled. Chromium stopped before rendering because the environment rejected socket creation (`process_singleton_posix.cc:297`, `Operation not permitted`). No sandbox bypass, installation, live access or source change was attempted. No new-editor screenshots or visual pass are claimed.

The separate GitHub inventory purchase/receiving browser suite exercises existing actual components on desktop/mobile. Its seven passing scenarios are useful regression evidence, but are not a dedicated visual review of the new role editor.

Sanitized local boundary receipt: `evidence/e412-browser-environment-boundary.json`.
