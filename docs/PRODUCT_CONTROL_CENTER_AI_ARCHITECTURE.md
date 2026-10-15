# Mezan OS Product Control Center — AI-ready architecture

## Authority boundaries

- Salla remains the commerce publishing channel.
- Mezan OS owns analysis, drafts, approvals, audit, local taxonomy, cost profiles, and AI governance.
- Existing cost collections remain independent and authoritative for Mezan cost calculations:
  - `mezan_product_cost_profiles_v2`
  - `mezan_cost_resources_v2`
  - `mezan_product_option_cost_bindings_v2`
  - `mezan_order_item_cost_snapshots_v2`

## Change lifecycle

1. Read current product from Salla.
2. Build a Mezan draft containing only allowed fields.
3. Show before/after diff.
4. Approve manually or through a bounded policy.
5. Write approved fields to Salla.
6. Re-read the product from Salla.
7. Verify every intended field.
8. Store immutable audit records and the verification result.

## Product domains

- Core commerce: name, price, sale price, status, SKU, barcode.
- Content: description, short description, SEO title/description/keywords/slug.
- Classification: Salla categories, brand, Google category, Mezan local taxonomy.
- Media: images, ordering, primary image, ALT text.
- Options: choices, custom text fields, variants, inventory.
- Costs: preserved in the existing Mezan cost engine and never overwritten by Salla publishing.
- Performance: visits, add-to-cart, checkout, orders, conversion, returns, ad cost, profit.

## AI execution levels

- `suggest_only`: AI creates a draft only.
- `approval_required`: AI draft requires human approval.
- `bounded_auto`: automatic publishing only inside explicit field and risk limits.

Price, sale price, visibility, inventory, and destructive media actions must default to `approval_required`.
