# Qoyod product endpoint recovery — 2026-09-18

## Production evidence

- PR1078 deployment completed at 2026-09-18 15:50 platform time and Release
  Guard v5 verified the intended runtime three times.
- The refreshed Qoyod exceptions UI now exposes the persisted latest failure.
  Non-COD order `286150959` failed during `GET /products` with HTTP 404 at
  `2026-09-17T20:42:05.458000+00:00`.
- Production is configured with the legacy Qoyod base while Qoyod's current
  public API documentation identifies `https://api.qoyod.com/2.0` as the
  stable v2 API and lists Products as a readable resource.

## Repair

Both Qoyod HTTP clients now handle only this narrow migration case:

1. Call the configured legacy product-list endpoint.
2. If and only if that read returns HTTP 404 from a recognized legacy Qoyod
   host, repeat the same GET against the canonical v2 host.
3. Promote the client instance to the canonical host only after a successful
   2xx product read, keeping all following product, invoice and payment calls
   on the same origin.
4. Surface canonical authentication, network, throttling and provider errors
   unchanged. None of them can become an assumed product absence or authorize
   a product/invoice write.

No automatic retry, invoice creation or Qoyod financial write is part of this
source change.

## Verification and rollout

- Focused regression suite: 73 passed.
- Python compilation and `git diff --check`: passed.
- Deploy through Release Guard v5 only after source and intent CI succeed.
- After deployment, retry exactly one previously validated non-COD order and
  inspect its newly persisted endpoint/result before allowing the automatic
  worker to drain eligible non-COD orders.
- Cash-on-delivery orders remain deferred. Their separate 4.99/5.00 SAR fee
  difference must not be bypassed or included in this recovery.
