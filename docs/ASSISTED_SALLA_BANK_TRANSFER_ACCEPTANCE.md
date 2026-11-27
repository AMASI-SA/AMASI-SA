# Mezan ↔ Salla bank-transfer WhatsApp order — P06 acceptance

**Date:** 27 November 2026 (Asia/Riyadh)  
**Status:** DRAFT / feature flags OFF / not deployed  
**Owner intent:** the WhatsApp customer may report they already transferred money and upload an apparent bank receipt. The agent checks its *visible details only*, Mezan creates **one Salla bank-transfer unpaid order**, selects/ensures the store's **«بإنتظار المراجعة»** status, and customer service verifies actual bank deposit before marking paid or beginning fulfillment. The agent keeps talking politely and does not assert money is received.

Issue [CSA #139](https://github.com/AMASI-SA/MEZAN-CUSTOMER-SERVICE/issues/139); related [CSA #96](https://github.com/AMASI-SA/MEZAN-CUSTOMER-SERVICE/issues/96), [Mezan #2139](https://github.com/AMASI-SA/AMASI-SA/issues/2139), draft [Mezan PR #2140](https://github.com/AMASI-SA/AMASI-SA/pull/2140), [CSA PR #104](https://github.com/AMASI-SA/MEZAN-CUSTOMER-SERVICE/pull/104).

## Non-negotiable financial safety

- A screen capture may be forged, recycled, delayed, edited, or show a transfer to a wrong beneficiary. **An apparently readable/consistent receipt is NOT bank settlement confirmation.** The workflow cannot write `paid`, payment collected, GL/ledger, Qoyod, COD, or fulfillment; an authorized staff member checks actual funds from trusted bank records separately.
- Bank orders are **NOT** the prior Mada/credit-card checkout-link branch. No new payment link is generated/sent for bank review; no second Salla create POST for retries. Distinct pricing mode `salla_bank_review` and status `created_bank_review_pending`.
- Only an explicitly approved private image-inspector callback may process the canonical latest Chatwoot customer photo. Image bytes and raw receipt bank details remain private; they never enter the general ChatGPT/OpenAI model prompt or GitHub logs. The callback is an interface only, **not an installed live image analyzer**.

## Implementation prepared in draft PR #2140

1. `bank_receipt_review.py`: conservative typed apparent receipt fields, Saudi IBAN length+MOD97, exact beneficiary name/IBAN and transfer amount/currency/ref/time check against approved Salla receiving bank. Returns `appearance_plausible` or `needs_customer_clarification` and `bank_credit_confirmed=false`; never paid.
2. `bank_source.py`: existing merchant Salla OAuth `GET /payment/banks/{id}` with actual `payments.read` scope, only active customer-facing bank. If Salla does not return a verifiable IBAN, block rather than guess.
3. `bank_receipt_action.py` + default-OFF `POST /orders-v2/assisted-salla/signed-bank-receipt`: signed purpose-limited canonical customer image/message/attachment info with preliminary visible fields, secret distinct from Salla OAuth. `bank_receipt_stage.py` saves a redacted evidence row and HMAC transfer-reference reuse fence, never original receipt image or raw reference.
4. `bank_receipt_retention.py`: require actual Mongo TTL indexes on `salla_ai_bank_receipt_evidence.purge_after` (7 days) and HMAC `salla_ai_bank_receipt_refs.purge_after` (30 days), using explicit one-time approved migration only; no request-time automatic schema writes.
5. `intent_writer.py` and `service.py`: bank-only, separate source `mezan_verified_unpaid_bank_review_intent_v1`, independently verified receipt, actual enabled bank/payment/carrier/catalog, immutable encrypted order and customer consent. One `POST /orders` with `payment.status=pending_payment`, `method=bank`, `accepted_methods=[bank]`. **Do not include unchecked `store_bank_id` or `reference_id`**: official docs/changelog disagree on the current field, so controlled API acceptance must verify before use.
6. `bank_staff_review.py`: durable one-case `awaiting_staff_bank_verification` record for original Chatwoot receipt attachment, Salla order identity and amount, staff review required, no auto settlement; uncertain case write after Salla POST is quarantined without second POST.
7. `bank_review_readback.py`: read canonical Mezan synced Salla bank order, exact ID/reference/method/currency/total/under-review status and unpaid state, compare original receipt amount, flag mismatch; owner-only, default-OFF read pilot `GET /orders-v2/assisted-salla/bank-review/{intent_id}`.
8. `bank_status_guard.py`: discovers the exact **merchant-customized** active status named «بإنتظار المراجعة» (not the broad `under_review` slug). A real read-only connected-store status lookup showed more than one different customized status shares that same broad slug, including «تم المراجعة». Strict status selection and at-most-once `POST /orders/{id}/status` with `status_id` and `send_status_sms=false` is available only through explicit owner-only, default-OFF `POST /bank-review/{intent_id}/ensure-under-review`. Result requires Salla readback; unknown status update is never blindly replayed. No actual Salla order status was changed in this development turn.

Official sources: [Create Order](https://docs.salla.dev/5394145e0), [List Banks](https://docs.salla.dev/5394165e0), [Update Order Status](https://docs.salla.dev/order-status/update-order-status), [List Order Statuses](https://docs.salla.dev/order-status/list-order-statuses), [Merchant API Changelog](https://docs.salla.dev/changelog).

## Verified synthetic CI

- [Mezan Actions **run 46028544461**](https://github.com/AMASI-SA/AMASI-SA/actions/runs/46028544461): **471 tests PASSED** at implementation SHA `7aba0866fd1f3f92335bc0d179cc16d0981b4c53`. Synthetic tests cover draft/product and option, image appearance, strict active bank, HMAC, transfer-reference dedupe, independent bank-only quote/customer consent, one unpaid Salla create, staff review case, review status ID, replay limits and real TTL gate. No real provider or bank mutation.
- [CSA Actions **run 46028319723**](https://github.com/AMASI-SA/MEZAN-CUSTOMER-SERVICE/actions/runs/46028319723): **235 tests PASSED** for guarded WhatsApp receipt-image callback, Mezan signed sender, bank-order acknowledgment and one-review notice, plus existing online checkout. The image analyzer is stubbed with a safe private synthetic callback. The worker source is still draft; no real WhatsApp messages.

## Production acceptance blockers — NOT DONE

- [ ] Obtain actual Mezan connected merchant OAuth `payments.read` and `products.read` grants in Salla Partners Portal (Mezan #2149); do not modify defaults prematurely because unauthorized requested scope may break OAuth with `invalid_scope`.
- [ ] Verify Salla's currently accepted bank account field (`store_bank_id` vs `reference_id`) in an approved disposable one-order staging test; verify Salla's actual customer bank `GET /payment/banks/{id}` returns IBAN and exact beneficiary. Never assume previous screenshot/bank data.
- [ ] Install a privacy-approved private vision/OCR callback without forwarding image to the general AI model, ensure bank receipt photo authenticity cannot be proven from appearance alone, test no PII leaks/unsafe media URLs.
- [ ] Set up private HMAC/TTL indexes and authorized staff review permissions. The current readback route is **owner-only**, not yet a staff-facing queue/UI. Only staff verifies actual bank statement and then may trigger a separately reviewed payment confirmation process; no such auto-posting exists.
- [ ] Connect default-OFF native worker receipt hook and bank order stage to live Chatwoot and create ONE owner-approved synthetic Salla bank order, verify exact customized under-review state, unpaid method/amount, Mezan sync, safe customer notice, dedup and full rollback. No production flags or deployment until acceptance.
- [ ] Investigate actual merchant API response if a bank order initially appears as `payment_pending`: the owner-gated status helper is prepared but is **not** automatically enabled in the Salla-create worker. Never claim status is confirmed before readback.

**No real Salla bank order, bank credit, payment posting, production merge/deploy, customer message or private receipt image was processed here.**
