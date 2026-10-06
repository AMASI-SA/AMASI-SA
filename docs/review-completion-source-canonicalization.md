# Review completion source canonicalization

Scope: future explicit review operations only. No order discovery, backfill,
historical approval, worker policy change, or production data mutation.

## Failure and contract

The old source hash serialized selected raw business sections verbatim. The
refresh path (`order_engine/salla_refresh.py`) copies the carrier name into
`shipping.company_name`; a later webhook can retain only `shipping.company`.
That representation difference changed the hash despite identical carrier facts.
After provider confirmation, validation failed before the workflow/event commit.
The worker correctly stopped with `requires_review` rather than bypassing approval.

The incident order's provider-confirmed/source-changed boundary is established.
The exact pre-call raw snapshot was not retained, so this patch does not claim
that the carrier alias alone explains every historical order. Synthetic replay
demonstrates this mechanism with the real refresh, webhook, mapper, transaction,
worker and catalog code; it is not a live Salla order test.

## Canonical schema v2

Two separate hashes remain (source and loaded OrderDTO). The durable operation
stores `fingerprint_version=2` before provider I/O. Their combined contract is:

| Section | Retained facts / normalization |
| --- | --- |
| Identity | Raw id/reference, DTO order ID/number; no renumbering or case folding |
| Creation time | DTO creation instant (component cohort eligibility), normalized to UTC; no invented timezone for naive values |
| Items | All source item facts and all DTO item fields: product/parent/variant, quantity, SKU, options, custom fields, customer selections, prices, availability and fulfillment facts |
| Product aliases | An added item.product_id identical to product.id is redundant; unequal identities fail closed |
| Customer | Full source customer section and DTO customer/addresses; exact name/full_name and phone/mobile aliases only |
| Shipping | Entire source shipping and DTO shipping/recipient; equal company/company_name and method/shipping_method aliases; equal shipping_address/shipping.address relocation |
| Payment | Raw method/status/amounts and explicit paid/remaining/collection facts; complete DTO payment and totals |
| Other approval facts | Notes, options, customer/staff notes, gift flag |
| Numeric JSON | 1 and 1.0 equal; strings/option answers/SKUs are not coerced; nonfinite values rejected |

Conflicting aliases cause `409 review_completion_source_changed`; no precedence
rule hides conflicting evidence. Object key order is immaterial, list order is
preserved (option precedence can matter). Unknown content inside retained
business sections stays guarded; it is not silently dropped as metadata. No
global removal of customer/shipping/items, media, or arbitrary unknown fields.
New unexplained representation differences may therefore still fail closed and
need evidence before expanding the schema. This is not a claim that every
possible future Salla representation is equivalent.

Event timestamps and top-level status remain outside the hash as before.
Active/cancellation/payment eligibility are checked independently, including in
the final transaction. Acceptance configuration/product-component bindings and
their version/fence, workflow revision, component generation, actor authorization,
lease, and workflow/event/result transaction are unchanged.

Existing unversioned operations retain their original hash algorithm. There is
no automatic rehash, upgrade or transition out of requires_review. Explicit
reapproval validates the previous operation under its saved schema before
creating a new approval identity. Unknown schema versions fail closed.

## Verification

`backend/tests/test_review_completion_source.py` covers old-hash failure/new-hash
equivalence, other proven aliases, conflicting values, real commercial changes,
version compatibility, and real replica-set/ASGI replay:

refresh -> provider confirmation -> rejected event insert/transaction rollback
-> status webhook -> worker restart -> same operation -> reviewed workflow and
one event -> visible in /reviewed and reviewed-products catalog.

It also races two workers with manual retry, rejects changed products/options/
SKU/customer selection/shipping, and preserves component_acceptance_changed.
Provider transport/auth are synthetic. Production and historical orders are not
read or mutated by these tests. Broader review and G47 results belong to the
exact tested commit/CI artifacts, not to this design document.
