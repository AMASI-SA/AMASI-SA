# Assisted Salla order creation V1 — design + implementation checkpoint

**Date:** 2026-11-26 Riyadh · **Status:** DRAFT, no production enablement

Owner tasks: [Mezan #2139](https://github.com/AMASI-SA/AMASI-SA/issues/2139), [CSA #96](https://github.com/AMASI-SA/MEZAN-CUSTOMER-SERVICE/issues/96), discovery [CSA #100](https://github.com/AMASI-SA/MEZAN-CUSTOMER-SERVICE/issues/100).

## Product decision

The WhatsApp customer-service AI collects: **recipient name and phone → destination and available shipping carrier → payment method → exact Salla products/options/quantities → confirmed total → customer confirmation**. Mezan creates **one** Salla order with `payment.status=pending_payment`. Salla owns checkout and generates the payment link itself; Mezan already syncs/display links. No custom payment-link generator, checkout service or additional gateway. Do not treat a new unpaid order as paid.

## Inspected current Mezan code

- Existing rotating OAuth + Salla Merchant client: `backend/salla_integration/service.py:call_salla(db,user_id,"POST","/orders",json=...)`. The default *requested* scope includes `orders.read_write`; check the connected store's *actual token* scope (and shipping/product scopes) before enabling.
- Existing order synchronization: `backend/salla_integration/sync.py` persists `payment_checkout_url`. This PR adds documented `urls.checkout` fallback in addition to `payment_actions.remaining_action.checkout_url` and legacy `checkout_url`.
- Existing canonical mapper: `backend/order_engine/mapper.py` now also reads `urls.checkout` fallback. Mezan's `OrderDetailsV2.js` already shows open/copy checkout buttons; NO new link UI is needed.
- **Manual Salla pending-payment order** #302076044 from owner's screenshot shows checkout action in Mezan. This does NOT prove every API-created pending order produces an identical sync payload; verify with a controlled approved test, no second order for link fetching.
- New `salla_order_creation.payload` implements a strict allowlist and only `pending_payment`, with 2026 national-address fields. No price, total, paid status or checkout URL can come from AI.
- New `salla_order_creation.service` checks **two independent server-persisted proofs**, stores idempotency intent in Mongo using stable unique `_id` BEFORE one provider POST, and NEVER blindly retries timeout/unknown result. No accounting writes.
- New route: `POST /api/orders-v2/assisted-salla/create-confirmed`, mounted on server and restricted to owner. `MEZAN_ASSISTED_SALLA_ORDER_POST_ENABLED=1` required; **defaults OFF**. The endpoint will fail closed unless both proof records exist. The future WhatsApp agent must NOT use owner credentials or write directly; Action Gateway service-to-service scope remains outstanding.

## Trusted proof schema — internal, never client-produced

Before a POST the backend must hold:
1. `salla_ai_order_quotes` row: user_id, intent_id, inbox_id, conversation_id, payload_digest, version, `state=verified`, `source=mezan_authoritative_quote_v1`, verified_at/expiry UTC, positive two-decimal total_sar, and true flags for products, shipping, pricing and eligible payment methods. Quote writer MUST fetch authoritative Salla/Mezan pricing, inventory, options and carrier availability; the LLM cannot write its own "verified" quote.
2. `salla_ai_order_confirmations` row: same scoped identifiers and digest, matching quote_version, `source=chatwoot_customer_inbound_v1`, `confirmed=true`, unique customer inbound message_id, confirmed_at after quote verification and within 30 minutes. This must derive from *verified* Chatwoot webhook/tenant/inbox/customer role. AI replies and staff notes do not count.
3. `salla_ai_order_intents`: stable sha256 tenant+intent as Mongo primary key, one insert before POST. `created` returns Salla order_id/reference_id, `unknown` requires reconciliation; every subsequent replay returns recorded state, NEVER posts again. No raw customer details or URLs in this ledger.

The proof writers are **not implemented** in this checkpoint; therefore this endpoint cannot currently create an order even if the feature flag were accidentally enabled. This is intentional fail-closed delivery.

## Acceptance matrix

| Gate | State | Evidence required |
|---|---|---|
| Official Merchant API create endpoint | SOURCE REVIEWED | https://docs.salla.dev/5394145e0 (2026-09-21) |
| National address fields | SOURCE REVIEWED | https://docs.salla.dev/orders/update (2026-10-08) |
| Pure pending-payment builder | CODED | synthetic pytest CI |
| Independent quote + inbound confirmation validation | CODED | synthetic pytest CI, signed event integration still needed |
| Idempotent POST / unknown outcome | CODED | synthetic pytest CI, Mongo concurrency integration still needed |
| Existing payment URL read path | CODED | new `urls.checkout` fallback, runtime source provenance still unverified |
| Live Salla OAuth orders.read_write | NOT CHECKED | owner-scoped non-secret token scope |
| Catalog/options/stock authoritative quote writer | NOT BUILT | separate Mezan P03 |
| Chatwoot signed customer confirmation writer | NOT BUILT | CSA integration / Mezan Action Gateway contract |
| Action Gateway scoped service-to-service auth | NOT BUILT | authenticated tenant+inbox claims, never share owner token |
| Salla one-order staging E2E and link sync | NOT RUN | explicit approved synthetic test and readback |
| Production rollout | NOT APPROVED / OFF | owner final approval after acceptance |

## Security, reconciliation and accounting

- Never store secrets, raw customer conversation or contact details in GitHub. Approved privacy controls and customer data handling must be reviewed before sending to AI.
- Bank transfer receipts and COD must not automatically generate a paid state or cash collection. All new orders start `pending_payment` in this V1 until an explicit verified payment workflow is added.
- No direct DB write by LLM or chat worker, no bypass of Action Gateway, and no product/stock/price invention. Exact confirmed cart/quote must be immutable.
- On ambiguous Salla response, mark `unknown`, reconcile manually/safely using Salla order evidence and owner authorization. Do not retry blindly.
- Existing operational balance/Qoyod/employee workflows remain untouched. This PR adds only a new disabled order creation path and read-only checkout fallback.
- Tests must cover invalid address/Saudi national address, eligibility, revisions/expired consent, tenant isolation, duplicate messages/concurrent calls, unknown response and provider event replay.
- Salla's `urls.admin` is for staff, not payment. Do not substitute it for `urls.checkout`.

## Next instructions

1. Run branch-specific synthetic CI and fix until PASS; confirm no global conftest/test dependency contamination.
2. Create a Mezan authoritative quote adapter, using actual product/variant/option IDs and shipping rates; validate OAuth product/shipping scopes, avoid broad permission requests without need.
3. Build a signed Chatwoot inbound confirmation writer and Mezan Action Gateway service-to-service authorization; wire quoted state machine with retries/corrections and human takeover.
4. Test one authorized synthetic creation in a safe environment, confirm Salla order ID and existing Mezan synchronization, check checkout URL if available; no new link generation.
5. Review CI/PR, merge only with appropriate owner permission, never deploy/live-enable until gates pass. Update #2139 and CSA #96 with each commit/CI/result.

## 2026-11-26 — Additional implementation checkpoint (signed CSA→Mezan channel)

- `backend/salla_order_creation/signature.py`: pure HMAC-SHA256 signature verification over UTC timestamp, canonical UUID delivery and exact raw JSON; 300-second clock skew, strict JSON fields/scope/action type; no Salla credential in signed body.
- `POST /api/orders-v2/assisted-salla/signed-action`: default-off endpoint restricted by `MEZAN_ASSISTED_SALLA_ORDER_POST_ENABLED`, fixed `MEZAN_ASSISTED_SALLA_OWNER_ID`, `MEZAN_ASSISTED_SALLA_CHATWOOT_ACCOUNT`, `MEZAN_ASSISTED_SALLA_CHATWOOT_INBOX`, `MEZAN_ASSISTED_SALLA_HMAC_SECRET`. All settings required; do not commit values.
- Signed request contains **only** account/inbox/conversation, intent ID, quote ref/digest/version and customer-inbound confirmation message ID. Mezan loads `order_payload` from its own trusted persisted quote; account/tenant/context binding and both separate proof rows remain mandatory before a single Salla POST.
- `salla_ai_action_deliveries` stores the signed delivery's unique hash; repeated signed delivery never causes second provider POST. The order intent collection also uses a stable unique Mongo `_id`.
- Provider response total currency and amount are compared to the verified SAR quote within 0.01. `created_amount_unverified` is NOT eligible for automatic checkout link delivery.
- `GET /api/orders-v2/assisted-salla/capabilities` provides owner-only readback of stored OAuth scope presence without exposing secrets; it does not prove live API authorization. `GET /intents/{intent_id}` provides owner-only safe creation state for reconciliation.
- Complementary customer-service pure draft/transport lives in **CSA PR #104**; independent proof writers are still to be built in Mezan **#2141** and CSA **#105**.
- Synthetic GitHub Actions `Salla assisted order (synthetic only)` run **45882595487** passed **57 tests** at source `45a8a3447e317dc159fb9fe83b934eb69234d4e6`. Separate unrelated `Unified Ads Manager Read Only` job failed because `_fetch_daily_spend` did not match its pre-existing reviewed AST digest; do not alter ad accounting in this Salla PR. Overall PR is **not** a green release.
- The endpoint is **not live**, shared secret and action-bridge deployment are **not configured**, no genuine quote/confirmation proof rows exist yet and no real Salla create-order call has been run. All production flags remain OFF.


## 2026-11-26 — Read-only catalog and carrier-rate source adapters

- `backend/salla_order_creation/readers.py` adds **read-only** `read_exact_product` (`GET /products/{id}`) and `read_shipping_rate_candidates` (`GET /shipping/companies/estimate-rate`) using the EXISTING refreshing Salla client, with lazy runtime import for hermetic tests.
- Product reader rejects absent `products.read` scope, wrong IDs, hidden/unavailable/out-of-stock products, untrusted quantity, non-SAR price and malformed option/value IDs. It reports base price + option facts only; **`can_use_as_final_quote=False`** until product options, stock and discounts are validated for the exact cart.
- Carrier reader checks existing `shipping.read_write` scope, takes exact country/city + optional postal code, parses Salla rate `id`, `company_id`, estimate amount and COD service fee separately. **`is_final_cart_quote=False`** because the estimation endpoint can omit new cart weight/options and may not reproduce final shipping charge.
- Tests: `backend/tests/test_salla_order_creation_readers.py`, including scope-denial, bad identity, non-SAR/zero/unknown stock, malformed price, estimate-vs-final guard, fake provider responses. This code is not an authoritative quote writer and does not satisfy the `salla_ai_order_quotes` record required to POST; work remains in #2141.
- New provider-failure guard prevents marking `success=false` response as created even if it contains an ID; latest prior isolated [run 45882923892](https://github.com/AMASI-SA/AMASI-SA/actions/runs/45882923892) **59 PASS**. First expanded reader CI attempt [45883296371](https://github.com/AMASI-SA/AMASI-SA/actions/runs/45883296371) failed at import time due missing FastAPI in isolated runner; lazy-import fix commit `8b4e624dc30b90067817fbb966cfbdd4e622e555` requires new exact-head CI result before declaring reader tests passing.
- Official source documents: https://docs.salla.dev/product/product-details and https://docs.salla.dev/6899590e0 (retrieved 2026-11-26).

## 2026-11-26 — Latest exact-head acceptance

- [GitHub Actions **45883513695**](https://github.com/AMASI-SA/AMASI-SA/actions/runs/45883513695) on commit `326e9cb6f85ebae51675cabc21cf58c00692dcc8`: **80/80 synthetic order creation, HMAC and source-reader tests PASS**. Earlier import failures were fixed by lazy runtime loading in the product and carrier readers; do not count failed attempts as accepted tests.
- No real Salla API create-order HTTP POST or live customer checkout has been exercised. The readers explicitly tag Salla shipping estimates as **non-final**. This PR is still a draft and feature OFF. Broader unrelated CI may still fail, so this does not authorize merge or deployment.

## 2026-11-26 — Signed consent, returning buyers and exact catalog/carrier preflight

- `backend/salla_order_creation/confirmation.py`: new independent signed Chatwoot customer proof type, quote scope/digest/version/timestamp revalidation, immutable Mongo `_id` dedupe for `salla_ai_order_confirmations`. The `POST /api/orders-v2/assisted-salla/signed-confirmation` endpoint is gated by `MEZAN_ASSISTED_SALLA_CONFIRMATION_ENABLED=1`, **separate from the order POST feature flag**, and only accepts the approved account/inbox via its purpose-bound HMAC.
- Distinct private keys: `MEZAN_ASSISTED_SALLA_CONFIRMATION_HMAC_SECRET` for consent and `MEZAN_ASSISTED_SALLA_ORDER_HMAC_SECRET` for order creation. The prior unsuffixed HMAC env name is no longer accepted by these draft endpoints. Never share or commit the secrets.
- The two signed HTTP routes now stream and cap request bodies before allocating, and reject abnormal/missing Content-Length, oversize payloads and malformed signatures. Tests remain hermetic; FastAPI and `server.py` syntax checked in dedicated CI.
- The pure `customer_identity.py` uses an exact, owner-scoped verified Salla customer ID for repeat buyers, or requires the *real buyer-provided email* for first-time Salla customers; it never substitutes the gift recipient, invents an email or assumes two customers are the same from an approximate name. Salla Create Order schema: https://docs.salla.dev/5394145e0 (name/mobile/email required only when customer ID missing).
- Product snapshot `readers.py` now records option types and SKU variant IDs/stock; `catalog_validation.py` checks exact canonical value IDs, required options, product/variant quantity, live source/freshness and total quantity across duplicate lines. SKU-only inputs remain unsupported until resolved to an exact catalog ID; price hints are **NOT** final quote evidence.
- Shipping source records also carry checked country/city, and `shipping_selection.py` validates the customer-selected courier ID/eligibility/COD amount against exact destination and rate. **The shipping estimate is NOT the final cart quote and cannot authorize creation.**
- The original Mongo UTC-naive timestamp behavior is safely normalized to UTC for *database-backed proof records*, never assuming the local server timezone. The customer provider message time has strict bounds.
- [CI run 45889071228](https://github.com/AMASI-SA/AMASI-SA/actions/runs/45889071228): **158 synthetic tests PASS** on source `f1eebea9bcbadad0132527210a0f247074d7d83d` (including HMAC limits, proof persistence, catalog options, buyer identity). A later expanded carrier/country rate test suite and syntax check require **their own exact-head CI**; do not automatically add unverified cases to 158.
- P02 CSA counterparts [PR #104](https://github.com/AMASI-SA/MEZAN-CUSTOMER-SERVICE/pull/104): protected draft persistence and signed consent/action sending; [CI 45889365933](https://github.com/AMASI-SA/MEZAN-CUSTOMER-SERVICE/actions/runs/45889365933) **73 tests PASS** on its earlier exact source.

**Still NOT DONE:** full server-owned final cart quote writer, live proof writers hooked into native Chatwoot worker, authorized scope verification, actual one-order Salla creation, provider checkout readback, full backend regression/production deployment approval. Quotes from estimates MUST NOT be persisted as `state=verified`. Signed confirmation proof persistence may be safely tested in isolation before enabling order posting, but both feature flags remain OFF.

## 2026-11-26 — Carrier selection and exact source verification checkpoint

- Added `backend/salla_order_creation/shipping_selection.py` to check a selected Salla carrier/rate against the **same country, city and courier ID** and to disallow COD if the provider estimate does not include a verified COD service. It verifies scope/freshness but returns `can_create_order=False` because estimates do not prove the final amount of a particular cart.
- Added `backend/tests/test_salla_order_creation_shipping_selection.py` and extended the isolated CI.
- **Verified GitHub Actions run [45889547290](https://github.com/AMASI-SA/AMASI-SA/actions/runs/45889547290): 171/171 hermetic synthetic tests PASS** on implementation commit `97ad91a122b403a9fc2eb44fcb7ed9620bcbb3a8`. This is a dedicated synthetic workflow, **not** end-to-end external Salla/Chatwoot test or full repository green status. General unrelated CI requires separate review.
- Branch has documentation-only commits after this tested implementation; feature flags still OFF and no real orders have been created.
- Outstanding gate remains a **server-controlled, customer-specific final cart quote** incorporating actual option/variant pricing, VAT, discounts, carrier shipping and COD fee, or another authoritative Salla supported final quote. A standalone carrier estimate MUST NEVER be marked as an eligible final order quote.


## 2026-11-26 — Honest preview (no premature final cart total)

The buyer-facing flow now has an isolated, source-restricted **read-only estimate** path:

- `backend/salla_order_creation/quote_preview.py` combines validated product option/variant identities with the destination-specific, carrier-specific Salla *estimate*; it returns visible line-price hints and carrier estimate but deliberately returns `verified_grand_total_sar: null`, `is_final_cart_quote: false`, `can_create_order: false`. It never invents a payable amount or creates a checkout/payment URL.
- `backend/salla_order_creation/readers.py` checks the actual stored merchant OAuth grants for either `products.read`/`products.read_write` and `shipping.read`/`shipping.read_write`; it does not infer installed grants from requested defaults.
- New read-only `read_checkout_payment_methods` uses Salla's `GET /payment/methods` with `payments.read` and distinguishes store-enabled candidates in the Create Order allowed enum (`mada`, `credit_card`, `bank`, `cod`) from other store gateways (Apple Pay, BNPL, etc.). A payment option's presence does **not** establish COD eligibility for the selected carrier/branch. Actual `payments.read` token scope is **not yet checked**.
- Official docs checked on 2026-11-26: [Create Order](https://docs.salla.dev/5394145e0), [Product Details](https://docs.salla.dev/product/product-details), [List Estimate Rates](https://docs.salla.dev/6899590e0), [Available Payment Methods](https://docs.salla.dev/payments/available-methods), and [Twilight cart SDK](https://docs.salla.dev/twilight/react/sdk/cart). The merchant Create Order API is confirmed; a supported **merchant server-side final-cart pricing endpoint** with exact shipping/discount/tax rules was **not identified in these sources**. Twilight's cart SDK is a customer/storefront flow, and `priceQuote` asks guests to log in; it is **not** a drop-in backend quote for independent Chatwoot service.
- New offline tests cover preview uncertainty and method filtering. Exact-head CI for the new tests is **pending**, not yet claimed successful.

**Still blocked:** server-authored, authoritative final price for the exact customer checkout cart. Never label a subtotal of product listings + carrier estimate as final or sign it into `salla_ai_order_quotes`. An owner-reviewed manual pilot quote (if separately authorized in the future) would be an interim mode, not autonomous AI pricing. No feature flags were enabled, no real order placed, no existing financial/accounting entry changed.
