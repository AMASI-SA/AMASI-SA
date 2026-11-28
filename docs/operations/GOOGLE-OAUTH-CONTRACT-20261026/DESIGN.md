# Google OAuth and account membership consistency — proposed contract

## State and limits

This is a design checkpoint, not implementation or deployment approval. It changes two documentation files only. Source review used `753c1717ceaf44156358aa986a4d082b20d0fdc7`; this branch starts at production merge `11a52dd933d7269cd80046219dcd93d5a6e62297`. Revalidate the latest production code before implementation.

The parent coordinator observed account `5052346701` connected, a campaign table with 22 rows, and a successful native management detail read before this design task. This task did not repeat that live check. No credential, environment value, provider response or live database was accessed. Do not attribute the working connection to this design.

The base already contains a preventive reporting change: report health does not set connection status/provenance or create a connection document. This prevents a report failure from revoking management, and prevents a successful or partial report from inventing restored authority. It does not implement the generation contract below or repair OAuth.

## Code evidence and remaining risks

| Surface | Observed behavior | Required contract |
| --- | --- | --- |
| `backend/integrations_control_center/google_projection.py::_persist_google_projection` | Credential, integration, permissions and account replacement are sequential independent writes. Prior refresh ciphertext can be reused without comparing Google subject. | Atomic, generation-fenced projection; same-subject proof before refresh-token reuse. |
| `google_ads_reporting.py::_refresh_access_token` | Updates shared credentials by tenant alone. HTTP failures are broadly treated as reauth. | Compare grant and token revision; classify rejection separately from transient failure. |
| `google_merchant_registration.py::_fresh_google_context` | Independently refreshes the same credential by tenant alone. | Use the shared refresh CAS helper and preserve Merchant scope/error contracts. |
| `google_analytics_realtime_routes.py::_fresh_google_access_token` | Another shared credential refresh writer. GA4 source attribution imports this helper. | Include GA4 in the migration; migrating Ads and Merchant alone leaves a bypass. |
| `ads_auto_sync_scheduler.py::_mark_needs_reauth` | Can mark the integration by tenant/provider without identifying the failed credential generation. | A stale failed refresh must not invalidate a newer relink. |
| `google_merchant_registration.py::_persist_merchant_result` | Replaces Merchant account membership separately from shared credential changes. | Commit the Merchant projection under the same grant fence without changing other services. |
| `google_ads_management.py::account_client` | Independently reads account/global status, gets a token, then independently computes credential epoch. | One consistent local generation proof plus a dispatch-time recheck. |
| `service.py::_persist_test_snapshot` | Generic local test path can persist connection/account authority. | Google guard must prevent this path from acting as a connection writer. |

The production package composer in `integrations_control_center/__init__.py` moves exact `test_google_*` routes before the generic `/{provider}/test-connection` route. Therefore ordinary composed Google test URLs use their exact local test handler. This is not a global proof that the generic service is unreachable: direct service calls and the base router still exist. Test both route precedence and direct-call protection.

## Proposed state model

Preserve the existing public projection schema for consumers. Add internal fields rather than redefining connected, reporting quality or account selection implicitly.

| Field | Location | Meaning and change rule |
| --- | --- | --- |
| `grant_generation` | Shared credential; copied to provider, account and permission records | Opaque unique value for a successful OAuth relink or explicit revocation. Never changed by access-token refresh. |
| `token_revision` | Shared credential | Monotonic revision of accepted access-token material within one grant generation. |
| `account_set_revision` | Provider projection; copied to accounts and permission observation | Revision of the completely verified membership for one provider under one grant. |
| `authority_write_revision` | Shared credential or dedicated per-tenant authority record | Real write fence to serialize projection mutations with OAuth; not part of campaign approval identity. |
| `discovery_complete` and safe observation metadata | Provider discovery evidence | Whether validated, bounded enumeration proves the entire relevant account set. Separate from report success. |

Exact types, bounds and indexes require implementation review. Credentials remain encrypted in the dedicated private collection. Public records and audit output contain no token, token fingerprint or credential-derived secret. A campaign authorization epoch binds grant generation and the relevant account-set revision, not token revision; otherwise ordinary refresh would unnecessarily invalidate approval.

Legacy rows without generations need an explicit compatibility/migration contract. Never silently infer a valid generation from a reporting read or relabel stale account membership. Existing approved proposals should fail closed when they cannot satisfy the new proof. Owner revocation must remain possible even with expired or unavailable OAuth.

## OAuth projection transaction

1. Capture the expected current grant generation when creating the server-side OAuth state, in addition to existing tenant, nonce, expiry and browser binding. Do not let an older outstanding callback overwrite an intervening relink.
2. Exchange the single-use code and collect user identity, granted scopes and complete provider discovery outside Mongo transactions. Do not log any token or response body.
3. Validate the proposed credential and each provider's membership evidence. Prior refresh ciphertext may be reused only for the same proved nonempty Google subject under an explicitly compatible grant. A changed subject without a new refresh token cannot inherit the previous user's long-lived credential.
4. Open a real transaction using snapshot reads, primary preference and majority write concern. Compare the captured generation against current authority; a mismatch requires a fresh owner workflow, not silent last-write-wins.
5. Write the new credential generation and the corresponding integration/account/permission observations as one consistent state. Preserve stable IDs and explicit account selection for accounts retained by the same proved identity. Do not erase unrelated operational settings as a side effect of replacing membership.
6. Missing granted scope revokes authority for that service. Incomplete discovery is not a verified empty set. Old membership may be retained as historical/stale evidence, but cannot be rebound to the new grant as current proof. A transient error in one service must not silently fabricate access or destroy another service's verified scope/membership.
7. Commit once according to the reviewed transaction failure contract. Unknown commit acknowledgement does not mean rollback; resolve by reading the exact generation/operation identity. Never repeat the code exchange or replay an advertising action to guess the outcome.

No HTTP request belongs inside a Mongo transaction. Optional telemetry outside the transaction must not be able to claim a different authoritative result; mandatory audit membership should share the commit where required.

## Discovery completeness

Existing discovery needs stronger evidence before account deletion or replacement can be authoritative:

- `_get_json` currently normalizes a non-dictionary success payload to an empty dictionary without a discovery error. Reject malformed payloads.
- GA4 and Merchant discovery use a bounded first page without a complete pagination proof. Follow validated pagination within explicit limits; reaching a bound or malformed continuation is incomplete.
- Validate Ads resource names and normalize account IDs strictly. Accessible-account enumeration does not establish campaign-write permission, account currency or timezone.
- Distinguish missing scope, proved empty enumeration, incomplete enumeration, manager accounts and verified client membership.
- Keep per-service errors separate. A successful Ads report or one valid metadata row cannot promote the whole shared Google grant or all accounts to connected.

## Shared access-token refresh

Route Ads, Merchant and GA4 through a common internal refresh helper while preserving their existing public error contracts. GA4 source attribution shares the GA4 helper; include its compatibility tests.

Read credential generation and token revision, make the refresh request outside a transaction, then compare-and-set the same generation and revision when storing the new access token. A losing response must be discarded. Reload a current proved credential if permitted by the caller's bounded policy; never return an old token with the latest generation.

An actual grant rejection can invalidate only the generation that was rejected. Timeout, rate limiting, provider 5xx, malformed success and local configuration failure are not interchangeable with owner revocation or confirmed token expiry. Unknown storage acknowledgement requires an exact revision read; no automatic campaign dispatch or budget release follows from it.

Merchant projection updates must use the same grant fence. A transaction that merely reads the shared credential and writes a separate provider document can still permit write skew against OAuth. Perform a real conditional authority-revision write in the projection transaction so conflicting grant changes serialize. This revision is not a token refresh or campaign authorization epoch.

## Management proof and dispatch boundary

`account_client` should read credential, global provider authority and requested account membership in a consistent snapshot, requiring matching grant and account-set generations. Validate the necessary scope, connected status, provenance and requested tenant/account. Return the private proof alongside the client, never to a model or ordinary public payload.

Native metadata/configuration and campaign reads remain outside Mongo. Recheck local grant/account-set identity before adopting the read evidence and immediately before dispatch through the existing authority/gateway. Both management and creation authorization paths must consume the same proof; adding a helper without migrating consumers is insufficient.

An access-token refresh preserves approvals. Relink, account removal, scope loss and explicit revocation invalidate affected proof. Local transactions cannot make external OAuth revocation atomic with Google HTTP. Keep the existing submission-floor, uncertain-outcome and read-based reconciliation contracts; never infer that an ambiguous provider request was not sent.

## Transaction capability and compatibility

Use a real disposable write/abort probe against the selected database. Replica metadata or a successful ping alone is not transaction evidence. Bind capability to the actual database/client and a bounded validity interval. Unsupported transactions or a failed capability probe must stop the authority mutation; never fall back to sequential writes.

Before runtime integration, enumerate every shared credential writer and every Google connection/account writer again. Preserve GA4 property reads, GA4 source attribution, Search Console account projections, Merchant registration and existing Ads reporting. Avoid a broad database rewrite merely to add fields. Migration timing, legacy proof handling and rollback need their own reviewed acceptance contract.

## Acceptance requirements — not yet executed

| Case | Required observation |
| --- | --- |
| Abort after each OAuth write stage | Credentials and every provider/account/permission remain at the previous complete generation. |
| Concurrent callbacks based on one old generation | Exactly one accepted relink; no mixed account set or silent overwrite. |
| Old Ads, Merchant or GA4 refresh finishes after relink | New credential remains unchanged; caller cannot use the stale response as new-generation proof. |
| Old refresh rejection arrives after relink | New grant remains connected and usable. |
| Access refresh versus relink | Refresh retains valid approval; relink invalidates old proof. |
| Changed subject and no new refresh token | Previous subject's refresh token is never inherited. |
| Complete empty membership versus truncated/malformed discovery | Only complete evidence can assert removal; incomplete evidence cannot authorize management. |
| One omitted scope or one service discovery failure | Affected authority fails closed; unrelated proved services preserve their contract. |
| Merchant projection races OAuth | No mixed generation and no modification of Ads/GA4/Search Console projections. |
| Exact production test route versus direct generic service call | Exact route wins; generic Google path cannot overwrite authority. |
| Account removal/relink between metadata and submission | No advertising mutation on stale local proof. |
| Standalone Mongo or failed transaction probe | Explicit unsupported/unproved result and zero partial authority writes. |
| Lost commit acknowledgement | Read resolves exact generation/operation; no blind relink, retry, quota refund or fence release. |
| Credentials unavailable during owner revocation | Owner can revoke automation without a live Google read. |
| Existing reporting prevention matrix | Connected/error/needs-reauth/missing documents are never promoted, revoked or created by reporting. |

Exercise races with disposable real Mongo replica transactions, real matching and commit behavior, and controlled provider-transport fakes. Unit tests alone do not prove transaction capability or recovery. Capture exact commands, full source SHA, CI and independent review. No paid advertising write is required to prove this credential contract.

## Next safe action and handoff

The parent coordinator should push this dedicated documentation branch and record the exact local commit SHA. This design is not merge-ready runtime work and must not acquire a Release Guard lease or be described as restored OAuth.

For implementation: fetch current production and task checkpoints, read all applicable instructions, confirm active file ownership, review this schema and service compatibility against fresh code, then create a focused failing disposable-replica test for one atomic vertical. Coordinate shared scheduler and package-composer files. Preserve the currently working live connection; do not request reauthorization solely because this design exists. Actual recovery, if later needed, requires fresh verified OAuth/account evidence and separate native read acceptance.
