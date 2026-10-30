# Durable funded initiation and owner interface contract — proposal only

## Baseline and boundary

The accepted storage source is PR1659 tested62129405ba3aba94f1c5edc3001f337d4665da19, runtime frozen8438cee05dd6e70983f76fd94be442b3d47e2249, durable ledger eb9669e1a134db6494c4acd00d9a238298c190da. Actual focused CI40976156447/job123804579244 passed264 tests including89 real Mongo cases without skips. This is bounded storage evidence, not permission to fund a campaign. This document implements nothing and does not supersede RESERVE-INITIATION-AMENDMENT.md from995504.

The proposed next slice is an internal mandatory-transaction preparation producer. It may return a private initiation ACK only following a positive exact raw commit ACK in its original task. It never calls a paid provider mutation or returns an execution token to a browser. Public launch, activation UI wiring, prepared paid-client gateway and workers require separate reviewed implementation. Existing configured grants remain inert.

## Stable admission identity and immutable prepared journal

The permanent slot is `(user_id, canonical_account_id, request_key)` and `grant_key` remains its existing hash, excluding grant revision. A changed revision, new process, retry button or changed timestamp cannot create a new attempt in that slot. A new owner request is a new explicit reviewed identity, never an automatic replacement for an uncertain attempt; it cannot bypass an unresolved account fence or hold.

The producer derives server-side proposal_id, execution_claim_id, preparation_operation_id and the new funded creation_identity from the reviewed pure graph. It rejects an existing RUNS row for the slot in ANY phase. Its proposal idempotency identity is the same funded creation identity. Caller JSON cannot set active state, activation proof, actor, ownership, native_verified, graph operations, scope epochs or claim IDs.

RUNS `phase=prepared` must include user/account/grant_key/request_key, grant revision, owner generation, review digest, creation_identity, exact immutable full intent and graph digest, preparation operation identity, original proposal immutable projection, exact own claimed account fence, execution claim, consumed=true, prepared timestamp, validity bounds and the POST-PREPARATION common guard epoch. It stores an explicit versioned preparation_binding and digest matching the consumer's existing required projection; additional original graph/auth/product/native proof fields are recorded separately. No secret credentials or reusable authority object is persisted.

Important schema detail: the existing unique RUNS identity index includes creation_identity. The real producer MUST populate it at preparation; copying the intentionally minimal test-only preparation fixture would create missing/null unique-key collisions across different grants. Consumer accepts only the matching canonical identity, not a new identity generated during reserve. Exact schema/index compatibility must be proved before attachment.

## Owner review and request surface

Review/save is distinct from one-shot issuance. Review uses authenticated current owner dependency, exact nonsecret auth projection/document identity/password_updated_at, canonical owned Google account identity and current server draft/product evidence. It shows mode, market, current product/URL, campaign type Search/RSA, exact currency/time zone, explicit requested average daily budget, campaign/account commitment limits, valid interval, request identity and exclusions. All monetary fields require owner input; there are no financial defaults.

The owner must explicitly acknowledge: «هذه ميزانية جوجل اليومية المتوسطة وحدود التزامات الميزانيات، وليست سقفًا صارمًا للإنفاق الفعلي يوميًا أو إجمالًا». `AVERAGE_DAILY_COMMITMENT` and acknowledgment version/text digest belong to the reviewed digest. Hard daily/total spend requirements are unsupported and block this mode; they are not silently translated to Google average budgets. Profitability targets remain separately owner-defined and are not inferred from spend limits.

Configured save remains nonactive and does not consume the permanent slot. GET/review/readback never issues private continuations. Browser POST must use the existing authenticated owner/CSRF-or-existing-request-protection contract and strict expected review digest/revision/owner generation plus a server review nonce/admission identity; no bearer-like financial token is returned. The nonce has no authority without fresh authenticated validation.

Do not publish a standalone owner 'prepare now, resume later' button. That would consume the slot then lose a nonreconstructible in-process ACK between HTTP requests. The first producer slice remains internal/unattached and testable. Later, an explicit one-shot owner launch POST must execute preparation→reservation→floor→one provider request within a single owned orchestration lifecycle; the private continuations are never JSON, queued job payloads or persisted resumable credentials. Its button stays disabled until that complete path and prerequisites are proven. A worker may plan/reconcile but cannot reconstruct a lost continuation to launch.

## Fresh preparation before the transaction

Bounded native read/validate-only work precedes the TX: canonical Google identity/currency/time zone, complete native budget inventory/resource sharing/reference-count proof with strict500 bound, current Google conversion/geo/configuration and exact new-enabled graph validation; current canonical product visibility, inventory, URL, source revision and owner-reviewed product identity; current grant/draft/policy digests and nonsecret Google auth epoch. This pre-read accepts only the exact configured/unconsumed server review; it does not call an active-only authority reader by fabricating activation fields. Its private proof is minted by actual reader collaborators in the new producer module, then consumed by the preparation TX. Observation clocks start before the first slow read and cannot be renewed by a later step.

No native/provider HTTP occurs inside preparation TX. Raw JSON `complete`, `identity_verified` or validation flags cannot stand in for actual reader-produced private proof. Owner/password/OAuth drift between reads and TX must fail closed. Snapshot reads of OAuth/product/native evidence do not serialize external writers; this limitation is preserved.

## One preparation transaction and positive ACK

The producer uses the accepted retained, bounded mandatory transaction lifecycle, replica-set capability gate, snapshot read concern, majority+j write concern and exactly one explicit raw commit attempt. No standalone fallback, SDK automatic commit retry, DDL, collection auto-provisioning or provider call is allowed inside it.

Within the same transaction:

1. Read the common account guard, both existing policy records when present, the exact configured reviewed grant, server draft/product, original proposal identity/fence availability, RUNS slot and owner auth projection. Require fresh namespace readiness and current supported shapes; absent/incomplete legacy inventory is not empty evidence. Require no conflicting active claim/unresolved unmapped hold and strict100 allocation/500 native bounds.
2. Recheck expected grant revision/owner generation/review digest, explicit spend semantics, current validity/time zone/activation interval, native freshness, account ownership/auth epoch and complete graph binding. Any present policy's currency/zone/authorization digest is exact; common cap is the conservative minimum of explicit owner caps, including retained holds. No standing policy is invented for a legitimate creation-only case.
3. Acquire the exact existing management account fence under its unique user/account index, and create the immutable service-owned original new-campaign proposal in the same TX. An existing foreign or unresolved fence blocks issuance. This contract chooses creation of BOTH original proposal and fence in this admission transaction. Earlier owner preview is non-executable review data; it cannot preclaim an alternate native proposal/fence. An existing management proposal/fence is not adopted.
4. Apply the conditional users marker with exact _id/nonsecret auth projection/password stamp. A concurrent role/disable/password write conflicts on the same document. Fresh authenticated provenance is mandatory; users.update/validator failure aborts issuance, without adding that dependency to safety revoke/cancel paths.
5. Only for this explicit confirmed admission, set the exact grant's one-shot activation fields and `state=active,consumed=true,claim={proposal_id,execution_claim_id,preparation_operation_id}`; with activation_version=1, activation_revision=current grant revision, activated_by=current owner, activated_at=transaction time, activation_review_digest=exact review digest and activation_policy_binding=exact present policy authorization digests. This contract chooses the SINGLE combined admission TX, not a separate activation or ambient pre-activated grant. Save/configuration never calls it automatically.
6. Insert RUNS phase=prepared with immutable binding including the resulting guard epoch `old_epoch+1`, exact claimed fence and original proposal. Advance the common epoch and record this preparation operation marker. There is NO monetary allocation yet and no funded dispatch authority.
7. Recheck wall/monotonic deadline and grant/proposal authority time immediately before committing. After a positive exact commit ACK, synchronously check current clock/deadline/native observation age before minting a private `_InitiationAck` for the exact same DB object, owner/account/slot/proposal/claim/preparation ID/binding digest. The ACK is never minted from a cached operation result or majority readback. The ACK deadline cannot outlive the original reviewed validity.

The exact epoch/projection expected by current reserve must match: preparation writes epochN+1 and records N+1, reserve reads N+1 before its own epoch increment. A concurrent policy/revoke/common-guard change invalidates the preparation; it does not justify rewriting the stored epoch to the latest value. If existing common lifecycle supports cached positive results, the producer must explicitly reject those as issuance evidence, following the floors' consumed-operation behavior.

## Outcomes and continuation semantics

| Observation | Durable interpretation | Allowed next action |
|---|---|---|
| Proven precommit validation/conflict | No producer ACK; existing permanent state stays as observed | No automatic retry; owner may refresh review only under an explicit new admission policy |
| Exact commit ACK, fresh time and binding | Prepared slot consumed; no money allocated yet | Mint one private ACK in original task and pass directly to reserve |
| Commit ACK positive but expired/deadline crossed | Prepared remains consumed | No ACK, reservation or provider request; retain own fence pending positive resolution |
| Commit unknown/timeout/cancellation | RUNS may be absent or prepared; absence is not abort proof | No ACK/retry/reconstruction; retain in-flight resource slot and conservative account fence handling |
| Worker/process restarts or GET sees prepared | Durable evidence only | No new private ACK, reserve or launch |
| Reserve unknown | Prepared or reserved permanent journal; initiation burned | No reserve retry; hold/fence retained; read-only reconciliation cannot mint a floor ACK |
| Floor unknown | Submitted may have committed; reservation/hold remain | No provider HTTP, re-send or refund by inference |

The first unpersisted preparation cannot be distinguished from never-attempted preparation by later reads. There is no infinite recursive journal to conceal this. A browser retry/duplicate request must not silently become a new preparation attempt; process admission identity is consumed before the first await, and a future owner action explicitly chooses a new request identity only after existing fence/uncertainty resolution. This limitation must be visible, not labeled 'failed safely, retry now'.

Owner revocation/cancellation wins before reservation/floor only via the common epoch/proposal CAS. A positive logical floor before revocation is already in flight; do not claim it can be unsent. Prepared-only cancellation needs its own reviewed conditional transaction to mark the slot permanently terminal and invalidate any live private ACK through epoch/grant/proposal changes. No money existed at prepared, so this is not a budget refund. This proposal does not add a fence-delete/release shortcut; known-no-HTTP resolution must use an independently reviewed exact-own-record protocol. Unknown cancellation never releases by inference. OAuth-independent revoke/cancel remain usable when their existing supported transaction prerequisites hold.

## Prerequisites: separate contracts, no inferred readiness

| Surface | Required evidence before issuance |
|---|---|
| Existing bootstrap four targets | Standing policy, creation policy, common budget guard and technical engine probe collections: supported validator/options/shapes, exact existing unique indexes, complete bounded legacy commitment inventory and effective app privileges. Engine commit/readback proof alone is insufficient. |
| Six policy writers | Standing save/revoke/activate and creation save/revoke/activate all participate in the same mandatory common-epoch TX contract before ANY funded issuance. Production runtime flag remains false until actual namespace/legacy/native evidence and selective migration acceptance. Preserve OAuth-independent revokes; unavailable activation is not permission to disable existing revocation. |
| GRANTS | Supported configured/one-shot active/revoked schema, exact auth/review bindings; unique `(user_id,account_id,request_key)` named google_funded_grant_scope; effective find/insert/update plus actual validator compatibility. Bootstrap's four targets do not include it. |
| RUNS | Supported immutable prepared/reserved/submitted/settled shapes and permanent consumption; unique `(user_id,account_id,grant_key)` google_funded_run_slot and `(user_id,account_id,creation_identity)` google_funded_run_identity; no omitted/null identities; effective find/insert/update and validator compatibility; no TTL/autorelease. |
| Management documents | Actual proposal identity/idempotency and account fence unique indexes, draft read completeness, supported shapes/validators and find/insert/update for exact operations. Unknown metadata blocks. No opportunistic ensure_indexes in funded request path. |
| users | Existing exact owner auth projection and conditional marker supported by effective users.find/update and current validator; disposable tests do not establish production permission. No hash/secret projection, username/role metadata disclosure, RBAC or validator change is authorized. Safety paths do not gain a users.update dependency. |
| Native/shared exposure | Complete actual resource-linked inventory, current currencies/time zones, all existing allocations/holds and legacy unknowns, every automatic budget increase sharing the common guard. External Google writers are not serialized by Mongo; their race remains an explicit limitation and freshness/revalidation requirement. |

Provisioning, schema/index verification and read-only privilege audit remain separate from owner financial authorization. Successful create_index does not prove a collection is compatible, empty, safely migrated or authorized for business writes. Missing namespaces may not be implicitly created by the public handler. A readiness result is neither permanent authority nor a reusable activation token; recheck relevant contract at issuance and fail closed on metadata drift. Do not reveal database names, hosts, users, roles, raw privileges, tokens or secret config.

## Proposed exclusive files and staging

- NEW `google_ads_funded_creation_preparation.py`: authenticated review/admission helper and private original-task ACK issuer; no public attach in first implementation.
- Existing `google_ads_automation_budget_guard.py`: one new typed preparation command/transaction handler seam, explicit post-preparation epoch and no cached-ACK issuance. Existing six writer behavior and floors preserved until separately reviewed integration.
- Existing funded grants/authority: minimal typed adapter to exact current prepared journal projection; no generic activate flag and no new issuer in a read/get helper. Prove old tests plus missing-prepared/null-identity protections.
- NEW unit and disposable replica tests/workflow for preparation, independent reviewer required before root composes release source.
- Later NEW owner review component/service and strict routes, with root-owned two-line attach/import seams assigned only after runtime storage and full paid-gateway lifecycle are accepted. Root coordinates commercial/retail/Reports seams; no whole historical module copy.

The first implementation should ship no button that consumes admission until there is an accepted same-process consumer. It can remain an unattached helper tested against the real replica. Owner configured review UI may be a separate nonpositive slice. Root approval of this contract precedes any runtime code changes.

## Acceptance before positive attachment

- Exact authenticated owner/password stamp before capture and races before/after marker; stale auth, staff, disabled owner, unknown validator or denied users.update never issue; unrelated user fields unchanged.
- Absent/custom-validated namespaces, wrong/missing/nonunique/partial index, missing creation_identity and incomplete legacy inventory block without DDL. Auth-enabled disposable roles test exact permissions; no production RBAC changes.
- Two independent clients duplicate same slot across revisions: one preparation commit/epoch and one original-task ACK at most; loser no ACK. Same account different request keys cannot bypass the unique unresolved fence.
- Grant/review/proposal/fence/graph/resource/account/currency/zone/auth/product/native tamper and changes between pre-read and TX abort issuance; metadata/native external writer limitations explicit.
- Owner revoke/cancel before common write prevents ACK; after positive preparation invalidates reserve through common epoch; no OAuth dependency for safety paths. After logical floor never pretend 'unsent'.
- Three clock/deadline phases including late ACK; original deadline never renewed, no ACK minted after expiration, retained background work cannot open another admission slot.
- Actual replica failCommand91, connection close, applied lost ACK, cancellation and restart: exactly one commit wire attempt, zero provider writes; no cap from GET/readback/cached commit/absence; prepared permanent identity remains consumed.
- Preparation creates zero monetary allocation; reservation alone creates exactly one hold; pending/native existing standing and PAUSED/funded commitments use one conservative max per verified resource. No unknown release/refund/first-launch duplication.
- End-to-end future gateway must pre-acquire final paid client/auth/config, freshly bind authority, and after positive floor ACK perform only synchronous clock/runtime/permission checks then exactly one atomic brand-new ENABLED Search graph mutate without an intervening await/refresh. Existing PAUSED or manually stopped campaign enable/resume remains blocked.
