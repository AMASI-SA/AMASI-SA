# Owner-triggered Google namespace bootstrap

Status: approved implementation contract; no implementation, production mutation,
or live DDL claim is made by this checkpoint. Base: `08160c883a040f3a9fb49a165dab8074d4ef7309`.
The coordinator explicitly authorized isolated new files and a separate technical
reservation in the existing receipt collection. Existing diagnostics, policy
writers, owner receipts, release intent, UI and route attachment remain unchanged.

## Observable result

An explicit authenticated owner request may create only a freshly proved-absent
subset of the four fixed namespaces below, with their required indexes, in one
replica-set transaction. Existing compatible empty namespaces are preserved.
Any existing nonempty, custom, incomplete, inaccessible or unknown namespace
blocks the entire operation before target writes. A completed operation is
observed without repeating DDL. An ambiguous operation remains reserved across
process restart and can only be inspected; absence of evidence is not an abort.

| Label | Fixed namespace | Required indexes |
| --- | --- | --- |
| Standing policy | `mezan_google_ads_automation_policies_v1` | Ordinary `_id_`; unique ordered `(user_id:1, account_id:1)` named `google_automation_tenant_account` |
| Creation policy | `mezan_google_ads_automation_creation_policies_v1` | Ordinary `_id_`; unique ordered `(user_id:1, account_id:1)` named `google_creation_tenant_account` |
| Account guard | `mezan_google_ads_automation_budget_guards_v1` | Ordinary `_id_` only |
| Engine probe | `google_ads_budget_guard_engine_probes_v1` | Ordinary `_id_` only |

Collections use ordinary defaults, including simple binary collation. No custom
validator, TTL, view, capped, timeseries, clustered, encrypted, sparse, hidden,
partial or unknown index/collection contract is adopted. No index is repaired,
dropped, renamed or rebuilt. No data document is inserted into these four targets.
The technical reservation is separate from these target namespaces.

## Public seam and authorization

Implement a standalone, initially unattached route attachment function in a NEW
module. Owner review/preflight and execution use explicit POST requests; receipt
inspection uses GET. Request models forbid extra fields and never accept database
names, collection names, indexes, validators, budgets or financial flags. Execution
binds an explicit acknowledgement, request identity and server-generated manifest
and observation digest. Preflight is read-only and is not authority by itself.

Validate the authenticated dependency and a bounded unique fresh database owner
before any reservation, including active/disabled flags, owner role and exact BSON
`password_updated_at` binding. Repeat the owner fence before commit and before a
successful response. Missing, duplicate, changed or unavailable owner evidence
fails closed. No OAuth or external provider request is necessary or permitted.
Do not claim continuous authorization or future database privileges from these
bounded observations.

## Complete preflight

Use PRIMARY only and a verified ordinary replica set, not standalone or mongos.
Require complete metadata for all four exact names using `listCollections`,
`nameOnly:false`, no authorized-only/name-only fallback, a bounded batch and a
terminal cursor. Validate every returned row, unique exact name, UUID, collection
type, options and `_id` metadata. A missing name is proved absent only when this
full authorized response is complete. Read complete `listIndexes` for every
existing target; reject unfinished index builds and unknown contracts. Close only
cursors created by this operation and retain an uncertainty blocker on failed
cleanup rather than opening additional cursors blindly.

Prove an existing target globally empty using bounded PRIMARY majority `find:{}`
with projection `_id`, `limit:1`, `singleBatch:true` and a terminal cursor. A row
belonging to any owner blocks bootstrap. The old audit's owner-filtered zero rows
and its engine-probe `inventory_complete:true` are not global-empty evidence.
Recheck UUID/options/indexes and data at the operation fences. Record observations
as bounded observations, not a simultaneous catalog snapshot. Catalog commands
cannot run in a transaction and do not acquire majority snapshot semantics merely
because document reads use majority.

Check fresh effective privileges and actual access, including complete metadata,
global target find, target insert, the two policy createIndex actions, and the
technical receipt's find/insert/update actions. Ordinary `create` accepts target
insert privilege; do not demand admin/dbOwner or invent a createCollection
requirement when insert is sufficient. Successful old technical probes do not
prove these privileges remain available.

## Durable admission and atomic DDL

The existing `google_storage_commit_probe_receipts_v1` collection may hold one
disjoint database-global bootstrap record. Its fixed `_id`, operation kind and
schema are separate from all existing `owner_...` diagnostic receipts. Preserve
existing receipt bytes and old GET/POST contracts. Prove this receipt collection
and its `_id` contract suitable before writing; do not create or repair it as an
implicit bootstrap side effect. All DB target names are constants.

Insert an exact reservation with majority+j acknowledgement and majority readback
before any target DDL. Bind owner/auth state, request nonce, manifest version/digest,
observed metadata digest, target plan and attempt identity. The database-global
unique `_id` serializes separate processes and owners. A process-local lock is only
an additional resource bound. No TTL or elapsed-time release is allowed.

Start one manual transaction with PRIMARY, read concern `local`, majority+j commit
write concern and bounded commit time. CAS the exact reservation. Create only
targets that the fresh full preflight proved absent, then create the two required
policy indexes while their collections are new and empty in that transaction.
Existing exact empty collections receive no DDL. Recheck data and owner before
commit, and set the reservation to an applied marker inside the SAME transaction
as DDL. Do not use retrying transaction callbacks or application resubmission.
Send one commit command; no nontransactional fallback.

Mongo `create` returns success for an existing collection with identical options.
Therefore a successful create is not evidence that this attempt created the name.
Actual-Mongo races must prove that foreign-create, inserted-row, custom-options,
missing-index and drop/recreate changes cannot be adopted or produce false
verification. Native transaction restrictions prevent a new index being added to
an already existing old collection. They do not replace catalog/UUID/data fences.
Do not claim untested race behavior from documentation alone.

An existing empty policy collection with a missing index is NOT eligible. Mongo
cannot add a new index to such a collection within this transaction. Leave it
unchanged and report the concrete blocker. No dropping, migration, legacy adoption,
nontransactional repair or expanded writer barrier is authorized by this slice.

## Readback, uncertainty and resource limits

After an acknowledged commit, use a fresh session for PRIMARY majority receipt
readback and complete PRIMARY metadata/index/UUID plus global-empty observations.
Require exact applied identity and all contracts. Only after those pass may an
exact CAS record verified completion. Read back completion before reporting it.
No cleanup operation deletes target data or alters indexes. The receipt is retained.

Lost acknowledgement at reservation, DDL, commit or final receipt, timeout,
cancellation, uncertain cursor/session cleanup or unknown server outcome keeps
admission blocked. Never resend DDL or a commit to guess the result. GET can
recognize an exact atomic applied receipt and expose the current bounded metadata
state; a missing/apparently reserved receipt is not proof of abort. Pending work
cannot be automatically reopened by another POST or a process restart.

Use explicit overall and per-command bounds. Preserve ownership of any still
running task until it actually terminates; no detached overlapping retry. Bound
cleanup separately and report cleanup uncertainty. Sanitize errors and responses;
do not emit connection strings, role/user names, environment values or raw server
exceptions. Test cancellation at the real async seams.

Every success remains storage-only: `execution_authorized:false`,
`funding_ready:false`, no policy activation/configuration proof, no future permission
guarantee and zero provider writes. No advertising or financial document is
written. The six policy routes, launch worker and financial flags remain untouched.
New funded GRANTS/RUNS namespaces belong to the connection task and require their
own later provisioning contract; they are not implicitly covered here.

## Required evidence before implementation acceptance

First capture actual authenticated Mongo 8.0.26 DDL capability/permission tests in
dedicated CI, before implementing the production module. Test absence creation
and unique indexes in one local-read-concern transaction, rollback, rejection of
snapshot DDL and new indexes on old empty collections, and target insert plus
createIndex permissions without dbOwner/createCollection grants. No production or
host DB calls are authorized. Loopback disposable test DB mutations are CI only.

Then cover the actual public seam and persistence with:

- Four absent names, mixed absent/compatible empty names, no-op completed receipt.
- Every target containing another owner's row, engine-probe row, custom metadata,
  incomplete metadata/cursor, old missing index and inaccessible metadata. Preserve
  exact existing documents, options, indexes and UUIDs.
- Authenticated restricted roles missing each required action and authorized-only
  metadata fallback refusal; exact owner disable/role/password before/during races.
- Two independent callers/processes contending on one receipt; one DDL attempt.
- Foreign same-options/custom-options create, foreign insert, UUID replacement,
  and old-empty missing-index races after preflight, using actual server behavior.
- Failure after each create/index rolls back all target changes; command listener
  proves no outside-transaction DDL and no provider/policy writes.
- Lost ACK at reservation, each DDL, commit and completion, with simulated process
  restart. Distinguish commit-before-lost-reply and abort-before-lost-reply; neither
  causes application resend or guessed release.
- Timeout/cancellation/session cleanup behavior, unchanged existing diagnostic
  receipts and original namespace/commit diagnostic regression tests.

Dedicated workflow must assert exact collected/pass counts, named real-Mongo cases
and zero skipped/failed/error cases. Functional CI is distinct from release
eligibility: root separately owns integration, source A/intent-only B, original
build, Release Guard, publication and live behavior acceptance.

## Primary sources read during design

- MongoDB v8.0 Transactions: https://www.mongodb.com/docs/v8.0/core/transactions/
  (explicit DDL uses local; new index only absent/new empty collection; no catalog
  list commands inside a transaction).
- MongoDB v8.0 create: https://www.mongodb.com/docs/v8.0/reference/command/create/
  (identical existing options return success; ordinary create permits target insert).
- MongoDB v8.0 privilege actions: https://www.mongodb.com/docs/v8.0/reference/privilege-actions/
  (createIndex distinct privilege).
- MongoDB listCollections: https://www.mongodb.com/docs/manual/reference/command/listCollections/
  (full metadata permission, authorized-only fallback, UUID).
- MongoDB listIndexes: https://www.mongodb.com/docs/manual/reference/command/listIndexes/
  (hidden and in-progress indexes are returned).

These sources establish design constraints, not empirical proof of the future
implementation or current production capabilities.
