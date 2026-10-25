# Paused Snapchat campaign publication

Status: source and seven integration seams authored; tests pending with the coordinator.
This slice creates one SALES campaign, one PIXEL_PURCHASE group, one WEB_VIEW
creative and one REMOTE_WEBPAGE ad. Campaign, group and ad are forced PAUSED.
Creative packaging and review are shown separately. No activation is performed.

## Scope and authority

The owner enters names, headline, HTTPS destination, native-currency daily group
budget, timezone-aware start time, country, reason and conversion window.
Account, product/variant, Pixel and Public Profile are selected from real scoped
catalogs. The existing browser File upload panel supplies an owner/account-bound
READY upload ID; neither manual media IDs nor arbitrary file-fetch URLs are used.
The server verifies current account currency, roles, product/variant membership,
Pixel membership/eligibility, media readiness and profile sharing. USD and SAR
are supported; no exchange conversion or default spending amount is inferred.

The preview persists one immutable plan, owner/account/fingerprint and four
fixed child idempotency keys. One explicit owner token/revision approval covers
the entire displayed plan and the strictly bounded replacement of dependency
references with verified provider IDs. Child approvals are derived server-side
from that parent grant and audited with the actual owner. No client analytical
claim or expected_outcome field supplies authorization.

Parent preview, approval, review, claim, checkpoint, completion, reconciliation
and cancellation events append atomically with their CAS state change. The
bounded history retains the latest 100 events with the actual actor and parent
approval revision. Reviewing the plan preserves prior approval events. Nonces,
confirmation hashes and worker lease tokens never enter the public audit.

## Applied integration seams

The following bounded seams are now applied to the existing management input,
fingerprint, baseline gate, final authorization, router, native panel and CI.
All existing autonomy and media/provenance paths are preserved. The coordinator
must review and test this integrated source before enabling publication:

1. In SnapchatManagementProposalInput add optional publication_id, normalize it
   with the existing identifier validator, and reject it unless the action is
   one of campaign.create, ad_squad.create, creative.create or ad.create.
2. Preserve old request fingerprints: in
   snapchat_management_request_fingerprint remove publication_id from the
   canonical model dictionary only when its value is None. Nonempty references
   must be included in the fingerprint.
3. In create_snapchat_management_proposal, after build_snapchat_operation and
   before the baseline-unavailable refusal, compute:

       from .snapchat_campaign_publication import validate_publication_creation_proof
       publication_creation_proof = await validate_publication_creation_proof(
           db, user_id, payload, operation
       )

   Keep the existing unavailable baseline unchanged. Add only
   "and not publication_creation_proof" to its no-windows/provider-is-None
   refusal. The helper returns an exception for invalid references; it does
   not return a permission bit supplied by the client. Persist publication_id
   and publication_creation_proof in the child proposal row. Ordinary previews,
   activation, budget changes and bid updates retain their existing gates.
4. At the final management authorization callback after resolving the durable
   provider-write token, route publication create requests through:

       from .snapchat_campaign_publication import assert_publication_write_fence
       await assert_publication_write_fence(db, user_id, row, operation)

   This callback compares the exact server-materialized request and operation,
   including first-party tracking URL expansion, rechecks current dependencies
   and PAUSED ancestry, then performs a last parent DB lease/revision fence.
   Do not put an unrelated DB await after that final fence and before POST.
   Nonpublication create paths keep their existing authorization; updates keep
   their autonomy/profit/drift checks.
5. Import attach_snapchat_campaign_publication_routes in
   integrations_control_center/__init__.py and call it with the same router,
   db, current_user and _require_owner used by the management/media routes.
6. Mount the new SnapchatCampaignPublicationPanel from the existing owner panel:

       <SnapchatCampaignPublicationPanel
           ownerId={ownerId}
           accountId={form.accountId || accountId || ""}
           onChanged={onChanged}
       />

   Import it from ./SnapchatCampaignPublicationPanel. The component receives
   the actual signed-in owner ID, never a display label.
7. Add the new exact paths to existing scoped CI/release allowlists. Include
   the new runtime module in the governed backend identity manifest where
   required. The dedicated workflow adds real publication and existing guard
   suites; it does not replace existing required checks.

The unavailable-baseline exception is server-owned proof for new, zero-delivery
entities only. It does not mark decision data complete or relax later delivery,
inventory, contribution-profit, financial-update or activation requirements.

## Runtime and recovery

All publication endpoints are under
/integrations-v2/snapchat_ads/management/publications and require the owner:

- POST /preview: immutable plan and confirmation token; no provider write.
- GET /: latest owner jobs; GET /current?idempotency_key=... recovers the same
  preview; GET /{id} retrieves its state. These reads never start a worker.
- POST /{id}/approve: one plan token/revision approval.
- POST /{id}/execute: starts/resumes the same durable job.
- POST /{id}/reconcile: provider GET-only recovery of the current child.
- POST /{id}/review: explicit review of the same remaining plan, retaining all
  completed IDs and unknown child states while clearing the previous approval.

A bounded detached worker has a unique server claim token and acknowledged
revision fence before any child work starts. The timeout is ten minutes and
the operational lease is fifteen minutes. A claimed-but-unstarted worker can
be recovered after sixty seconds only by CAS of its exact revision/token.
Cancellation or lost acknowledgement during the initial claim conditionally
stabilizes only that worker's token; a cancelled loser cannot stop a winner.
An expired running claim is not permission to retry an uncertain child write.

Each child is looked up by its original fixed key. A completed child surviving
a lost parent checkpoint is adopted through its exact confirmed verification
ID. An approved never-sent child is executed once through existing management.
Attempting/unknown children stop advancement and require reconciliation.
Default Motor naive-UTC BSON timestamps are normalized only for trusted server
fields; owner-entered start_time must retain an explicit timezone.

Parent expiry requires explicit review and approval of remaining work. An
expired child may refresh the SAME proposal/token/revision only if its exact
parent/key/body binding is proven, its status is previewed/approved, its write
state is not_attempted, both reached/uncertain flags are false, and no active
management entity lease exists. Its expiry is capped to the parent grant.
Existing audits/write markers remain. A failed, attempted or uncertain child
never receives a replacement key or a fresh create authorization.

Campaign/group/ad unknown-create handling delegates to existing management
reconciliation. For unknown creative creation without an ID, the new helper
uses two complete fixed-account creative catalogs with a fresh exact-ID GET
between them. It requires one exact server-generated name and all submitted
controls, account/type/time-window identity, and a scan-token CAS. Zero,
duplicates, drift or incomplete listings remain unresolved and never authorize
another create. This identity recovery remains GET-only after approval expiry.
Snap's creative catalog does not establish deleted-aware absence proof.

Final verification rereads the complete chain, proves all three delivery
entities PAUSED with correct relationships, and reports creative/ad review
state separately. A final verification interrupted after all four child IDs
were saved can finish through read-only reconciliation after approval expiry.

## Primary provider references

- https://developers.snap.com/marketing-api/Ads-API/campaigns
- https://developers.snap.com/marketing-api/Ads-API/ad-squads
- https://developers.snap.com/marketing-api/Ads-API/creatives
- https://developers.snap.com/marketing-api/Ads-API/ads
- https://developers.snap.com/marketing-api/Ads-API/api-patterns

The Ads mapping specifies WEB_VIEW creative with REMOTE_WEBPAGE ad. Creative
catalogs are account-scoped, return creatives[].creative, and support bounded
cursor pagination. Do not add an undocumented deleted-catalog option.

## Verification handoff

The author performed source edits only: no processes, tests, Git operations,
provider requests, database writes or deployment. All new checks are unexecuted.

The backend integration test calls real management create/approve/execute with
provider=None, monkeypatching actual provider class methods instead of passing
the injectable provider seam. It forces unavailable baseline data and expects
only the valid parent proof to permit the paused chain; an ordinary create
must still fail. Additional cases cover parent approval/integrity, source and
catalog guards, currency/kill switch, naive BSON dates, lost claim/insert
acknowledgement, cancelled losers, stale unstarted claims, lost completed-child
checkpoints, stable-key refresh, concurrent workers and positive-only creative
reconciliation. Frontend checks cover no default spending, reads-only opening,
explicit execution/review, account reset and reconciliation without new POSTs.

Run the dedicated publication workflow and existing management/media checks
against the seven integrated seams above. A green fake-backed suite alone does
not prove production provider permissions, provider canonical field echo, or
live campaign publication. A separately authorized paused provider rehearsal,
readback and governed deployment verification remain coordinator-owned.

