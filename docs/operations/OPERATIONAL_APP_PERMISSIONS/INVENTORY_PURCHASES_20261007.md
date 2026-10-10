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

Web affected suites: 74 PASS across 7 suites (13 new inventory tests). Native
inventory verifier: 17 PASS; full native verifier chain and TypeScript check PASS.
Historical 34 native checks apply only to the previous APK, not this candidate.

## Exact isolated candidate and actual UI evidence

- Backend product: ef26adea2307362eb90d05fb16eb3dfa0e44794a.
- Backend + Web: 8eaffe51f7821a6ce663f1931017be33d092c2fd,
  tree 00de043f3d3b0b9e0241d91946a5e95a56fedb15.
- Native: 414eb82b8da76322e5acf54ed8e186985cbd4572,
  tree 763726d4731e1c7a9224e71621c1113bdf21ba7c.
- APK: AMASI-Operational-Inventory-414eb82-x86_64.apk, version 1.0.11 / 14.
  SHA256 11eaa0ccfa37ed5bd3ffcf0beaab4a3c09f48af1fac1fb8131a0ef81f4649d2d.
- Actual emulator: AMASI-R5-Isolated, Android 15 / API 35, x86_64.
- Backend: loopback port 8135, adb reverse; database
  operational_balance_device_uat_20261005. Synthetic accounts/catalogue only.
- Evidence directory: D:/codex-evidence/operational-inventory-20261007.

Actual Web component + real local API: WEB-STOCK-0701 recorded 3 products at
20 + 9 line tax and 2 components at 5 + 1.50 tax: net 70, tax 10.50, gross
80.50. Invoice alone left liquidity 1501 unchanged. A separate bank payment of
30 left invoice outstanding 50.50. Reload/read-back preserved one invoice.
The browser harness imports the actual committed component, with synthetic auth;
this is not a fresh full-shell or Production-auth acceptance test.

Actual installed native APK: APP-STOCK-0701 recorded 4 products at 25 + 15
tax and 2 components at 10 + 3 tax: net 120, tax 18, gross 138. A deliberate
after-save HTTP 503 preserved the pending command. User-visible retry returned
the same invoice once. Separate bank payment 38 left 100 outstanding; cash
payment 100 left zero. No receipt required. Web refresh showed the native result.
Force-stop/relaunch/menu/reopen preserved both invoices and quantities 7 / 4.
Final read-back: bank 938 (1006 - 30 - 38), cash 395 (495 - 100).
Evidence: inventory-retry-top.png, inventory-retry-success.png,
inventory-bank-paid.png, inventory-fully-paid.png, inventory-reopened-final.png,
final-readback.json and device-trace.jsonl. These synthetic values are not real
financial activity. The fixture blocks external connections and non-operational
runtime DB writes. No Accounting writer is imported.

Native image and RTL/header display observed. The browser initially could not
load cross-port synthetic image URLs; same-origin fixture image proxy resolved
catalogue/new-invoice images (naturalWidth 160). The older Web fixture invoice
retains its original unavailable cross-port image snapshot; it was not rewritten.
The fixture home Orders endpoint intentionally returns 404 outside this scope.
New purchase permission combinations/concurrency/custody are automated coverage;
this run does not claim fresh physical-phone or full-application UAT acceptance.

No requirements/lock/release identity changes. #1263 untouched. #1271 and native
#257 remain Draft. No merge/deploy/Prepare/Prepublish or Production financial writes.

## User-requested UI simplification

Web product source 1a0942e4e7714d4f466d87ef86e2fbedda57b969; native APK source
294f5ee84ffb07c8abb177d5b0871dd97308c991. Purchase entry now has normal supplier
selector, compact product/component choice, search/image cards, line qty/price/tax,
invoice/date/optional note, total and one Save Purchase action. History/quantities
live under Reports, read-only. Invoice payment is reached from the supplier entry;
write-only known-invoice lookup and explicit allocation preserved. Original pending
commands remain recoverable in their own flow. No Backend/API/financial contract,
dependency, lock, release identity or permission identifier changes.

Fresh checks: Web 82 PASS / 0 FAIL across seven affected suites (19 inventory);
native full typecheck/verifiers PASS, inventory25, permissions21, cases14.
An initial Web command referenced two non-existent test paths; corrected run above
passed all seven suites. Backend267 historical evidence preserved by zero backend
diff; not rerun for this UI-only change.

Actual Android15/API35 emulator installed simplified APK1.0.11/14, SHA256
fc81d6f2045ac5c58c556c74ac68852cd43c24b9fd8b1b1219ff9b370adb1828, installed hash
matches. Purchased 2 products on SIMPLE-0701: net20/tax3/gross23; form reset and
did not show reports/payment. Reports navigation showed invoice and quantities9/4.
Supplier -> Pay inventory invoice -> bank23 succeeded; remaining0, bank938-23=915,
cash395 unchanged, exactly one SIMPLE invoice. Real committed Web component also
visually checked against isolated API: only purchase fields/save shown.
Local evidence: D:/codex-evidence/operational-inventory-simplified-20261007,
simple-purchase.png, simple-saved.png, simple-reports.png, simple-paid.png,
readback.json, emulator-x86_64/source-proof.json and build-result.json.
Backend is unchanged loopback8135 / operational_balance_device_uat_20261005.
No new full application acceptance claim. Ready for owner UI review, not deployment.
