# Product creation: types and customer options

The existing Mezan creation workflow now accepts `product` (ready-made product)
and `service` (made-to-order service). The type remains immutable after Salla
creation. Other product kinds and other customer field kinds are outside this
increment.

Shippable services require an explicit positive shipping weight in kilograms;
Mezan never invents it. The adapter forwards this weight with `require_shipping`.

Draft `options` is an optional array, defaulting to empty for older clients:

```json
[
  {"name": "هل تريد إضافة كرت إهداء", "description": "اختر", "type": "choice", "required": true, "values": ["نعم", "لا بدون كرت إهداء"]},
  {"name": "كرت الإهداء", "description": "اكتب اللي تبيه على كرت الإهداء", "type": "text", "required": true, "values": [], "condition": {"option_index": 0, "operator": "=", "value": "نعم"}}
]
```

Names and choice values are trimmed and must be unique. Each choice has 2–100
values; there are at most 20 options. The UI includes an editable preset of ten
child sizes, from one to ten years. Each value has its own editable row with
add/remove controls and a visible count. Text fields accept the customer's own text.
Optional descriptions (up to 1000 characters) are shown beside the customer field.

An optional visibility condition references another choice field by its zero-based
draft index, an operator (`=` or `!=`), and an existing choice value. Self references,
missing references and dependency cycles are rejected before provider writes.
Removing a parent in the editor invalidates its dependent conditions until fixed;
other indexes are remapped. The interactive preview hides the gift text initially,
shows it as required for “نعم”, and removes it for “لا بدون كرت إهداء”. Hidden ancestors
also hide their descendants. Editing and reopening preserve descriptions/conditions.

The Salla adapter maps `choice` to `select` and `text` to `text`, and includes the
options and descriptions in the `POST /products` payload. Conditional products
start hidden with zero stock and unlimited quantity disabled. After creation,
Mezan explicitly hides the product and verifies its hidden status and base fields,
then resolves draft references to the actual Salla option/value IDs. It updates
only the existing dependent option via `PUT /products/options/{option}`; it never
resends `values`, which could regenerate variants. It reads the product back and
checks every condition before applying the approved inventory policy and final
status. A product without images stays hidden.

Uncertain condition writes retain the product ID and `publish_unknown` state,
stop before enabling inventory, and require reconciliation. Repeating the existing
publish flow finds the product by SKU, rechecks saved conditions and skips those
already applied. It never creates another product for that resolved SKU.
Existing tenant permissions, SKU duplicate checks, approval, publication
confirmation and reconciliation remain in force.
The existing temporary operation freeze is preserved: new products require
preparation and do not track finished-goods inventory, regardless of older
client choices. This change does not reopen those controls.
Editing a draft resets approval. Existing images and categories survive edits.

Read-back must confirm the product type, option names, input kinds, required
flags, descriptions, ordered choice values and resolved visibility conditions.
A returned product ID alone is insufficient:
mismatches produce `published_unverified`, retain the error and product ID, and
never re-create the product. The inventory policy's mode remains a string on the
draft; provider application details are kept separately as
`inventory_policy_result`. Older drafts that contain the result object still
serialize correctly.

Provider reference: https://docs.salla.dev/product/create-product

Conditional option update: https://docs.salla.dev/5394196e0

Validation covers the creation lifecycle with a provider fake and frontend
interaction, including the gift example, missing references, cyclic conditions,
ignored provider writes, hiding failures and timeouts after a successful write.
These tests do not substitute for live provider acceptance after
deployment. No live product is created by the tests.
