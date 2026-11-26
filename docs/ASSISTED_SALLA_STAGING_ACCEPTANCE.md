# Assisted Salla order — staging acceptance / release guard

Owner decision (27 Nov 2026): AI/Mezan creates **one** unpaid Salla order, waits for the same order to sync into Mezan, verifies Salla's actual payable amount, reads the **already synchronized** customer payment URL from the Mezan order, then sends the link in the **same WhatsApp conversation**. Do not generate new payment links or copy merchant admin URLs. The customer chooses whether to pay through Salla; no AI payment capture.

Related issues: [Mezan #2139](https://github.com/AMASI-SA/AMASI-SA/issues/2139), [quote/source #2141](https://github.com/AMASI-SA/AMASI-SA/issues/2141), [CSA P05 #116](https://github.com/AMASI-SA/MEZAN-CUSTOMER-SERVICE/issues/116), [CSA runner blocker #122](https://github.com/AMASI-SA/MEZAN-CUSTOMER-SERVICE/issues/122). Source PRs: Mezan #2140, CSA #104.

## Source-of-truth / state transitions

1. Bot verifies buyer/recipient and item/options, address, carrier and payment method, using its existing signed scoped Chatwoot native session.
2. Mezan retrieves actual Salla catalog IDs/option values, verifies carrier *eligibility*, and checks currently enabled electronic methods. A new server-owned quote record of source `mezan_verified_unpaid_order_intent_v1` may record **no final price**. The quote returns `verified_total_sar=null`, so the bot must never call the estimate a final price.
3. Customer explicitly approves creation of a **pending-payment** order for the selected items. Signed Chatwoot proof binds the exact quote ID/digest/version and current message.
4. Mezan's feature-gated existing Salla OAuth client performs exactly one `POST /orders` with payment `pending_payment`. Unique intent ID prevents duplicates.
5. If the response contains the Salla order ID but not its human reference, an authenticated **GET /orders/{id}** can recover reference_id. No second POST.
6. Existing Salla→Mezan synchronization stores the order and its `payment_checkout_url` / `urls.checkout` / remaining payment action.
7. Mezan's signed read-only checkout endpoint compares **same Salla order ID + reference**, merchant, conversation, pending/unpaid state, valid positive SAR total and approved HTTPS checkout host. For legacy preverified amount, the actual Salla amount must match quote ±0.01 SAR; for new post-create-priced mode, **Salla's synced total is authoritative**. Any unsupported currency, missing link/total, order mismatch, paid/partial status, or untrusted URL blocks automatic delivery.
8. CSA uses the existing native Chatwoot guarded send with a durable once-only ledger. If the link is not yet synced, retry only READ requests at bounded intervals, never Salla creation or a potentially delivered WhatsApp message. If latest incoming message changed/human took over, escalate rather than bypass authorization.

## Mandatory test before go-live

- [ ] All Mezan hermetic CI tests PASS at exact PR HEAD, including post-create order intent and Salla reference recovery.
- [ ] All CSA hermetic/worker tests PASS at exact PR HEAD. **Currently blocked** by CSA GitHub Actions jobs failing before steps (including no-code smoke); do not skip the gate. Inspect repo Actions runner/billing/policy UI, record error and recover CI.
- [ ] Mezan owner-only capability endpoint confirms *actual* installed merchant OAuth scopes for `orders.read_write`, catalog product read, shipping read and payment-method read; do not infer from requested defaults.
- [ ] Check at least one store-approved eligible carrier/shipping rate, actual buyer Salla customer ID or real customer-supplied email, and real national address. No fabricated data/phone/address.
- [ ] Salla staging/disposable test account or specifically approved synthetic order. No live store order before owner acceptance of exact staging procedure. Record Salla ID and reference only, not PII or private URL in GitHub.
- [ ] Confirm status remains unpaid and no GL/Qoyod/COD booking is performed on creation; post-payment collection is separate and verified.
- [ ] Confirm Salla's auto-generated URL appears in Mezan for API-created pending order (not only dashboard-created order).
- [ ] Confirm Mezan's checkout read returns the *same* order's actual SAR amount and live customer URL. Verify real hostname; configure exact allowlist in protected server secrets, not guessed domains.
- [ ] Confirm one native WhatsApp message, correct conversation, exact number and amount, after Salla/Mezan sync. No duplicate on Chatwoot webhook replay, timeout, server restart, or double agent attempt.
- [ ] Verify personalized gift products with required text/conditional options against actual Salla Create Order response.
- [ ] Negative suite: post-create amount missing/zero/non-SAR; wrong order ID/ref; link absent; dangerous hostname; stale quote; invalid token; unsupported COD/bank for post-create-only mode; product options missing; stale customer approval; human takeover; provider timeout; unknown send outcome.
- [ ] Configure 7-day TTL cleanup for temporary private quote PII; check database access, retention and privacy policy. Do not emit PII or links in log/CI artifacts.
- [ ] Owner go-live approval, exact-release SHA audit, rollback plan and production observability. **All assisted Salla feature flags OFF until all gates pass.**

## Important implementation notes

- `MEZAN_ASSISTED_SALLA_QUOTE_WRITE_ENABLED`: off by default. Owner-only source-validation pilot. Never expose owner credentials to the bot.
- `MEZAN_ASSISTED_SALLA_CONFIRMATION_ENABLED`: off by default; scoped signed customer approval proof.
- `MEZAN_ASSISTED_SALLA_ORDER_POST_ENABLED`: off by default; scoped idempotent Salla order creation.
- `MEZAN_ASSISTED_SALLA_CHECKOUT_READ_ENABLED`: off by default; scoped link readback after sync.
- Different purpose-bound HMAC secrets and approved account/inbox for confirmation, order, checkout. Never store secrets in repository.
- CSA native worker has NOT been wired live. Preview components and retry worker remain isolated. Further work must not unintentionally change Chatwoot staff handoff or accounting.
