# Assisted Salla order — staging acceptance / release guard

## 2026-11-27 — New exact-head CI/OAuth acceptance checklist

- [x] Previous Mezan synthetic suite failure 46057491664 diagnosed from actual job logs as test harness import of absent FastAPI via `order_engine/__init__.py`; not interpreted as application logic regression.
- [x] Added separate test-only pure mapper runner to execute existing canonical mapper pytest against actual `mapper.py/models.py` without booting FastAPI or importing real Mezan server; new isolated workflow includes it.
- [ ] **Current exact-head** isolated mapper + assisted-order suites and UI read-only source guard must all PASS in GitHub Actions. Historical Mezan MCP Gateway run 46057491486 PASS was at an earlier SHA; repeat verification on release head.
- [ ] With owner credentials already managed by Mezan, call `GET /api/orders-v2/assisted-salla/capabilities` read-only and examine ONLY the boolean capability fields (no OAuth tokens). `payment_bank_read_scope`, `catalog_read_scope`, `shipping_read_scope` and `orders_create_scope` represent STORED Mezan OAuth token claims, NOT actual live API permissions.
- [ ] Owner approves Partners Portal `products.read` and `payments.read` (and any required carrier permissions) before reconnect/re-authorization; inspect independent Mezan authenticated Salla endpoints without disclosing bank IBAN or bearer tokens in CI/issues. Confirm active receiving bank IBAN with trusted private readback.
- [ ] No live Salla order/status/paid/fulfillment, WhatsApp sends, GL/Qoyod or production merge/deploy before complete positive E2E.


## 2026-11-27 — Salla 2026 payment-status shape and independent provider scopes

- [ ] New GET Order Details responses can differ from the deprecated expanded schema (Salla documentation last changed 2026-10-08; https://docs.salla.dev/5394147e0). The documented expanded structure includes `payment_actions.remaining_action.paid_amount: {amount, currency}` and `payment_methods[]` with paid portions. The status-changing bank action now accepts **explicit finite zero** from top-level `paid_amount` or the documented nested SAR money object, while rejecting missing, malformed, non-SAR, conflicting or nonzero amounts, paid/refunded status and partial `payment_methods`. Test the **actual Mezan app's current** light-format response in protected staging before enabling this code; examples alone do not prove live field presence.
- [ ] Official POST `/orders/{order_id}/status` supports `status_id` for custom sub-status and `send_status_sms=false` (https://docs.salla.dev/5394148e0). Our single guarded POST is isolated from payment/GL; no POST unless exact unpaid bank pending source/merchant identity and one-time Mongo intent all agree.
- [ ] Official GET `/payment/banks/{bank_id}` requires `payments.read`, and its public example omits IBAN entirely (https://docs.salla.dev/payments/bank-details). The current private bank precheck **must fail closed** without an independently verified, active beneficiary IBAN from Salla's authenticated bank source. Do NOT invent or hardcode an unverified receiving IBAN, assume merchant payout-bank is customer receiving bank, or ask the LLM to infer it.
- [ ] Status readback requires Mezan's current GET `/orders/statuses`, exact merchant customized ID/name and synced order child status; generic `under_review` or canonical `status.name` is not enough.
- [ ] No production activation before Salla Partners Portal `products.read` and `payments.read` grant/re-authorization (#2149), token-scope readback, current exact-head CI PASS, private receipt processing, human bank-credit confirmation and one staging order evidence.


## 2026-11-27 — Additional non-regression gate for exact merchant bank state

- [ ] Confirm actual Salla statuses from **Mezan's own** OAuth connection, not the separately connected Salla plugin. The currently observed merchant customized IDs are pending «بإنتظار الدفع» **1555791576** and review «بإنتظار المراجعة» **780246489**; if merchant statuses change, re-discover and fail closed until verified.
- [ ] A generic `under_review` parent slug or top-level canonical `status.name` can conceal child «تم المراجعة» (custom ID 1539366206). Readback must match `status.customized.id` + `status.customized.name` (strict real Salla raw mapping), not `status_native` alone.
- [ ] Initial write: exact source custom «بإنتظار الدفع», unpaid bank method, matching owner/intent/order/staff-review record, unique target in live statuses. Already target status may return read-only with **zero POST**; any other child ID/slug, paid/unknown amount, missing field, tenant mismatch or missing case denies without POST.
- [ ] Re-read order immediately before the only possible status POST: if staff/webhook advanced it, abort and quarantine for manual reconciliation; no blind retry. After POST, re-read exact ID/slug/unpaid and never claim confirmed review if payment or status changed in flight.
- [ ] Execute new mapper/status/readback synthetic regression tests at **current branch HEAD** and controlled one-order staging E2E, verifying no duplicate Salla order, no backward transitions, zero unapproved bank credit and no accounting/fulfillment writes. Current CI remains queued/unverified.


## 2026-11-27 — Review UI acceptance (DRAFT)

- [ ] Build and render `/whatsapp-bank-review` in Mezan V2 under authenticated owner, granted admin and explicitly permitted customer-service accounts; verify navigation hidden for users without `bank_receipts.view`.
- [ ] Check backend 403 for unauthorized direct URL regardless of hidden menu. Confirm wrong configured merchant owner and disabled `MEZAN_ASSISTED_SALLA_BANK_REVIEW_READ_ENABLED` give safe refusal, not an empty misleading queue.
- [ ] Inspect synthetic image/file/text cases from `GET /bank-review/worklist` and per-intent readback, without PII in page HTML, browser URLs, source links or model calls.
- [ ] Confirm no transaction-mutating frontend HTTP verbs in the new page, and that existing `BankTransferReview` ledger confirmation UI is completely separate.
- [ ] Run `frontend/src/pages/WhatsAppBankReview.test.js`, `frontend/src/components/MezanV2NavigationShell.test.jsx`, page build and Mezan API smoke against a secure synthetic preview; **NOT run / passed yet**.
- [ ] Only after exact source CI, Salla independent OAuth scopes, protected bank document parser, staff credit-check procedure and one Salla staging order may owner consider go-live. No production activation yet.


## 2026-11-27 — Staff authorization, worklist and reconciliation acceptance

- [ ] Source-only staff queue `/bank-review/worklist` and per-intent readback must reject unauthenticated viewer/warehouse roles; grant only explicit `bank_receipts.view` (plus owner/admin defaults via existing role catalogue), not broad `orders.manage`.
- [ ] Verify user effective permissions use Mezan's `_effective_perms`; a forged permissions field must not authorize. Bank review staff queries must resolve the configured merchant owner from trusted server settings, not staff user ID. An incorrect or unconfigured merchant owner fails closed.
- [ ] Confirm paginated (limit <= 25, offset <= 1000) owner-scoped case list includes only `awaiting_staff_bank_verification`, `paid=false`, `bank_credit_confirmed=false`, `fulfillment_eligible=false`, and never leaks raw receipt, IBAN, transfer reference hash, buyer name or media URL.
- [ ] Original receipt stays in protected Chatwoot. The list returns source IDs for authorized staff to locate the original; Chatwoot permissions and bank statement verification are independent.
- [ ] Ensure exact Salla custom review name is confirmed on same synced Salla/Mezan order before staff accepts; reject generic shared `under_review` and `تم المراجعة`.
- [ ] The queue only allows reading and cannot approve bank credit or trigger Salla status, account/GL, fulfillment or refund writes.
- [ ] New synthetic test module `test_salla_order_creation_bank_staff_worklist.py` is included in `salla-assisted-order-synthetic.yml`; **latest exact-head result still unverified / queued**. Do not switch production flags ON from the unit tests alone.


## 2026-11-27 — Live Salla status metadata evidence and bank receipt readback guard

- [x] Read-only Salla merchant status listing confirmed customized **بإنتظار المراجعة = 780246489** and **بإنتظار الدفع = 1555791576** (27 Nov 2026), with multiple other merchant statuses including «تم المراجعة» sharing generic `under_review` slug. These metadata were obtained via a separately connected Salla app; they **DO NOT certify Mezan's OAuth permissions**.
- [x] Source-only Mezan `bank_status_guard.py` selects the exact custom review name, not the shared slug, and `bank_review_readback.py` now requires that exact synced custom name rather than accepting `under_review`. Synthetic regression checks cover wrong same-slug state; not yet confirmed by exact-head CI.
- [x] Source-only signed preliminary receipt type `image|file|text` with customer-text `source_attachment_id=0`, other media requiring positive original Chatwoot attachment ID. Structured claims never set paid or release fulfillment.
- [ ] **Still BLOCKED**: exact-head GitHub Actions synthetic PASS; Mezan's own current Salla products.read/payments.read/bank permission token readback (#2149); tenant/host secrets/TTL; approved private media parser; authorized staging one-order post/create/readback; independently checked staff bank-credit decision. No production merge/activation or actual customer receipt/order.



## 2026-11-27 — Multi-turn protected-field and source-revision UAT gates

- [ ] Verify actual Mezan field ACK has only `state=saved`, safe `fields_present`, revision and stable opaque `vault_ref`; never names/phones/text/location/payment URL.
- [ ] Confirm CSA PII-free SQLite progress ledger follows one Chatwoot source contact across multiple real incoming messages, supports verified recipient→address advancement, and does not persist actual customer values or transmit them to OpenAI. Complete stage only when all required fields have Mezan saved receipts.
- [ ] Confirm same message retry is idempotent; different contact, stale revision or staff takeover cannot advance stage.
- [ ] After quote preparation, customer edits any private field: Mezan MUST detect changed encrypted vault revision and refuse Salla POST, requiring fresh quote and customer confirmation. Verify encrypted Salla quote cannot be opened with wrong owner/intent or old Fernet key.
- [ ] This is DRAFT until new exact HEAD CI and owner-approved single staging Salla order/checkout link succeeds. Last older 297 synthetic PASS is not certification of these new source protections.

## 2026-11-27 — Added private-data acceptance gates

- [ ] Install `salla_assisted_private_fields_ttl_v1` on `salla_ai_private_order_fields.purge_after` and `salla_assisted_quote_purge_ttl_v1` on `salla_ai_order_quotes.purge_after` with expireAfterSeconds=0 in an explicitly authorized migration. Confirm both readbacks before any real data writes. Do NOT migrate other collections/operational balances.
- [ ] Provision different production-only Fernet key `MEZAN_ASSISTED_SALLA_PRIVATE_FIELDS_KEY` (optional _OLD rotation), and purpose-specific HMAC secrets for private field write, vault quote, customer approval, Salla POST and checkout read. Never print, commit or send keys to the AI.
- [ ] Verify bot user cannot read raw buyer name, phone, email, address, coordinates or actual personalized engraving text from vault or Mongo quote, only opaque field-presence metadata and refs.
- [ ] Use signed Chatwoot current-customer public message and immutable contact_id for private field writes and quote; staff takeover must stop capture immediately. Do not send those values to OpenAI in normal context/history.
- [ ] Verify encrypted temporary quote read can be decrypted ONLY by Mezan after account/intent proof; wrong tenant, tampering, expired TTL and changed source contact prevent Salla create.
- [ ] Validate `private.option_text_<option id>` against actual Salla text/conditional option requirements; do not pass raw engraving value through model, logs or purpose-bound Chatwoot quote request.
- [ ] Default flags `MEZAN_ASSISTED_SALLA_PRIVATE_FIELDS_ENABLED`, `MEZAN_ASSISTED_SALLA_SIGNED_PREPARE_ENABLED`, `MEZAN_ASSISTED_SALLA_QUOTE_WRITE_ENABLED`, `MEZAN_ASSISTED_SALLA_CONFIRMATION_ENABLED`, `MEZAN_ASSISTED_SALLA_ORDER_POST_ENABLED`, `MEZAN_ASSISTED_SALLA_CHECKOUT_READ_ENABLED` must remain OFF until explicit go-live and full CI.
- [ ] No real customer data, card number, CVV or OTP in CI/github issue comments. Use fully synthetic fixtures.
- [ ] Check source-specific CI for **new encryption tests**; 297 prior PASS tests do NOT certify these files.


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
