# Direct ACTIVE Snapchat campaign publication

Status: source and integration authored; fresh verification is coordinator-owned.
This slice creates one SALES campaign, one PIXEL_PURCHASE group, one WEB_VIEW
creative and one REMOTE_WEBPAGE ad. Campaign, group and ad are created ACTIVE
directly. There is no create-PAUSED-then-activate sequence. General nonpublication
creates retain their previous PAUSED behavior.

Configured ACTIVE is distinct from Snap's review, packaging and actual delivery
states. The campaign request also uses creation_state=PUBLISHED, as documented
for campaign creation. An ad may remain pending review after ACTIVE is saved.
The UI reports those provider outcomes separately and never promises immediate
delivery.

## Explicit owner authority

The owner enters account-native daily group budget, start time, country, names,
headline, HTTPS destination and reason. There is no default spending amount or
automatic currency conversion. Account, product/variant, Pixel and Public Profile
come from real owner/account-scoped catalogs. The existing browser File uploader
supplies a verified READY upload ID; manually invented media IDs and remote file
fetch URLs are not accepted.

The immutable server plan includes the ACTIVE publication contract and requested
status, exact account/currency, product/variant, media/profile, content, targeting,
budget and schedule. One explicit token/revision approval covers that complete
displayed plan and bounded substitution of verified dependency IDs. Older plans
without this ACTIVE contract do not silently gain ACTIVE authority.

A fresh server-owned proof checks current selected account/currency, roles,
product/variant membership, media/profile dependencies, Pixel membership and
eligibility, and the existing repository inventory predicate:

    inventory_verification_status == "verified"
    inventory_delivery_blocked is not True

The proof uses management._capture_proposal_baseline with the selected product
and variant. Missing inventory proof blocks the launch. A brand-new campaign
may lack historic performance windows; those windows and measured cost/profit
coverage stay explicitly unavailable when absent. They are not replaced with
invented history, zero cost or fabricated profitability. The owner's exact
approved initial budget supplies launch authority. Ordinary financial updates,
autonomy execution, drift checks and later delivery decisions keep their
existing protections.

Both native mutation and activation flags must permit the write. The flags,
inventory, dependencies and invoking worker claim are rechecked at the final
boundary after awaited credential resolution.

Before an ACTIVE ad POST, the final provider-read sequence additionally rereads
the exact already-created creative against the approved parent content, after
the inventory/dependency awaits. It requires the bound completed child, verified
creative ID/account and every approved creative control, including headline,
media and signed destination URL. Only then may the final parent claim/revision
fence and flag checks permit transport. Content drift during the ad's token
await is blocked before the ad POST, rather than discovered after publication.

## Native integration

The bounded integration surfaces are:

1. SnapchatManagementProposalInput accepts an identifier publication_id only
   for create actions. It is an untrusted reference, not authorization.
   ACTIVE input requires the reference and activation acknowledgement.
2. The request fingerprint includes nonempty publication_id. A missing/None
   value is omitted so legacy idempotency digests remain unchanged.
3. create_snapchat_management_proposal calls
   validate_publication_creation_proof before building a publication operation.
   Only its successful server proof enables the server-only publication_active
   builder keyword. The default native builder still creates PAUSED even when
   a caller supplies a reference. The proof and reference are stored in the
   child row; bounded safe proof is available to audit/read APIs.
4. The ordinary missing-baseline refusal is bypassed only by that current
   approved publication proof and its verified inventory. Existing baseline
   content remains unchanged. Client expected_outcome data is never proof.
5. execute_snapchat_management_proposal carries a server-only
   publication_worker_claim from run_publication through the native executor
   and its after-token callback. Native HTTP execute cannot accept or invent
   this context. assert_publication_write_fence requires the exact invoking
   claim, current approved plan, step, child and body; it rechecks dependencies
   and inventory, then performs the last parent DB fence and both flag reads.
   No unrelated DB await is interposed before provider transport.
6. Stored operation integrity and unknown-create matching recognize ACTIVE
   only for this exact server-proof contract. Existing nonpublication create,
   update, autonomy and activation paths retain their original authorization.
7. The router composes attach_snapchat_campaign_publication_routes with the
   existing owner boundary. SnapchatCampaignManagementPanel mounts the actual
   publication form using the authenticated owner ID and native selected account.
   Existing settings CI allowlists the exact paths and runs focused regressions.

All three delivery bodies contain ACTIVE directly; campaign creation also
contains PUBLISHED. No automatic cleanup, delete, pause or follow-up activation
write is implied by this feature.

## Durable child identity and tracking

The parent persists four fixed child keys, child proposal IDs and verified
provider IDs. Before any child adoption, refresh, reconciliation or use, the
server verifies exact owner, account, publication ID, action, fixed key, request
fingerprint, creation proof/step/plan and any already bound proposal ID. It
compares the stored operation against parent-derived approved semantics, not
against a child's self-reported intent. A pre-existing completed creative under
a predictable key cannot supply different media or content.

Creative tracking URLs contain a signed token with an issuance timestamp.
Re-minting this token during validation would reject a valid delayed operation.
The validator instead verifies the existing signature and exact owner/account/
product/proposal-link/host claims, then reconstructs every approved destination,
query and Snap macro field. Every other creative field is derived from the
parent. Final readback uses those same expected parent semantics.

The worker passes its immutable claim through executor context because merely
loading whichever parent claim is active is insufficient. An old worker cannot
gain authority from another worker's claim, even when the same plan is approved
again. Parent claim/nonce material is never included in public proof or audit.

## Restart, expiry and audit

The job is durable before a detached bounded worker starts. A unique claim and
acknowledged CAS revision fence child work. Worker timeout is ten minutes;
operational lease is fifteen minutes. A claimed-but-unstarted job may recover
after sixty seconds only through an exact revision/token CAS. Cancellation or
lost acknowledgement during the initial claim conditionally stabilizes that
worker's token and cannot stop a different winner.

Stable keys recover a preview created before its parent checkpoint. Completed
children survive a lost checkpoint and are adopted only after strict binding
and confirmed verification. Approved never-sent children execute through the
same native proposal. Attempting/unknown children stop advancement; a lease
expiry never permits blind replay.

Parent expiry requires explicit review and reapproval of remaining work while
preserving completed IDs and unknown markers. The SAME expired child proposal
may refresh only when exact parent/body/key binding and trusted never-sent
state are proven: previewed/approved, not_attempted, reached=false,
uncertain=false, and no active native entity lease. Its new expiry is capped
to the parent. A sent/uncertain child receives no replacement key.

Parent events append atomically with CAS changes, retaining the latest 100
events with actual actor and approval revision. Review preserves earlier
approval events. Confirmation values and worker claims do not enter public
history. Default Motor naive-UTC BSON dates are normalized only for trusted
server fields; owner-entered start_time still requires an explicit timezone.

## Partial and unknown provider outcomes

Known created ACTIVE parents remain visible with their IDs and configured
status. If ad creation was requested and the response is lost, the UI states
that the ad may be active and spending may have begun. The orchestrator does
not send another create, infer that nothing happened, or claim the chain is
safely paused.

Campaign/group/ad reconciliation reuses native complete bounded catalogs,
expected ACTIVE status, exact submitted controls, relationship/time evidence,
fresh readback and native fences. Creative no-ID recovery uses two complete
fixed-account catalogs around a fresh exact-ID GET, exact approved fields and
a scan-token CAS. Zero matches, duplicates, malformed/incomplete listings or
drift stay blocked and never authorize another create. These identification
reads can resolve proven results after owner approval expiry without granting
new write authority.

After all four IDs are confirmed, fresh readback validates ACTIVE configuration
and relationships. The response uses configured_delivery_status separately from
provider_delivery_status and creative/ad review and packaging fields. A final
readback interrupted after all IDs were saved can finish through GET-only
reconciliation without another provider write.

## Routes and UI

Owner-only routes are under
/integrations-v2/snapchat_ads/management/publications:

- POST /preview and POST /{id}/approve prepare and approve the exact plan.
- POST /{id}/execute starts/resumes the existing job.
- POST /{id}/review explicitly renews review of the remaining immutable plan.
- POST /{id}/reconcile performs provider GET-only recovery.
- GET /, GET /current?idempotency_key=... and GET /{id} read durable state and
  never start a worker or automatically POST.

The Arabic form states direct ACTIVE creation and possible spending, shows all
names/content/budget/account/Pixel/profile/media fields, and has explicit review,
approval and execution controls. Owner/account changes clear stale selection
and upload state. An unresolved ad outcome disables execution and exposes
read-only reconciliation.

## Provider references and verification

Primary API contracts checked on 2026-10-25:

- https://developers.snap.com/marketing-api/Ads-API/quick-start
- https://developers.snap.com/marketing-api/Ads-API/campaigns
- https://developers.snap.com/marketing-api/Ads-API/ad-squads
- https://developers.snap.com/marketing-api/Ads-API/creatives
- https://developers.snap.com/marketing-api/Ads-API/ads
- https://developers.snap.com/marketing-api/Ads-API/api-patterns

Quick Start shows direct ACTIVE creation; campaign reference examples include
PUBLISHED. WEB_VIEW maps to REMOTE_WEBPAGE. Snap review/delivery is not guaranteed
by configured ACTIVE. Creative catalogs do not establish deleted-aware absence
proof. The current Media specification table supports Top Snap 3..1800 seconds;
this publication slice preserves that bound and existing raw upload controls.

The author used source editing only; no local process/test, Git operation,
provider/database write or deployment was performed. Coordinator-run focused
tests, actual HTTPX MockTransport token-after-await checks and isolated Mongo7
approval/launch/lost-checkpoint tests are required before publication. The
dedicated workflow requires the Mongo cases and includes existing native write
guard regressions. All verification results must reference the exact integrated
source checkpoint. No test is authorized to send live advertising/budget writes.

The transport regression mutates provider headline, media or destination during
the actual ad credential await. It exercises public orchestration and the real
native provider HTTP methods through MockTransport, expects only the first
three creates and zero ad POSTs, and checks that an explicit resume cannot
recreate or publish the changed creative. The valid-content four-create path
remains the positive control. These tests can be copied onto the earlier source
to demonstrate that the original defect reaches the ad POST before readback
detects the content change.
