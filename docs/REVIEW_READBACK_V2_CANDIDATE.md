# Isolated Review V2 candidate

Base: Production GitHub `20191e31116bfa9921057ba907fd6df9b62c023b`, tree
`8bf4cac944f4cddd78934ad5e014a7cfe2ab122a`. This candidate selectively includes
the displayed-approval contract of #1330 (`4616ed11557c85daee0d49613c4fd6e654720f70`).
It does not update #1330, Android #268/#269, Production, inventory policy, or accounting.
The immutable candidate HEAD/TREE are recorded by CI and its artifact manifest.

## Wire contract

- `GET /api/order-reviews-v1/{order}?local_only=true&readback_contract_version=2`
  returns a consistent displayed snapshot, `approval_token`, fingerprint and
  `approval_context`. Context binds contract version, merchant, employee, order,
  workflow revision, and opaque server login family/epoch. Provisional MFA/OTP
  tokens and legacy sidless tokens cannot authorize V2.
- `POST /api/order-reviews-v1/{order}/complete`: only `expected_revision`, signed
  `approval_token`, `readback_contract_version=2`, UUIDv4 `client_request_id`.
  No new quantities, product facts, legacy reapproval inputs or client identity
  are accepted. The Android receipt is stored and read back before its one-shot
  native transport can send. The server's operation ID remains distinct.
- `GET /api/order-reviews-v1/{order}/completion-operation?readback_contract_version=2&client_request_id={uuid}`
  resolves that merchant/employee/order/attempt only, using one primary snapshot.
  It never substitutes the latest operation. A newly authenticated session of
  the same authorized employee may read history without the old approval token.
- Completed proof includes the complete attempt binding, token SHA256,
  `operation_id`, `committed_revision`, `event_id`, `event_type`, `completed_at`,
  `commit_confirmed=true`, local completion mode and `salla_status_sync=not_requested`.
  The actual operation and commit event must agree. Current workflow revision and
  operation pointer are separate fields, including every completed fast return.
- Absent/nonterminal attempt: HTTP202, `state=unknown`, `retry_post=false`,
  `reconcile=get_only`. Driver/commit/readback uncertainty: HTTP503 with the same
  no-replay contract. Known execution rejection remains HTTP409; it is not a
  durable assertion that no other in-flight execution can commit.
- HTTP200 alone is never client success. Revision, fingerprint, original family,
  actor, order, request ID, commit event and current workflow must match. Historical
  proof is displayed as history; it cannot become current consent.

## Session and concurrency boundary

One bounded `order_review_auth_sessions_v2` row is minted only after completed
authentication. Both token types and both refresh paths preserve that family.
Completion conditionally increments its active, unexpired row inside the same
owner transaction; acknowledged logout revokes that exact row with majority+journal
write concern. They therefore serialize, rather than relying on a prior read.
The operational capability cannot create/revoke/upsert or target another session.

If completion commits first, later logout cannot undo it. If confirmed revocation
wins, an older snapshot cannot commit. An undelivered offline/local logout is NOT
server revocation. Android's accepted Cold Start policy still prevents reuse on
restart; it cannot retroactively cancel an already delivered server request.
Legacy V1 tokens do not gain fictitious session guarantees.

Native merchant bridging retains the verified employee family and actor. It does
not turn an employee's consent into owner consent. Current linked-owner lookup and
page permissions continue to apply, including historical readback. A delayed old
logout cannot revoke a newer family; new-family logout deliberately emits no cookie
deletion that could erase newer browser login cookies. Revoked cookies are unusable.

## Catalog and first use

The existing acceptance fence now includes legacy `products`, `salla_products`,
`mezan_products_v2` identity, SKU, barcode, parent and variant identities. Numeric
and positional array updates are normalized. Existing operation profiles, resource
bindings, option bindings, resources and lifecycle settings remain fenced. The
server passes the same guarded DB to Sync/import/intake; the legacy intake update
is explicitly owner-scoped. Nonmaterial name/image/cost-only updates preserve the
acceptance version. No writer is disabled and no external API is used by tests.

The fence namespace, session authority and request-ID unique index are initialized
before readiness in both global and per-process startup. Failure prevents readiness.
The synthetic first-use negative control reproduces Mongo commit112 when setup is
deliberately omitted; the initialized path rejects stale data without unexplained500.
Driver uncertainty does not trigger another POST. Internal transaction retry remains
bounded by the existing owner implementation; it is not a new HTTP submission.

## Acceptance and rollout

The candidate-only Linux workflow exercises real Mongo8.0.12 PRIMARY, current source,
synthetic fixtures, no provider credentials, and an explicit Python socket boundary.
It is not an OS-wide sandbox. Evidence upload and exact Artifact-ID download, archive
SHA256, file hashes, HEAD/TREE and run/attempt checks are mandatory. FAIL/ERROR/SKIP
cannot pass. The Android candidate retains all43 accepted gates and adds V2/native
tests; Ready/Auth/Cold Start/Scanner/supplier source remain protected.

Web retains #1330's displayed-token V1 contract; it is not represented as a V2 client.
The new Android candidate requires this V2 Backend and its new review-only native
module. Old binaries without `approval_token` fail closed on approval; they cannot
be claimed fully compatible merely because login/read endpoints work. Deployment
requires a separately approved review rollout/freeze or an approved token-capable
client transition. No downgrade that bypasses displayed consent is included.

Unobserved POST is deliberately UNKNOWN, potentially indefinitely. No general attempt
journal, deadline-based inference, automatic receipt eviction or new POST is added.
Android's bounded500 receipts need explicit operational handling if exhausted. A
future reopened-order/resolved-rejection retry policy requires separate review.

Build blockers remain independent: Supplier Backend #1329 is unmerged; isolated
Backend/DB and test accounts need commissioning; Trusted EAS is NOT_COMMISSIONED;
signing/build approval and physical Samsung UAT are outstanding. No APK/EAS/OTA is
authorized by source-test success.

Rollback before release is abandoning these independent candidates. After a separately
approved rollout, reverting Backend while V2 clients are active is unsafe: freeze
review approvals first and retain operation/event/session evidence. Never replay
UNKNOWN receipts, roll back business commits, or delete evidence to make rollback easy.
