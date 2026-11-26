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
