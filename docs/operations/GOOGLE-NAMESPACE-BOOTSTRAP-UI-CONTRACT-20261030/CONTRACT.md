# Google namespace bootstrap: attachment and owner interface contract

Status: DOCUMENTATION-ONLY proposal for coordinator review. No runtime, router,
Workspace, AuthContext, API interceptor, DDL, host or production change is authorized
by this checkpoint.

Base: `622c310d8dcee13da61410bca6b3ac85134a9cfe`
(base tree `c4074b70bc63d0bc44759256841319938516af20`).
New branch: `codex/google-namespace-bootstrap-ui-contract-20261030`.
Frozen bootstrap prerequisite: PR1656 source
`2c6aa43112019544f836f9d8e0d2464d4581f8cf`,
tree `bed2199f00fb4d7fc7cba308bf125f2ff9394e03`.
Final prerequisite evidence: `23e836301334bb08725275ba2b1049b515320ce6`;
CI40976213231/job123804759846:111 passed18.05s,47 unit plus64 actual
authenticated Mongo8.0.26 cases, zero skips. These are prerequisite results,
not results for the proposed UI or attachment.

Root reported reading the full frozen module, exact CI log and independent
budget_audit artifact7747 without a new bounded finding. Keep PR1658 and all its
frozen source/intent/host work untouched. Root alone owns production integration,
release A/B, Release Guard v5, deployment and live acceptance.

## Scope and ownership

This checkpoint adds only this contract and STATUS.json. Proposed later work:

| Owner | Proposed change | Boundary |
| --- | --- | --- |
| Coordinator | Two additive lines in `backend/integrations_control_center/google_ads_routes.py` | Import and call the already reviewed attachment function with existing db/current_user; no startup DDL or provider call |
| UI implementer assigned by coordinator | NEW `GoogleNamespaceBootstrap.jsx`, NEW `googleNamespaceBootstrap.js`, NEW corresponding tests and fixtures | Explicit storage flow only; no existing auth/interceptor modification |
| Integration owner assigned later | Actual-router tests, real-Mongo integration/fixture parity and dedicated CI | Retain prerequisite111-case gate and add exact-count integration gate after collection |
| Coordinator, only after separate approval | Minimal `GoogleAdsWorkspace.jsx` parent seam and its real AuthProvider test | This file is NOT owned or changed by the present task |

The proposed backend seam inside `attach_google_ads_management_routes` is exactly:

```python
from .google_namespace_bootstrap import attach_google_namespace_bootstrap_routes
attach_google_namespace_bootstrap_routes(router, db, current_user)
```

The frozen prerequisite is not currently part of base622c. Any implementation
candidate must selectively include its reviewed runtime/test/workflow blobs and
verify equality; do not merge its historical production ancestry or inherited
release intent blindly. If production advances, the coordinator creates a fresh
reviewable source while preserving other work.

A future parent may place the NEW child inside the existing owner storage section.
It must preserve the current technical commit check and namespace-read diagnostics,
commercial-plan/native/prior-season panels, all policy controls and campaign flows.
Do not replace a diagnostic with bootstrap or treat either result as financial
readiness. No Workspace edit until the coordinator approves that exact seam.

## Owner experience

Working title: "تجهيز حفظ حدود جوجل". The operation prepares fixed storage
structures in Mezan. It is database-global for the four reviewed names, not an
operation on the currently selected Google Ads account. No account, currency,
budget, objective, campaign or arbitrary collection is accepted by the child.

Default view explains scope and shows only the explicit button "قراءة حالة التجهيز".
Mount, opening details, owner/account changes, auth recovery and rerender send no
bootstrap GET or POST automatically. The enclosing Workspace's existing unrelated
reads remain outside this child; they must not trigger bootstrap.

1. Owner explicitly reads the receipt. A valid `no_preparation` response, with no
   unresolved local POST uncertainty, may expose the preparation acknowledgment.
2. Preparation acknowledgment (unchecked by default):
   "أفهم أن هذه الخطوة تتحقق من المتطلبات وتحجز طلب تجهيز تقني، ولا تنشئ حملات أو تفويض إنفاق."
   Button: "مراجعة المتطلبات وحجز طلب التجهيز". This POST can write the technical
   reservation, but cannot perform target DDL.
3. A strictly validated `prepared` response displays only the fixed labels in
   `would_create`, the response receipt time clearly labeled as the browser's
   receipt time, and server-provided expiry. Unknown/duplicate labels fail closed.
   Do not display or persist the opaque execution token or digests.
4. A separate, newly unchecked execution acknowledgment:
   "أوافق على تجهيز بُنى الحفظ الناقصة الموضحة فقط؛ لا يشمل ذلك تشغيل الحملات أو تغيير حدود الإنفاق."
   Button: "تنفيذ تجهيز الحفظ". Only this click sends one execute POST using the
   original validated server-returned binding. It must not reuse the first checkbox.
5. A valid current execute success says:
   "اكتمل تجهيز بُنى الحفظ المحددة وثبتت قراءتها في هذا الطلب. لم تُفعّل سياسات أو حملات، ولم يُمنح تفويض إنفاق."
   Success never changes policy controls, campaign permissions, global readiness,
   funding flags, owner budgets or account connection state.

Technical Mongo names, UUIDs, tokens and digest details are not needed in the main
flow. Fixed Arabic labels are enough: إعدادات الإدارة، إعدادات الإنشاء،
حارس حدود الحساب، فحص التخزين. Any optional technical explanation must be
inert text, never a URL or executable value supplied by the response.

## Authentication and response generations

Use the real `useAuth` from `frontend/src/context/AuthContext.jsx`; an `isOwner`
prop or Workspace readiness permission is at most an additional restrictive hint,
never the source of authority. Require authenticated status, a bounded canonical
string user.id and exact owner role. Explicit inactive/disabled/deleted flags deny
the view; malformed flags fail closed. Server checks remain authoritative.

The current AuthProvider does NOT expose a public session-generation counter.
Do not pretend its private `probeGeneration` is available. The NEW child maintains
a local monotonic generation from observed authStatus, user-object identity and a
canonical snapshot of relevant public identity/role/active fields. Replacing the
authenticated user object invalidates pending work even when the owner ID is the
same. A render-time current-context ref plus request identity fences callbacks;
an abort signal or an effect cleanup alone is insufficient.

A 401/403 from any bootstrap request immediately invalidates the child's current
authorization generation, clears token/result/acknowledgments and hides POST
controls. It must not mutate global AuthContext, manufacture a login state, read
cookies/access tokens or initiate login/refresh. Same-owner access may resume only
after a newly observed authenticated provider context and a new explicit receipt
read. Silent server revocation cannot be detected solely from browser state:
the backend's fresh owner/password/private-context fences remain required.

On checking/unavailable/anonymous status, owner change, role loss, relevant identity
change, refreshUser replacement, or unmount: advance generation, abort the client's
pending wait, discard local sensitive bindings and acknowledgments, and ignore all
late success/failure/finally callbacks including responses from adapters that ignore
abort. An old callback must not clear a newer request's busy latch.

Abort is NOT rollback. If a POST may have been sent, the outcome is unknown; the
server may still hold the original task or a durable consumed/applied receipt.
Never say "لم يحدث تغيير" or "أُلغي التجهيز" merely because the UI aborted or auth
changed. Do not add a cancel/delete/reset operation. Hiding stale owner content
does not release server admission. A new owner sees no prior owner's token or
details and can only initiate a fresh authorized receipt read.

## Exact transport and response contract

Use existing cookie-authenticated `frontend/src/lib/api` and `GOOGLE_ADS_ROOT`.
No new Axios client, token reads or shared interceptor changes. Every bootstrap
request includes `_mezanAuthRetried:true`, signal and a bounded client timeout
(proposed30000ms, UI wait bound only). The marker prevents the existing401
interceptor from posting /auth/refresh and automatically replaying this operation.
Test it through the real interceptor/adapter, not just a mocked service function.
No automatic network retry, polling, backoff POST, route refresh, provider call or
read-after-write request. Each GET also requires its own explicit click.

| Operation | Method/path suffix under GOOGLE_ADS_ROOT | Exact request body |
| --- | --- | --- |
| Read | GET /automation/namespace-bootstrap/receipt | No body/params; no arbitrary scope |
| Prepare | POST /automation/namespace-bootstrap/prepare | request_nonce:32 lowercase hex; acknowledge_preparation_only:true |
| Execute | POST /automation/namespace-bootstrap/execute | request_nonce:32hex, execution_token:64hex, observation_digest:64hex, manifest_digest:64hex, acknowledge_storage_only:true |

Generate a fresh cryptographically strong request_nonce only for a new explicit
prepare action. If secure randomness is unavailable, refuse locally; do not use
time/Math.random. An existing reservation may be returned by prepare with its
ORIGINAL nonce, different from the newly submitted nonce. That is valid backend
behavior, not proof a new reservation was created. Require a fresh review and use
the validated returned original binding. Never substitute the outgoing nonce or
mint a new execute token.

Store execution bindings only in private component memory. No localStorage,
sessionStorage, URL, DOM attributes, console, telemetry, downloaded fixture with
live values, or global state. Discard them on use, invalidation or uncertainty.
The service validates the complete public response before the component can render
success or enable an execute-request control. Allow only known status-specific
fields; reject unknown states, wrong types, extra authority claims, noncanonical
nonce/digests, impossible labels, malformed expiry and token leakage in a state
which does not define a token. Do not coerce strings/numbers to booleans.

Every accepted result has scope `fixed_namespace_bootstrap`, provider_writes:0,
business_document_writes:0 and strict false for execution_authorized, funding_ready,
policy_configuration_verified, legacy_bindings_verified, future_permissions_guaranteed,
atomic_catalog_snapshot_verified and new_uuid_lineage_verified.
namespace_contract_verified is true ONLY for the two current-observation states
listed below. For receipt-like states, execution_permitted MUST remain false.
The UI's local ability to request a storage operation is not backend campaign
execution authority; do not rename that false flag or propagate a new permission.

Only `prepared` additionally has preparation_token_available:true, the four binding
strings, a valid UTC expires_at, and a unique nonempty subset of the four fixed
labels in would_create. Validate expiry and disable on client-observed expiry;
client time never proves server validity and never extends the600-second server
window. Execute still revalidates everything. No arbitrary freshness TTL is added
to completed history or no-op observations.

## State and uncertainty semantics

| Durable/server or local state | Exact public result | Allowed next UI action and wording |
| --- | --- | --- |
| Initial | No response yet | Manual receipt GET only; no readiness claim |
| No record | no_preparation | After a successful explicit GET and no local unresolved POST, owner may separately acknowledge a NEW prepare; never execute |
| Prepared | prepared; namespace_contract_verified:false; execution_permitted:false | Review original binding and fixed would_create labels, new execute acknowledgment; or explicit GET |
| All four already compatible and empty | already_compatible_empty_noop; namespace_contract_verified:true | Prepare did no target DDL/reservation. Say this request observed compatible empty storage; no execute token or execute button |
| Consumed | reconciliation_required | Read-only; no resend, recreate, release or guessed abort |
| Applied but completion not verified | reconciliation_required | Same public state as consumed. The UI MUST NOT infer which internal state exists or claim no DDL occurred |
| Verified current execute | verified_at_observation; namespace_contract_verified:true; receipt_retained:true; execution_token_consumed:true; unique fixed created_labels | Current request completed; discard token. No further POST; manual receipt read only |
| Verified historical receipt | historical_completed_receipt; namespace_contract_verified:false; execution_permitted:false | Say a previous completion receipt exists, not fresh readiness. No repeat execute or prepare |
| Expired preparation | preparation_expired_retained | Retained reservation, read-only. Expiry is not deletion/release or permission for a fresh nonce |
| Different owner/private auth context | reservation_owned_by_another_auth_context | Generic retained-reservation message, no owner identity or token, read-only |
| busy/cleanup unknown/deadline/transport error after POST | No trusted terminal success | Generic unresolved outcome; read-only manual receipt recovery, no automatic retry |
| Invalid body/status/authority flag | Untrusted response | Clear candidate/token, fail closed, no success or execute controls |

GET never returns a fresh readiness state. A later GET after successful execution
must downgrade the displayed result to historical metadata, not retain the old
current success badge as if it were refreshed. Neither a historical receipt nor
technical diagnostic success implies current index inventory, legacy commitment
bindings, six policy-route safety, funded GRANTS/RUNS provisioning or campaign launch.

Prepared recovery after an uncertain PREPARE is allowed only through explicit GET
and a newly checked execution acknowledgment. Prepare cannot have sent target DDL.
If GET returns no_preparation after uncertain prepare, only a NEW explicitly
acknowledged preparation can be offered, after stating no durable reservation was
observed; never assert the first request never reached the server.

After an uncertain EXECUTE, the current interaction remains read-only even if a
later GET returns prepared or no_preparation. There is no client reset/refresh
button that re-enables POST in that interaction. Consumed/applied/expired outcomes
are always retained. Do not claim the browser's in-memory uncertainty latch
survives total reload, another device or process loss. Across those boundaries the
frozen server contract governs: an absent row never permits execute, a prepared
row never conceals a DDL dispatch without prior positive durable consumption,
and no request is automatically replayed. A future recovery/unlock UX requires
separate review; this slice provides inspection only after uncertain execute.

On POST401/403, cleanup error or rejected schema, do not infer no write: the
reservation or transaction may already exist. On GET failure, preserve any local
POST-uncertainty blocker; do not restore stale readiness or an old executable token.
Display only fixed, endpoint-specific safe messages. Never render raw exception
detail, connection strings, role names, database responses or arbitrary server text.

## Implementation acceptance tests proposed (not yet run)

| Layer | Required meaningful evidence |
| --- | --- |
| Frozen prerequisite preservation | Exact reviewed module/test/workflow blobs; original111 cases remain, including64 real Mongo; protected current622c and frozen1658 files unchanged |
| Actual management router | The real attachment function exposes exactly three intended endpoints once; importing/attaching/mounting performs zero DB writes/DDL/provider calls; owner dependency unchanged |
| Real authenticated Mongo integration | Actual management router + real auth dependency -> manual prepare(no DDL) -> execute(one commit/four absent names) -> GET(history); old diagnostics and receipt bytes preserved |
| State fixtures | Produce responses through the actual frozen handler/public serializer; validate against real-Mongo public responses for every defined state. Normalize only validated unstable test identities/timestamps, never status/authority/receipt fields; do not call random-token fixtures byte-identical |
| Real AuthProvider UI | Owner/nonowner/checking/unavailable/anonymous, same-ID refreshed object, role/flag change, owner A→B→A, unmount; actual /auth/me hydration, not a fabricated useAuth stub |
| User intent | Zero bootstrap requests on mount/open/rerender; explicit GET; separate unchecked acknowledgments; exactly one POST despite double-click/Enter; old prepared nonce preserved; no execute from no-op/history |
| Pending/late races | Aborting or ignoring abort on prepare/execute/GET, late success/failure/finally, old request vs new generation; no token/result leak or reopened POST; actual backend task and durable state remain intact |
| Transport | Actual API interceptor test:401 yields zero refresh POST and zero replay for both POSTs and GET;503/429/network/timeout never retry; no provider GET or side effects |
| Strict service contract | Each state, missing/extra keys, every false flag set true/omitted/string, nonzero writes, unknown/duplicate labels, token in terminal states, malformed/expired timestamp, nonce/digest identity; all fail closed |
| Ambiguity | Lost prepare ACK with/without durable row; lost consume/DDL/commit/final ACK; expired retained; owner invalidation after write; no-record not abort proof; uncertain execute remains read-only |
| Existing UI regression | Existing storage checks, commercial plan/native/prior-season, campaign/AI/page assertions retained under actual AuthProvider; no shared auth relaxation |
| Build/release separation | Focused tests, full governed Google frontend/backend and original build; exact JUnit counts/no skips after tests exist; fresh production Source A/intent B and Release Guard are coordinator-only later work |

Do not invent final test totals now. The implementer records collected counts,
red/green regressions, exact source/tree and direct vs synthetic CI checkout before
claiming this contract implemented. UI/component tests alone are insufficient for
the actual router/auth/DDL integration.

## Remaining approval boundary and next action

Coordinator review of THIS concrete contract is the next step. No shared-file
implementation is authorized by its existence. After approval, assign one owner
for the two router lines and one owner for the future parent seam, and implement
new child/service/tests in an isolated candidate. Preserve original source2c6,
PR1658 and all production leases. Runtime or interface changes needed beyond this
contract require a separately reviewed successor, not silent edits to frozen evidence.
