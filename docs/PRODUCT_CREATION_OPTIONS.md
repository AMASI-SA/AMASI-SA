# Product creation: types and customer options

The existing Mezan creation workflow now accepts `product` (ready-made product)
and `service` (made-to-order service). The type remains immutable after Salla
creation. Other product kinds and other customer field kinds are outside this
increment.

Draft `options` is an optional array, defaulting to empty for older clients:

```json
[
  {"name": "المقاس", "type": "choice", "required": true, "values": ["سنة", "سنتان"]},
  {"name": "الاسم المطلوب", "type": "text", "required": false, "values": []}
]
```

Names and choice values are trimmed and must be unique. Each choice has 2–100
values; there are at most 20 options. The UI includes an editable preset of ten
child sizes, from one to ten years. Text fields accept the customer's own text.

The Salla adapter maps `choice` to `select` and `text` to `text`, and includes the
options in the documented `POST /products` payload. There are no independent
option writes or new retry paths. Existing tenant permissions, SKU duplicate
checks, approval, publication confirmation and reconciliation remain in force.
Editing a draft resets approval. Existing images and categories survive edits.

Read-back must confirm the product type, option names, input kinds, required
flags and ordered choice values. A returned product ID alone is insufficient:
mismatches produce `published_unverified`, retain the error and product ID, and
never re-create the product. The inventory policy's mode remains a string on the
draft; provider application details are kept separately as
`inventory_policy_result`. Older drafts that contain the result object still
serialize correctly.

Provider reference: https://docs.salla.dev/product/create-product

Validation covers the creation lifecycle with a provider fake and frontend
interaction. These tests do not substitute for live provider acceptance after
deployment. No live product is created by the tests.
