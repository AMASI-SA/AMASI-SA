# Operational inventory purchase candidate

Approved scope: product/component quantity purchases in Web and native app.
Catalogue reads only canonical `mezan_products_v2` and inventory-tracked
`mezan_cost_resources_v2`, scoped to owner. Product identity is
`mezan_product_id`; component identity is `id`. Name/image/unit snapshot comes
from the source, not caller input. No Legacy fallback. No source inventory writes.

Actual supplier invoice records received whole-unit quantities, unit net price,
line tax amount, computed net/tax/gross and actor/time in the existing operational
CAS aggregate. Invoice itself creates a supplier obligation and no cash movement.
Separate existing movement payment allocates to that invoice from bank, cash or
employee custody. This is cumulative purchased/received quantity, not warehouse
available stock or inventory consumption. Fractional units are rejected explicitly.

API: GET inventory-catalog (entry context), GET inventory-purchases (reports),
GET inventory-purchases/entry/{supplier}/{invoice} (known invoice entry), POST
inventory-purchases (write). All under /api/operational-balances. Native uses the
two existing independent grants, checks real actor and live grant at CAS. No new
Opening, Audit or Accounting grants. No receipts required.

Same request retry/concurrency uses stable claim and CAS; invoice business identity
is supplier + invoice number. Conflicts across purchase/replacement/native MZ2
invoices are rejected. Later appearance in canonical MZ2 invoices stops refresh
for explicit reconciliation instead of adding a second debt. No automatic matching
or Accounting invoice is created. Old operational balances remain unchanged.

Fresh verification: 19 inventory Mongo/HTTP tests; full operational Backend suite
267 PASS (5 warnings), including MZ2-only runtime gate with products/resource
catalog Legacy traps. Initial red: inventory module missing; initial full regression
revealed independent read allowlist needed the new canonical product collection.
Allowlist test now exercises the purchase reader; periodic financial sync does not
load the product catalogue. No weakening of Legacy rejection.

Web component tests and native verification pass; new exact APK/browser/native UAT
pending in this checkpoint. Historical 34 native checks apply only to previous APK.

No requirements/lock/release identity changes. #1263 untouched. #1271 and native
#257 remain Draft. No merge/deploy/Prepare/Prepublish or Production financial writes.
