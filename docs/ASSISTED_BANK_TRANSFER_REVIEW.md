# Bank transfer via WhatsApp — unpaid Salla order + human receipt review

**Status: DRAFT / NOT DEPLOYED** — 27 November 2026, Asia/Riyadh  
Parent: [Mezan assisted orders #2139](https://github.com/AMASI-SA/AMASI-SA/issues/2139) / [bank task #2151](https://github.com/AMASI-SA/AMASI-SA/issues/2151).  
Branch PR: [#2153](https://github.com/AMASI-SA/AMASI-SA/pull/2153), based on draft assisted-order [#2140](https://github.com/AMASI-SA/AMASI-SA/pull/2140).  
WhatsApp counterpart [CSA #125](https://github.com/AMASI-SA/MEZAN-CUSTOMER-SERVICE/issues/125), [PR #126](https://github.com/AMASI-SA/MEZAN-CUSTOMER-SERVICE/pull/126).

## Owner-approved behavior

If the customer has paid (or claims to have paid) by **bank transfer**, WhatsApp AI can finish collecting the buyer/recipient details, national shipping address, verified Salla product options and quantities. Mezan creates **exactly ONE** Salla order with `payment.status=pending_payment` and `payment.accepted_methods=[bank]`. Then the Salla **ORDER status**, independently from payment status, must be set to `under_review` (Arabic: **بإنتظار المراجعة**) immediately after the Salla order is confirmed, even if the customer has not uploaded a receipt yet.

When the customer sends a receipt, a separately authorized private/local media inspector may check *appearance* (legibility, amount vs actual Salla total synced in Mezan, reference, date, visible beneficiary matching trusted bank directory, reuse/edit signals). The image remains in the scoped original Chatwoot conversation; no raw image, IBAN or bank info enters normal LLM prompts or GitHub. The WhatsApp bot may acknowledge receipt **for review**, not a confirmed bank credit. The customer service team reviews the original and **must reconcile actual money credited to the bank** before anyone may mark paid, start fulfillment or write GL/Qoyod.

A bank transfer screenshot is NOT proof of settlement. `receipt_appearance_checked_awaiting_bank_credit` is NOT an approved payment.

## Code on this draft branch

- `service.py` distinguishes server-verified `salla_bank_review` quote source from online card `salla_post_create`. Bank-only payment method, no pre-created link, no pre-known total, independently signed customer approval and idempotent one-time Salla POST; no paid state.
- `intent_writer.py`, `private_order_builder.py`, `private_vault_action.py`: Mezan source-verifies Salla product, variant/engraving, carrier eligibility, payment method; bank requires `payment_flow=bank_review` and `accepted_methods=["bank"]`. Buyer/recipient address/receipt fields remain encrypted in Mezan, not plain text to AI.
- `bank_order_initial_status.py`: immediately after confirmed creation, GET the exact order from Salla and safely submit one `POST /orders/{id}/status` with `slug=under_review` and `send_status_sms=False`, under a unique Mongo delivery fence and default-OFF `MEZAN_ASSISTED_SALLA_BANK_STATUS_ENABLED`. It NEVER calls payment-status, paid, GL or Qoyod. If GET/POST fails, returns an unresolved review-status state; do NOT repeat Salla Create Order or blindly retry a possibly successful status POST. Receipt-based status helper shares the same fence.
- `bank_order_readback.py`: get exact Salla ID/reference, verified **unpaid** bank method and positive SAR total from Mezan's canonical synced order; customer-proposed amount is never authoritative.
- `bank_receipt_review.py`: exact private-inspector schema and appearance-only verdict, never bank settlement. No production image/OCR/private vision inspector has been connected.
- `bank_review_queue.py`: immutable/replay-safe `salla_ai_bank_receipt_reviews` keyed per order + canonical Chatwoot message, with `awaiting_human_bank_confirmation` and hashed reference/attachment metadata. No screenshot URL, card data or GL.
- `bank_review_action.py`: HMAC receipt-only action, scope/tenant/contact/message binding. Salla bank receipt route `/orders-v2/assisted-salla/signed-bank-receipt-review` is off by default (`MEZAN_ASSISTED_SALLA_BANK_RECEIPT_ENABLED=0`); separate HMAC `MEZAN_ASSISTED_SALLA_BANK_RECEIPT_HMAC_SECRET`. Default-off Salla status flag controls update independently.
- `bank_review_staff.py` and `routes.py`: staff-only **receipt appearance** GET and triage POST, default-OFF `MEZAN_ASSISTED_SALLA_BANK_REVIEW_UI_ENABLED=0`, explicit `MEZAN_ASSISTED_SALLA_BANK_REVIEW_STAFF_IDS` / owner allowlist, returns safe metadata, never GL or paid. Staff may mark appearance reviewed or request clearer receipt. Bank credit confirmation must happen in a separately authorized financial workflow with bank evidence.
- `frontend/src/pages/AssistedBankReceiptReview.jsx`, `frontend/src/App.js`, `frontend/src/components/Sidebar.jsx`: draft route `/assisted-bank-receipts` and sidebar entry **إيصالات تحويل واتساب**, with visual warnings and appearance-only actions. No `confirm paid` button.

**Important existing accounting hazard:** `backend/bank_transfer_review_routes.py`'s `/{id}/confirm` posts a balanced bank GL group. NEVER invoke it from AI or from a photo appearance precheck; it is a separate bank-credit human confirmation workflow. The new code does not call it.

## Source/acceptance evidence

Official Salla API docs, reviewed Nov 27:
- [Create Order](https://docs.salla.dev/5394145e0): POST `/orders`, `pending_payment`, accepted method `bank` possible. Test the exact real merchant outcome in a controlled staging order.
- [Update Order Status](https://docs.salla.dev/5394148e0): POST `/orders/{id}/status` `slug=under_review`, not a paid mutation. Amasi's live statuses read-only check found customized `under_review` called **بإنتظار المراجعة**; do not hardcode its numeric ID.
- Merchant's existing Mezan order/GL code inspected above.

**Offline backend synthetic CI [run 45917707692](https://github.com/AMASI-SA/AMASI-SA/actions/runs/45917707692): 451/451 PASS** on implementation SHA `5b12d59aa7a0d571c45a38f82a5e5043924f63c0`. Includes the immediate under_review stage, bank-only checkout, amount/source checks, staff-only triage, appearance and duplicate receipt tests. This is **NOT** live Salla/Chatwoot acceptance and **NOT** a frontend build.

**CSA synthetic CI [run 45917373019](https://github.com/AMASI-SA/MEZAN-CUSTOMER-SERVICE/actions/runs/45917373019): 236/236 PASS** at prior bank-PR source, across four suites including bank review, guarded native send, readback and private PII field policy. Its new code is not live-connected either.

## Remaining gates

- [ ] App owner confirms actual Mezan OAuth permissions `products.read`, `payments.read`, `shipping.read`, `orders.read_write`; **not** blindly add scopes due previous `invalid_scope` (#2149).
- [ ] Provision signed-purpose HMAC/Fernet keys and TTL indexes, review role permissions and privacy for real bank receipt media; no PII to hosted AI by default.
- [ ] Connect/approve a private/local receipt inspector. Its output must be signed/tenant-scoped and only claim appearance; beneficiary must be checked against actual authorized bank account directory.
- [ ] Run one specifically owner-authorized Salla API bank-pending order on a disposable/staging account. Verify resulting bank payment selection, Salla under_review status and no paid/GL. Never test by creating arbitrary live customer order.
- [ ] Verify original private receipt remains accessible to authorized customer-service staff in Chatwoot and exact Salla order/Mezan amount sync; no customer data leaks into logs or model.
- [ ] UI frontend build + accessibility/RBAC acceptance and deployment of both Mezan backend/frontend with existing release guard. No production deploy authorized in this task.
- [ ] Define a separately authorized human bank-credit reconciliation workflow; connect Salla payment-state update and accounting ONLY after actual bank statement confirmation and audit evidence. Do not repurpose photo precheck as payment acceptance.
- [ ] Confirm timing of receipt sent *before* an order exists, human ownership handoff, duplicate webhook/screenshot, partial/wrong amount and abnormal status cases.
- [ ] Owner go-live approval, exact SHA traceability and rollback verification. All flags OFF until these gates pass.

## 2026-11-27 — Source-backed status/receipt acceptance update

New `bank_order_initial_status.py` is called from the purpose-signed Mezan Salla create endpoint only after an actual, confirmed Salla bank-only pending order ID is saved. This puts **ORDER status** `under_review` immediately, before any receipt, using the same once-only status delivery fence later used by receipt review. If the provider result is uncertain, it is a reconciliation state and cannot be blindly retried or reported as paid.

**Verified final backend synthetic suite: 451/451 PASS** in [run 45917707692](https://github.com/AMASI-SA/AMASI-SA/actions/runs/45917707692), source `5b12d59aa7a0d571c45a38f82a5e5043924f63c0`. Current commits after this source were documentation-only; React/frontend build and real Salla staging remain untested.

**Parallel CSA CI improved:** [run 45918477200](https://github.com/AMASI-SA/MEZAN-CUSTOMER-SERVICE/actions/runs/45918477200) **242/242 synthetic PASS** (includes 6 new bank-private-receipt native worker tests). An opt-in native receipt handler now prevents early sensitive bank attachment from entering the general multimodal model and stores only its canonical Chatwoot message/attachment reference. If the private inspector is OFF, it must say so honestly; no image authenticity claim. This pre-order attachment is NOT yet automatically linked to the eventual Mezan bank review. See [CSA #127](https://github.com/AMASI-SA/MEZAN-CUSTOMER-SERVICE/issues/127).

**Remaining human bank-credit mechanism:** [Mezan #2155](https://github.com/AMASI-SA/AMASI-SA/issues/2155) — an actual independently verified bank statement credit is required before any paid status/GL, and the old bank_transfer_review_routes.confirm is NOT a safe AI receipt-appearance action. Staff appearance triage remains human-review-only. Both bank PRs remain Draft, all flags OFF, no real customer orders/receipts/payment/WhatsApp messages, no production deploy.
