# Snapchat media create recovery

This follow-up is separate from the first media upload release in PR #1492.
It adds a read-only provider recovery path for an accepted media-create request
whose response was lost. It does not create another media entity.

## Provider contract checked on 2026-10-25

Primary references:

- [Snap Media API](https://developers.snap.com/marketing-api/Ads-API/media),
  “Fetch all Media in an Ad Account” and “Get a Specific Media”. The documented
  account endpoint is `GET /v1/adaccounts/{ad_account_id}/media`; each result is
  wrapped as `media[].media` with a successful sub-request status. The examples
  include account, name, type, creation time and `PENDING_UPLOAD` or `READY`.
- [Snap API Patterns](https://developers.snap.com/marketing-api/Ads-API/api-patterns),
  “Pagination”. Media supports pagination through `paging.next_link`, with a
  limit between 50 and 1000.

No arbitrary URL, name-search endpoint, idempotency key, media deletion flag
guarantee, or server-side SHA-256 verification is assumed.

The current [Snap Media API video upload specification](https://developers.snap.com/marketing-api/Ads-API/media#upload-media---video)
sets Top Snap video to 1080 × 1920 pixels, a 32 MB normal-upload limit, and
duration from 3 through 1800 seconds. Optional provider duration metadata is
checked against that inclusive range; 180, 181 and 1800 seconds are accepted,
while 1801 seconds and nonfinite values are rejected. This does not change the
existing byte limit or claim that the application locally decodes the video.

## Recovery behavior

The existing owner-only reconcile route handles `create_unknown`. After fresh
selected-account and creative-role checks, it validates the persisted immutable
preview fingerprint, owner/account/content-derived upload ID, server-generated
provider name, verified source flag, and absence of an attempted file upload.

The reader requests the entire account catalog with limit 1000. It accepts at
most 10 pages and 10,000 rows, within a 30-second recovery deadline. It requires
successful top-level and per-row responses, valid rows in the requested account,
unique media IDs, and a terminal page. A repeated cursor/page, page or row bound,
malformed response, unread continuation, or partial response prevents adoption.
Continuation URLs must remain HTTPS on the exact Snap Ads API host and exact
account media path, with only cursor and optional limit parameters.

All exact server-generated-name matches are considered together. Zero matches
leave `create_unknown` with `snapchat_media_create_unresolved`; multiple matches
leave it with `snapchat_media_create_ambiguous`. Neither result selects the first
or newest item, grants permission to create again, or enables file upload.

A sole candidate must be the expected account's video in `PENDING_UPLOAD`, with
the expected name and AI declaration. Explicit deletion, hidden visibility,
existing file metadata, absent creation time, or an implausible creation time
blocks recovery. The accepted clock allowance is five minutes before the
persisted create-attempt time and five minutes into the future. Recovery then
performs a fresh exact-ID GET, checks the same conditions again, and rechecks
the account and role before a revision/claim-fenced local compare-and-set.

Successful adoption persists the provider ID and bounded reconciliation proof,
changes the job to `awaiting_file`, clears the old confirmation token and expiry,
and keeps both provider-upload-attempted and readback-verified false. It does
not mark the media `READY` and issues no provider POST/PATCH/DELETE request.
The existing stale-claim recovery can enter this path; a resumed old worker or
another reconciler cannot overwrite the winning revision.

## Explicit upload after recovery

The owner must renew the existing preview and explicitly submit the same file.
The normal raw-body upload checks exact size, actual SHA-256, container marker,
selected account, current creative role, and the mutations switch. It performs
a fresh GET of the adopted ID and rechecks the pending candidate proof before
the multipart file POST. The stored original create-attempt time remains the
timestamp reference after the new file attempt begins. A previously uploaded
or `READY` candidate never authorizes this POST. The ordinary fresh provider
readback is still required before the creative form can receive `READY` media.

The mutations switch may be off during GET-only recovery. It still blocks the
subsequent explicit provider file write. Recovery never invents metadata or
claims that optional provider fields prove local video duration or dimensions.

## Remaining operational limits

Catalog pagination is not documented as an atomic snapshot. Changes that
produce malformed, duplicate or contradictory results fail closed, and the
fresh exact-ID check narrows the window; this is not provider idempotency.
Eventual catalog visibility can leave a job unresolved until a later explicit
reconcile. Accounts exceeding the bounded catalog budget remain blocked.
Snap's response must expose enough evidence for the candidate checks; absence
of required identity or creation-time evidence remains a concrete blocker.
An entity with evidence of an unacknowledged file upload stays blocked because
its content cannot be bound safely to the source from catalog metadata alone.

## Verification handoff

Source authoring only: no local processes, tests, provider requests, database
writes, Git actions, or deployment were performed for this follow-up by its
author. The coordinator must run the focused existing media test file and
review the exact integrated diff before publication.

Added deterministic cases cover lost-create recovery followed by exactly one
file upload; old confirmation rejection; actual-file digest mismatch; no match;
duplicate names; partial or incomplete catalogs; wrong account; uploaded,
hidden or `READY` candidates; fresh-GET changes; immutable-intent corruption;
role/account revocation; mutations-switch enforcement; owner isolation; stale
worker fencing; concurrent adoption; and fixed-path bounded pagination.

Only these files belong to this increment:

- `backend/integrations_control_center/snapchat_media_upload.py`
- `backend/tests/test_snapchat_media_upload.py`
- `docs/operations/SNAP-COMPLETION-20261025/MEDIA-RECOVERY.md`

The coordinator must add this new documentation path to any exact release/CI
allowlist when integrating. Existing workflow and frontend files are untouched.
