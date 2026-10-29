# Merchant observation selector and attachment assessment

Read-only assessment of source `7e686bcfc8d89c60cc9f8a4a9dba2d8289846f91`,
tree `0f3bfab9bebd343d27b01bb00e4ee14d98a76be9`. No runtime edits, provider
calls, credential reads, shared attachment, release intent or host changes.
This is a proposal, not an implemented or deployable selector.

## Existing surfaces and their exact limits

| Surface | Actual source and behavior | Appropriate use here |
| --- | --- | --- |
| GET `/integrations-v2/overview` | `routes.py::overview` derives the tenant through `require_review_scope`. Owners use their authenticated ID; a special Meta reviewer gets only Meta cards. For Google, `catalog.py` has no legacy sources, so `service.py::_snapshot` reads V2 provider/account projections. `_v2_snapshot` reads at most 50 local rows per provider. | The only existing common local Ads and Merchant candidate list. It does not call Google, prove completeness, bind a public owner revision, or prove current provider access. |
| Overview account rendering | `_card_from_snapshot` drops rows without `mezan_integration_account_id`; `_decorate_account` can inherit missing status, provenance and permissions from the provider. `models.py::OverviewResponse` and `services/integrationsV2.js` expose a display contract; frontend normalization adds defaults and a Map can collapse duplicate provider cards. | Consume the raw API response in a new strict service. Even a strict frontend parser cannot recover distinctions already removed by the server. Label every option as a stored candidate, never as independently verified ownership or permission. |
| GET `/integrations-v2/google_ads/workspace` | `available_accounts` uses `_accounts`, which prefers locally `mezan_selected` rows when any exist. Workspace rendering adopts `response.account_id` when none was selected. | Do not reuse workspace selection as an explicit owner choice for this child. |
| GET `/integrations-v2/google_ads/management/readiness` | Fetches native metadata and conversion goals using `account_client`. UI may select the only readable account automatically. | Not a local list request, and not the new helper's same-credential membership boundary. Do not trigger it for this selector. |
| GET `/integrations-v2/google_ads/lookup/merchants` | Ads `product_link` query via `google_lookup`, under an Ads account. | Indicates Ads-side linked Merchant IDs only. It does not prove a stored owned Merchant row, Merchant API content scope, or product access. Not required for the smallest selector. |
| OAuth connect/callback; test-connection | `google_connections.py` connect/callback and existing service test routes have their own writes and refresh/discovery behavior. | Never call from selection, mount, refresh-list or observation-error handling. |
| Configured Merchant registration | `google_merchant_registration.py` uses a separately configured registration/account flow. | Never use its configured account or developer identity as a default or permission proof. |

The local account rows are normally written by `google_projection.py` after
OAuth discovery, with explicit provider, user, external account ID, API
provenance and status; Merchant has `ad_account_id=None`. However the current
writer is a sequential provider update/delete/insert flow, not an atomic account
set publication. It writes at most 200 discovered rows per provider. Current
Merchant discovery requests one page of 100 and does not traverse a next token.
Thus neither all local rows nor a displayed overview is a complete remote
Merchant membership assertion. This assessment does not modify those writers.

No selector reads the shared encrypted Google credential or invents permissions
from `content.readonly` display aliases. Actual OAuth scopes and current token
binding remain inside the reviewed bound-read helper, reached only by the
explicit observation route.

## Source-backed local reproduction

Using the existing `test_mezan_integrations_v2.FakeDB`, actual
`IntegrationsControlCenterService._v2_snapshot` and `_card_from_snapshot`, 51
synthetic Merchant rows with IDs and no row-level status/provenance produced:

```json
{"input_count":51,"returned_count":50,"source_row_has_status":false,"decorated_status":"connected","decorated_provenance":"api_connection","completeness_field":false,"contains_owner_id":false,"contains_observation_revision":false,"writes":0}
```

The provider was explicitly `connected/api_connection`; no credentials existed
in the fixture. This proves display fallback and local truncation, not an
authorization bypass: the reviewed observation route reads raw local membership
again and rejects unsupported rows. The probe was read-only and used no network.

## Considered overview-only alternative (not selected)

The following frontend shape was considered; root selected the dedicated local-list contract below instead:

- `frontend/src/components/marketing/GoogleMerchantObservationSelector.jsx`
- `frontend/src/components/marketing/GoogleMerchantObservationSelector.test.jsx`
- `frontend/src/services/googleMerchantObservationChoices.js`
- `frontend/src/services/googleMerchantObservationChoices.test.js`
- `.github/workflows/google-merchant-observation-selector.yml`
- a new task CONTRACT/STATUS directory.

The wrapper imports the real `useAuth` hook from `context/AuthContext.jsx` and
the frozen `GoogleMerchantObservation` child. It accepts no caller owner-ID or
authorization Boolean. Visibility requires `authStatus === "authenticated"`,
a valid string `user.id`, owner role compatible with the backend's role check,
and no explicit disabled/inactive/deleted marker. No `is_owner` fallback,
`created_by`, URL parameter, cached report account, browser storage, first row,
or configured Merchant supplies the selection. Backend authorization remains
independent of these presentation checks.

Both select values start empty, even with a single candidate. A manual
"تحديث قائمة الحسابات" button issues exactly one fixed existing `lib/api` GET
to `/integrations-v2/overview`, with AbortController, a finite proposed 30-second
timeout and `_mezanAuthRetried:true`. This prevents this GET from causing a
browser refresh POST or automatic replay after 401. No mount, expand, option
change, window focus or timer request; no POST, sync, test connection, OAuth,
settings/account-selection write or Google lookup. This wrapper adds no
background reads; existing page behavior elsewhere is unchanged.

The new service validates raw object/array shapes before extracting only the
two exact Google provider cards. Missing/duplicate relevant cards, duplicate
provider IDs or candidate native IDs, malformed rows and noncanonical IDs must
not become selectable through normalization. Preserve canonical positive
decimal strings within signed int64, including exact Ads external/ad ID
agreement and Merchant external ID with null/missing ad ID consistent with
the actual writer. Use provider and row `connected/api_connection` only as a
display filter, never as an independent stored-membership proof. Rows that are
clearly disconnected/error/incomplete stay visibly unavailable or cause a safe
unproved-list result; do not silently upgrade them. Do not infer IDs from names,
resource URLs or format-stripped values. Names are bounded inert text; IDs stay
visible so similar names are reviewable.

Suggested Arabic explanation:

> هذه حسابات ظهرت في بيانات الربط المحفوظة، وقد لا تشمل كل الحسابات. اختر
> حساب إعلانات Google وحساب Merchant Center بنفسك؛ تتحقق القراءة التالية من
> الربط والصلاحية الحاليين.

Show only the returned subset, labeled as such. An empty subset means no
available candidate in this read, not that the owner has no Google account.
The request completion time is a local display time, not provider freshness.
The response's `generated_at` does not prove the accounts were newly discovered.

After explicit choices, pass `ownerId=user.id`, `isOwner=true` and the exact two
IDs to the unchanged child. Selecting IDs only renders the child; its separate
manual read button invokes the already reviewed owner route. Do not pre-filter
pairs through a native link list and imply they work. A stored candidate pair
may be rejected by the protected route; this is an expected, clear result.

## Cancellation and revocation behavior

Key wrapper state by the actual authenticated owner. Leaving authenticated
owner mode (including `checking` or `unavailable`, while AuthContext may still
retain an old user internally) immediately removes choices and evidence,
aborts pending list/child reads, and makes late completions inert. Never call
`refreshUser()` automatically from this wrapper to resolve an error.

Manual list refresh clears both current choices and unmounts old observation
evidence before sending the request. Even an identical returned list requires
explicit reselection; this avoids claiming a new list response preserves old
ownership evidence. Cancellation, unmount and later generations ignore late
responses even if the transport ignores abort. A list error/401/403 leaves no
selectable old list and no old evidence. Owner ID, owner role or auth-status
changes during a request cannot restore a prior owner's candidates.

Within an existing choice, the frozen child already clears prior evidence on
a manual observation read and renders safe errors on revocation, membership
change or missing grant. It does not report these errors to a parent callback;
no global response interception or DOM inspection should be added. After such
an error, the selected IDs can remain visible as candidate choices only. A
manual list refresh clears them. Actual backend checks run again for every
read. This design does not promise immediate revocation detection without a
new request, uninterrupted authorization, or invalidation of all open tabs.

## Root-owned attachment boundary

Root attaches `attach_google_merchant_observation_routes` using the actual
current-user dependency and integrates this new wrapper at the reviewed
commercial/workspace surface. The wrapper must not receive workspace's
implicitly selected account as proof of explicit choice. Root keeps router,
workspace, AuthContext, existing services and the sealed helper/observer/child
unchanged until a separately reviewed integration source is prepared.

The existing 699 route remains mandatory: authenticated owner field binding,
raw unique connected API provider/account snapshots, same-token scope binding,
complete native Ads link-set reads with exact account/selected Merchant,
bounded Merchant traversal, and final membership/credential fences. No field
returned by the list is a replacement for any of those checks.

## Acceptance for the next slice

Tests should run through real AuthContext shape, existing API interceptors and
the existing child with synthetic transports. Prove: zero initial requests;
one explicit list GET/no replay or refresh POST; empty selections with one or
many accounts; no workspace/URL/default adoption; real overview response shape;
bounded malformed/duplicate-ID refusal; connected/error and incomplete rows;
displayed partial-list limitation; explicit choices cause no read; only the
child's read button requests exact IDs; owner/auth transitions and old responses;
refresh/cancel race generations; refresh clears unchanged choices; safe
401/403/409 handling; inert labels; current child's route response fixture;
and no POST/sync/provider lookup. Governed frontend suite/build and independent
review are required before root's fresh source integration and live acceptance.

## If complete local account enumeration is required

The overview endpoint cannot supply it. A separate owner-only local GET module
would be needed, with current auth binding and bounded PRIMARY snapshot reads of
the exact two provider/account sets, explicit raw-field presence/identity checks,
max+1 overflow refusal, a nonsecret observation revision, and a claim limited to
complete *local* enumeration. It must not read credentials, call Google, repair
rows or assert remote completeness. This requires a reviewed shared membership
reader interface rather than copying or weakening the sealed route's fences.
It is a larger alternative, not a prerequisite to the candidate-only wrapper
above, and is not assigned or implemented by this assessment.

## Evidence state and remaining external inputs

Frozen owner route699 passed exact350 tests including52 actual Mongo cases;
child7e686 passed exact61 UI tests and build, both independently reviewed and
accepted by root within their unattached scope. Child PR1648 now has15 successful
PR workflows and one known Release Readiness failure: its old base is not
current production ancestry. No pending CI remains. That failure is not an
infrastructure retry and this assessment does not edit intent/history to hide it.

No owner-specific accounts, existing grant, native link or live provider access
were observed here. Missing Merchant local projection or grant remains a real
input gap, shown as an unavailable choice or protected read refusal; never fill
it from an Ads link or configured account. Root must source accepted slices on
fresh production, attach coherently, perform CI/review/Release Guard and test
the explicit owner flow live before declaring the feature available.

## Root-selected implementation contract

Root requires the dedicated local list, because the overview loses raw evidence.
The overview-only service above will NOT be implemented. New fixed owner-only
GET `/google_ads/merchant-products/choices` remains unattached and returns only
reviewable local candidates. Reuse the frozen699 AUTH/OWNER/PROVIDER/ACCOUNT
projections and canonical-ID/BSON encoding helpers without changing that file.
A new projection adds bounded optional display_name only. Two bounded PRIMARY
snapshot transactions read unique owner, exactly two providers and the entire
scoped account set with400+1 sentinel. Initial authenticated dependency fields
must match; final private owner/provider/account encodings must match. Missing,
duplicate or malformed evidence fails closed; no credential collection or Google
provider is touched. For the first conservative slice, provider and every account
row must explicitly be connected/api_connection; mixed unavailable sets refuse
rather than quietly excluding uncertain ownership records. Empty account sets
are a valid complete local observation, never proof of no remote accounts.

Return canonical Ads/Merchant IDs separately, bounded display names (ID fallback
only when a name is absent), an opaque nonauthorizing observation ID/time,
authenticated owner ID for UI scope matching, local completeness/count, and
explicit native-link/access/mapping/eligibility/execution flags false. The limit
is on the full local set, not each displayed subset. No public auth-field or
private BSON snapshot is returned. An observation is not a durable authorization
or uninterrupted membership claim. Each real product read still runs699 anew.

Approved new files: backend/integrations_control_center/
google_merchant_observation_choices.py; backend/tests/
test_google_merchant_observation_choices.py and its `_mongo.py` companion;
frontend GoogleMerchantObservationSelector component/test and
services/googleMerchantObservationChoices service/test;
.github/workflows/google-merchant-observation-choices.yml; new task CONTRACT/STATUS.
The manual wrapper contract above applies, using this dedicated endpoint instead
of overview. No existing parent/router/helper/child edits. Root approved this
implementation after reviewing the bounded interface proposal.
