# Disclose-once namespace bootstrap execution capability

This is the root-approved contract amendment for PR1667's blocking independent
review. Source675b8a6f501df4dfc0b87b6e0ff0eae72495a95f is rejected for release.
Its successful tests did not cover a second execute request after actual auth
replacement, page/root replacement, or another tab while the first request is
still ahead of durable consumption. Existing Mongo CAS prevents duplicate DDL,
but that does not satisfy the application's no-resend requirement.

This checkpoint changes tests and documentation only. The six new actual
AuthProvider tests intentionally assert the missing invariant against unchanged
675 runtime. They exercise both a rejected/lost response and an original pending
request, then attempt the actual second click. Runtime implementation follows
only after this checkpoint; root approved the contract before any runtime change.

## Durable server boundary

Use schema_version2 receipts for new initial preparations. Preserve the one fixed
global slot, four fixed namespaces, majority+j acknowledgment/readback, owner and
private authentication binding, catalog/UUID fences, cleanup/admission and
consumption-before-DDL behavior. Do not add a second namespace, delete/reset
operation, nontransactional DDL, index repair, financial flags or arbitrary scope.

Only the handler which successfully inserts a NEW version2 preparation, verifies
that insert acknowledgment and exact durable readback, and passes the final fresh
owner fence may disclose its execution capability in THAT initial prepare
response. An existing row is never a new issuance, even when request_nonce,
owner, private auth context and observation match exactly. A request racing the
unique insert cannot issue a second capability. The durable row itself records
that it belongs to the disclose-once protocol; a process-local flag is insufficient.

A dedicated issuance serializer emits the existing prepared capability fields
only in this initial acknowledged path. It is never called by receipt GET,
repeated prepare, lost-insert recovery, or historical execute. Public receipt
serialization is strictly token-free. Include schema version in token binding;
version1 preparations cannot be upgraded or adopted for execution.

| Situation | Server response | UI action |
| --- | --- | --- |
| GET, no row | no_preparation | Separate explicit prepare acknowledgment only |
| Initial new v2 insert ACK + exact readback + fresh owner | prepared, capability available | The originating live component can request execute once after a second acknowledgment |
| GET of v2 prepared row | prepared_retained_read_only, no capability | Inspection only |
| Any repeated prepare finding v2 row, including same nonce | prepared_retained_read_only, no capability | Inspection only |
| Old v1 prepared row | legacy_preparation_retained, no capability | Inspection only; execute refused |
| v1/v2 consumed or applied | reconciliation_required | Inspection only; no inference of rollback |
| v1/v2 verified | historical_completed_receipt | Inspection only, no fresh readiness |
| Expired prepared row | preparation_expired_retained | Retained/read-only, no replacement |
| Different owner/private auth | reservation_owned_by_another_auth_context | Generic inspection only |
| All compatible empty and no row | already_compatible_empty_noop | Current storage-only observation, no issuance |
| Unknown prepare/execute acknowledgment | Error without capability | Manual receipt inspection only; never resend/reissue the original operation |

All existing false financial/execution authority flags and zero provider/business
writes remain exact. Token-free states explicitly set execution_permitted:false
and preparation_token_available:false. Only the prepared response from INITIAL
prepare may carry request_nonce, execution_token, observation_digest,
manifest_digest, expires_at and would_create. GET rejects any response carrying
those capability fields instead of accepting an old server's token response.

An absent row after uncertain prepare is not proof the original request never
arrived. A separately acknowledged new prepare remains preparation-only and may
lose the global insert race; it cannot recover an earlier capability. An uncertain
execute necessarily originates from a prior acknowledged prepared row. No fresh
receipt, expiry reset, automatic retry, token reissue or guessed release is added.

## Client lifecycle

Never persist tokens, opaque binding fields or live responses in localStorage,
sessionStorage, IndexedDB, URLs, DOM, logs or global state. Keep the initial
capability in the originating component's memory only. Its dispatch consumes the
local opportunity immediately. On auth replacement, unmount, page reload,
unknown result, or invalid response, discard it permanently. Manual GET can
recover only inspection metadata, never another execute control.

Use the real AuthProvider generation fence and `_mezanAuthRetried:true` on all
requests, preserving abort/late-result protection and no interceptor refresh or
replay. Service validation must distinguish endpoint contracts: prepared is
accepted only from prepare; receipt GET cannot issue it. Execute validates an
already-held INITIAL prepare capability without fetching another one.

A client-only nonsecret uncertainty marker is insufficient: another device,
private browsing, cleared browser storage and server process replacement cannot
use the absence of that marker as evidence that execution was never attempted.
The selected contract therefore relies on durable server issuance restriction,
not local storage. No token persistence or new browser marker is needed.

## Cross-device/process and availability limits

A second tab/device gets token-free inspection even if the original execute has
not reached the consumed fence. Server restart reads the same version2 row and
cannot redisclose. A repeated initial POST after a lost prepare reply finds an
existing row and cannot redisclose, whether its nonce matches or differs. An
unconfirmed insert that actually persisted retains a read-only preparation; an
insert that never persisted has issued no capability and cannot have executed DDL.

Losing the initial prepare response, closing/reloading the page before execution,
refreshing the authenticated user object, or reaching an uncertain execute result
may strand a safely retained preparation. This slice deliberately has no recovery
or unlock mechanism. The UI must state that the capability is no longer available
and technical inspection is required; it must not call this cancellation or imply
no database changes. Any future recovery requires its own reviewed contract.

This application contract does not claim control over an adversarial actor who
manually copies and resubmits an HTTP body, or arbitrary network duplication.
Server owner/token binding and durable consumed CAS still admit at most one DDL
attempt. The supported UI emits no replay and no endpoint rediscloses capability
across the reviewed lifecycles. Tokens issued by version1 are not accepted by the
new execute handler. Mixed-version deployment must not be called accepted until
root's release verification confirms the reviewed runtime; this task performs no
live deployment or database action.

## Acceptance proof required

1. Six deterministic RED tests on unchanged675: lost/pending execute followed by
   actual same-owner AuthProvider refresh, full root/provider recreation, and
   another independently mounted tab. The actual attempted second POST must be
   blocked, not merely hidden by an assertion about flags.
2. Original positive initial ACK path remains usable exactly once with its two
   separate acknowledgments. Missing/false acknowledgments remain refused.
3. GET and repeated prepare of every v1/v2 state are token-free; initial issuance
   serializer, public fixture JSON, service endpoint validation and Arabic copy
   must agree. GET must reject an older capability-bearing prepared response.
4. Disposable authenticated real Mongo through the actual management router:
   same/different nonce repeated prepare, concurrent insert race, independent
   client/process restart, lost insert/claim/commit/final ACK, oldv1 execute
   refusal, retained history, and old technical receipts/diagnostics preserved.
5. Existing DDL rollback/foreign-create/UUID/auth/cancellation/cleanup tests remain
   meaningful. Reconcile changed prepared-inspection assertions explicitly; do
   not silently retain old capability-recovery expectations or skip tests.
6. Fresh CI exact source/checkout/counts/zero skips, actual AuthProvider and real
   API interceptor tests, full regression/build, then independent budget_audit
   and root review. Previous675 green results are historical, not acceptance.

Root owns later fresh production composition, Release Guard A/B and live
acceptance. No `/app`, provider, production DB or financial changes are authorized
by this amendment.
