# Mezan ↔ LightNode one-way WhatsApp buyer proof — source-only (2026-11-28)

## Live diagnostic / reason for change
- Owner: LightNode loopback Chatwoot /app/login returns 200.
- LightNode to https://mezansalla.com/ returns 200 with curl TLS verify=0.
- Emergent /app cannot resolve fkktxuxd.taila84686.ts.net (gaierror);
  the Tailscale-host URL is not an externally reachable Chatwoot endpoint.
- support.mezansalla.com returned 404 for attempted Chatwoot pages; never
  send the Chatwoot bot token to that unverified host.
- Owner's local .env token and host writes are not evidence of live/persistent
  Emergent Secrets, nor proof of token validity.
- No signed Mezan API live round-trip or live buyer identity check completed.

## Transport and ownership
The pinned CE AgentBot runs beside Chatwoot on LightNode and reads the real
bot-owned latest inbound WhatsApp message and ContactInbox source identity
through local loopback, not from a model prompt, webhook phone, or
submitted order reference. It performs two native snapshot reads surrounding
the current-buyer overlay read to fail closed on changed sender, contact, latest
message, staff takeover, muted/blocked state and mismatched inbox.

The CSA service computes a domain-separated HMAC proof using the existing
private service key (min 32 bytes):

HMAC_SHA256(key, "amasi.wa.buyer.v1\n1\n2\n<conversation>\n<message>\n<issued_at>\n<canonical_9665_mobile>")

It submits a different signed request to
POST /api/internal/whatsapp/order-stage/attested-read (fixed
https://mezansalla.com, no redirects) with ONLY:
proof_version, account_id, inbox_id, conversation_id, message_id,
attested_at, buyer_proof, order_number.
The raw buyer phone is never in the HTTP request. Request HMAC binds
method, exact path, timestamp, random nonce, key ID, SHA256(raw body);
Mezan's durable shared Mongo nonce reservation rejects replays, and the
attested proof is valid for at most 60 seconds. The fixed account/inbox is
1/2 and Mezan's tenant/owner is server-side configuration only.

Mezan derives the canonical Saudi customer mobile from the exact
tenant-scoped order and independently recalculates the buyer HMAC proof,
then uses the existing freshness + minimal safe-stage projection. Unknown
order, mismatched buyer, stale stage, unexpected JSON keys and bad proof
are denied uniformly. Never return full order/customer history.

## Rollout policy
- Only AMASI_WHATSAPP_ORDER_STAGE_ATTESTED_ENABLED=1 mounts the new route.
  Default is OFF; legacy reverse-Chatwoot stage and attested stage CANNOT
  activate together. Catalog remains independently gated.
- The current bot token and tentative AMASI_CHATWOOT_HOST are not required
  by this new Mezan endpoint; neither is a fallback or public exposure.
- CSA's order_stage_enabled must stay OFF until pinned CE overlay and
  local verified-buyer capability are deployed and tested in the owner-test
  inbox only. Never mount a general LLM tool that can supply the buyer phone.
- Source-only draft changes do not authorize changing production secrets,
  runtime flags or service restart, GitHub merge, Emergent publish, Salla
  order creation, WhatsApp send, or customer data access.

## Mandatory acceptance before production
1. Exact-head offline/CI tests in BOTH repositories; independently verify
   signed wire compatibility, HMAC raw-body hash, strict account/inbox, stale
   or swapped message, wrong buyer, replay, human takeover, malformed data.
2. Confirm live Emergent secret names, validity, HMAC-key equivalence by
   constant-time private challenge, tenant ownership and process loading,
   WITHOUT printing token/key/hash to logs or chats; avoid logging even
   digests that could facilitate offline comparisons.
3. Verify actual pinned Chatwoot CE local overlay endpoint from authorized
   host without exporting customer data; owner-test-only identity/UAT.
4. Keep HMAC key in root-only/safe systemd credentials on LightNode and
   managed persistent Secrets in Emergent; do not rely on .env surviving
   re-publish. Do not use support.mezansalla.com as Chatwoot target.
5. Only guarded Production V5 release with source A, intent B, clean build,
   approvals and 3x verify. Rollback must preserve accounting, Chatwoot
   idempotency, uncertain send #87, and prior #240 no replay.
