# Snapchat Public Profile scope diagnostic — 2026-10-27

Base: `5c3353034be910ab2bde62c12512d1d8ded26faa` (production #1581).
Branch: `codex/snap-profile-scope-diagnostic-20261027`. WIP source only; no
callback, credential, authorization scope, provider, advertising, budget,
deployment, or production database mutation by this task.

The Public Profile sharing-policy request returned HTTP 403 in prior live
acceptance. `public_profile_sharing_read` is the application's internal
capability label, not evidence that a provider OAuth scope of that name exists.
The 403's cause remains unproven; application access, token grant, linked
profile role, and sharing policy are distinct possibilities. Do not claim that
adding any single scope resolves the error. No new provider grant was attempted.
Snap's [Public Profile getting-started documentation](https://developers.snap.com/marketing-api/Public-Profile-API/GetStarted)
states that this API requires application allowlisting; it does not establish
which condition caused this specific sharing-policy rejection. Its
[Profile Asset Management reference](https://developers.snap.com/marketing-api/Public-Profile-API/ProfileAssetManagement)
documents the account sharing-policies endpoint used by the integration.

Current projection stores actual scope supplied by the token response and,
when absent, the requested-scope fallback in the same `scope` and permission
rows without provenance. Both cases are experimentally identical after
persistence. Existing records therefore cannot establish which scopes Snapchat
granted. The display-only correction reports Snapchat grants as unverified and
explains that requested scopes do not prove consent; it preserves the current
capability matrix, actions, connection state, and authorization behavior. It
does not modify stored credentials, OAuth callbacks, token refresh, or grants.
The OAuth-start response already describes the scopes requested for that flow.

Local backend verification: 55 tests passed across scope diagnostic, Snapchat
OAuth integration, and control-center regression suites; 91 media-upload tests
also passed. The new regression
failed before the display correction. Frontend dependencies are unavailable in
this scratch checkout; the two UI rendering assertions and production frontend
build must pass the triggered Snapchat Integrations V2 CI before integration.
First CI on `505a518b` had 71 passed / 1 failed: the test captured an old
`_actions` function before another test installed Snapchat's wrapper. The
regression now resolves that function dynamically. Running the exact nine-file
CI backend command with the same fixture JWT setting passed all 72 cases
locally; fresh remote CI and frontend checks are still required.
No B intent or deployment was attempted. A future separate scope-provenance
contract would need explicit provider-confirmed evidence and safe migration;
until then legacy scope values remain unverified.
