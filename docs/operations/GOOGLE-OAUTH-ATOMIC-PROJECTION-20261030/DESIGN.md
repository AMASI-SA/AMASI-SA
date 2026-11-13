# Atomic Google OAuth publication — implementation contract

This is a design/WIP checkpoint, not a deployable fix or evidence of restored connection. Base is production fb12300c353cb1875f4462dc279b25360913af5c, tree58b54046079e4c4891af2c54e3eeffa765dfacd1. Last live coordinator evidence is generic Google HTTP429; no OAuth-expiry or account-not-owned conclusion.

## Scope and writer audit

Approved existing files: google_projection.py, google_connections.py, google_discovery.py, google_oauth_security.py, google_merchant_registration.py. Root additionally approves only a Google-specific early refusal in service.py::_persist_test_snapshot. Existing Ads/GA4/Merchant credential refresh CAS and reporting/status separation remain unchanged. google_ads_reporting.py belongs to another agent.

New modules/tests/workflow will implement the shared publication boundary and test it against a disposable Mongo8.0.26 replica. Exact module paths: backend/integrations_control_center/google_oauth_publication.py; backend/tests/test_google_oauth_publication.py; backend/tests/test_google_oauth_publication_mongo.py; backend/tests/test_google_oauth_discovery.py; .github/workflows/google-oauth-atomic-publication.yml.

Current callback is the sole _persist_google_projection caller. Current discovery silently truncates GA4/Merchant at first page and skips invalid rows; current Merchant registration independently sets connected and replaces accounts. Generic service local-test persistence can bypass authority, although composed exact Google routes currently precede it. No Google disconnect endpoint exists; current general disconnect action is explicitly disabled. Existing management/creation policy revoke endpoints remain independent of OAuth and untouched.

The saved e377f6 OAuth design/generation WIP and f5488525 operation journal were read. Their contract is useful, but neither runtime primitive is wired or accepted as a complete solution. Current production already contains the separately accepted exact-snapshot credential refresh CAS, so that older alternative will not be copied.

## Required publication invariant

A successful callback publishes encrypted credential, Google grant generation, provider membership generation, complete account sets, permissions and connected state in one required Mongo transaction. All authority writes use one per-owner durable operation fence. No provider request belongs inside a transaction. No unsupported-transaction fallback, implicit retry, partial connected result, or restoring authority from a reporting/local diagnostic success.

Capture the owner auth/source baseline at OAuth start, before exchange/discovery. Bind nonce, expiry and browser state to that baseline. Compare exact BSON semantics, including absent/null/type and document identity, against a fresh transaction snapshot before provider work and before publication. Concurrent auth/password disable, reauth, refresh, membership mutation or connection revocation must invalidate the older operation. Never inherit an old subject's refresh token: absent new refresh token permits reuse only for the same verified nonempty subject and unchanged source credential.

A private durable per-owner operation marker is claimed once before exchange. Unknown claim acknowledgement gives no permission to exchange. The publication transaction changes that marker to its exact committed generation together with all authority rows. Unknown commit acknowledgement retains uncertainty and refuses replay/new overwrite; read-only resolution may report an exact committed marker but must not repeat exchange or publication. Cancellation/timeout must retain original driver task to completion and its fence; bounded admission prevents unbounded new work. Transactions are snapshot/PRIMARY with majority+journaled commit. Actual command monitoring will prove one raw commit command; PyMongo commit_transaction automatically retries regardless of retryWrites, so the existing reviewed raw commit/retained-driver pattern is the starting contract, not an unexamined session context manager.

All repository writers of membership authority participate: callback and Merchant projection. Generic local test must refuse Google authority persistence before writing. Metadata-only report writes remain nonauthorizing. External database writers and provider revocations are not serialized by this local contract; do not claim otherwise.

## Complete discovery contract

Fixed existing Google endpoints and granted scopes only, no new scopes or arbitrary URLs. Bound response bytes, total rows, pages and elapsed time; reject redirects, malformed/all unidentified rows, duplicate native identities, invalid resource paths, cycles, unexpected tokens and exhausted bounds. GA4 account/property hierarchy, Search Console exact site IDs/permission levels, Merchant account resources and directly accessible Ads customer resources remain distinct. Complete traversal proves this endpoint's bounded enumeration, not a continuous provider snapshot or all manager-account descendants.

Do not treat missing/invalid repeated fields as proof of a complete empty set without a documented protobuf JSON omission contract. Preserve exact product/service meanings: discovered Ads account does not establish native campaign-write permission, currency or timezone. Validate userinfo subject and granted scope evidence.

For a granted service with incomplete/failed discovery, refuse the entire shared-credential publication and retain the previous coherent generation. Explicitly omitted scope is a revocation for that service only when the whole successful new-grant transaction commits. A complete empty list is separate from failed discovery. Accounts retained under the same proven subject preserve explicit local selection and unrelated settings; no implicit selection or cross-subject adoption.

Merchant developer registration must bind its current credential and membership baseline before provider work and publish its fully enumerated result through the same transaction/fence. Provider registration itself is not replayed on ambiguous result. It cannot mark connected from an error/partial account list or overwrite a later OAuth callback.

## Management reader boundary: additional approval needed

google_ads_management.py::account_client currently independently reads account, integration, token and then credential epoch. An atomic writer alone cannot stop those separate reads spanning two complete generations. Proposed narrow seam: a new private bound account-read helper obtains a consistent local snapshot, validates matching grant/account-set generation for migrated rows, fences the exact credential returned by existing _credential, and rechecks the local snapshot before returning client/approval epoch. No reporting.py change and no weakening of legacy checks. Exact additional hunk must be approved by root before editing. Legacy row handling must be explicit; no automatic reporting-based generation migration. This publication slice cannot claim full management coherence before that seam is proved.

This does not make Google HTTP atomic with local authorization or complete all dispatch-time revocation/first-funded-launch work. No advertising/budget writes or operational flags are introduced.

## Acceptance matrix

- Complete all-service callback publishes one generation; readers never observe delete-then-insert intermediate state.
- Abort at every write boundary preserves previous credential/accounts/permissions/connection.
- First link, same-subject reconnect, changed-subject token refusal, omitted scope and proved empty enumeration.
- Invalid/duplicate/foreign/truncated/cyclic discovery refuses before authority writes; GA4/SC/MC field contracts preserved.
- Two stale callbacks, concurrent refresh, Merchant refresh/publication, owner password/disable, disconnect/account removal: no stale overwrite or mixed generation.
- Actual Mongo unknown claim/commit acknowledgement and post-commit read failure retain operation fence, emit safe code, no blind retry; command monitoring proves no repeated commit/exchange.
- Standalone/unsupported transaction fails closed; finite cancellation/driver cleanup behavior tested with disposable failpoints.
- Generic Google probe denied; composed exact Google test route unchanged; other providers unchanged.
- Existing 86 client/reporting/CAS tests and GA4/Merchant tests; offline owner revoke remains callable without OAuth.
- No actual provider, secret, production DB, browser or host access; root integrates/reviews/deploys separately.

## Primary references verified during design

- https://developers.google.com/analytics/devguides/config/admin/v1/rest/v1beta/accountSummaries/list
- https://developers.google.com/merchant/api/reference/rest/accounts_v1/accounts/list
- https://github.com/mongodb/specifications/blob/master/source/transactions/transactions.md
- https://www.mongodb.com/docs/languages/python/pymongo-driver/current/crud/transactions/

Next: root approves exact account_client read seam, then implement complete discovery and publication transaction with early remote code checkpoints, real replica CI and independent review. No source implementation or new tests exist in this checkpoint.
