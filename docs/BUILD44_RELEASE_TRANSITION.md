# Build44 required Backend transition (candidate, no deployment authorization)

Accepted baseline: #1338 cd47a536fba0928d262bae754186203be15f868c;
Android #270 ab79138abe6a42b71493bab7e4ad19db2809fa81, 1.0.29/code44.
Shipping integration source: #1335 f364c2e0c0d2e228d72bbc3520de3c8499374722.
Only its runtime, consumer and test changes are replayed; no Release Intent,
historical evidence copies or changes to the original branches.

## Staff transition before Backend activation

1. Keep existing Android installed. Identify staff who approve reviews; give
   them the existing, correctly scoped Web order-review permission through
   the normal owner process. Do not grant extra permissions automatically.
2. In an authorized isolated account, verify each affected role can sign in to
   the current Web /order-review page, display the review and submit its signed
   approval. Without this check, hold Backend activation; a written fallback
   alone does not demonstrate staff can use it.
3. Announce https://mezansalla.com/order-review as the temporary review path
   before activation. Old Android review POST has no displayed approval token;
   it receives 409, upgrade/Web guidance and retry_post=false, with no mutation.
   Do not mint a token on its behalf or retry the old POST.
4. Publish the separately approved Backend first, verify runtime identity and
   Web operation, then distribute the separately approved Build44. Existing
   non-review functions retain their routes. Retire the temporary Web path
   only after staff successfully use Build44 V2 review/readback.

## Shipping operation with the existing safe reconciliation design

- Ready confirms physical/virtual local preparation only when canonical Salla
  evidence is in_progress. It atomically records the delivery operation; it
  does not imply that a remote status update or AWB creation has succeeded.
- If Salla is not confirmed completed or has no provable current AWB, leave
  requires_attention. Staff inspect Salla and complete/create there only after
  checking existing shipment evidence; an unknown prior operation is never
  permission to create a second shipment.
- After Salla completion, existing Android/Web carrier-label refresh performs
  GET-only reconciliation. SMSA/iMile require current identity, printable status
  and verified PDF; store courier retains its current local document policy.
  Terminal/unknown statuses and carrier/shipment changes reject printing.
- No automatic external status/AWB POST is re-enabled. Local CAS/lease and
  status writers fence publication, not Salla's future state. A GET cannot
  prevent a subsequent remote change or revoke a PDF already downloaded.

## Verification and rollback boundaries

Retain baseline CI evidence for unchanged invoice/supplier/review code. Run
affected integration contracts because shared canonical writers and Ready
completion changed; execute the real PDF worker on Linux with isolated Mongo
and synthetic provider/document transports. No real provider calls.

Production activation still needs actual ASGI worker/replica capacity and
proxy/CDN/APM token-redaction evidence from the hosting gate, plus authorized
Samsung/printer and staff-Web checks. These are not proven by CI.

Before rollback, stop new supplier/shipping attempts and reconcile pending or
unknown ones by reading. Preserve attempt, idempotency and lease records. Do
not roll back to a writer that ignores active leases or automatically replays
external POST. Retain the staff Web review option during Android migration.

No Merge, Deploy, Restart, Production writes, APK/EAS Build or OTA authorized
by this document. The final PR evidence records tested HEAD/TREE and results.
