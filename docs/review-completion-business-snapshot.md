# Review Completion business snapshot and bounded recovery

This change is stacked above frozen Draft PR #1264, commit
`c3d5f366d77248b20f905e385ba9bd2800a036e9`. It does not authorize merge,
deployment, Salla calls or recovery of Production orders.

## Approval contract

### G47 prerequisite ordering

For a new completion operation, the existing read-only
`legacy_component_cohort` prerequisite check runs inside the claim transaction
before `build_snapshot`. Missing canonical evidence retains
`component_canonical_order_required`; missing/invalid provider creation evidence
under configured G47 retains `component_source_created_at_required`.
The returned cohort does not bypass Business Snapshot: legacy orders still
require their source evidence, and missing source is rejected before operation
insertion or provider I/O. Resumed approvals retain their immutable snapshot and
existing validation, acceptance, lease and transaction fences.

The legacy success regression supplies complete synthetic provider facts matching
its DTO and retains review/scan/pack/no-component/no-financial-effect assertions.
A separate negative regression proves legacy with missing raw source cannot
create an operation, workflow or event or call the provider. These fixtures are
contract tests, not Production evidence or a live Salla replay.

New durable operations retain a versioned business snapshot before provider
I/O. Schema and normalization versions are explicit. The original approval
snapshot is immutable across retry. Source and DTO facts, acceptance settings
and approval identity bind the snapshot to the existing operation. Integrity
hashes detect changed evidence; hashes alone neither authenticate evidence nor
reconstruct an absent approval snapshot.

Only established aliases normalize. Conflicting aliases fail closed. Changes
to products, SKU, quantity, options/selections, customer, shipping or payment
facts remain conflicts. Unknown source paths retain fingerprints and changes
are rejected as `unknown_source_change`. Normalization must not drop an unknown
field to make an operation complete. Cancellation, component source/generation,
revision, authorization and acceptance fences remain authoritative. Acceptance
configuration changes retain `409 component_acceptance_changed`.

Mismatch diagnostics describe schema versions, hashes, categories and field
paths, not raw customer values or credentials. They must survive a failed local
transaction without allowing a stale lease holder to overwrite a successor.

## Recovery boundary

`backend/order_review_recovery_guard.py` assesses explicit existing operations.
It does not discover orders or expose a Production execution CLI, HTTP route or
background job for `requires_review`. The allowlist is exactly:

```
291715477 291703306 291967952 291717149 292127659
291717456 291702542 291914588 291507628
```

Recovery requires the original operation identity, complete immutable approval
evidence, provider confirmation, authoritative current source and current
acceptance/component/workflow checks. Its two decisions are `SAFE_TO_RESUME`
and `REQUIRES_REVIEW`. `INSUFFICIENT_EVIDENCE` is an evidence classification,
not a third decision. It always produces `REQUIRES_REVIEW`.

An offline safe assessment is conditional on the supplied evidence. It is not
a recovery authorization token or proof that Production is currently unchanged.
The completion engine must reload evidence and recheck every guard under its
existing transaction and lease. Successful recovery uses the same operation and
approval identity, commits workflow/event/result atomically and forbids provider
transport. It must never create a new Review or change Salla status.

Old operations lacking the complete original snapshot are deliberately refused.
Attaching a current snapshot as though it were the original approval is not a
supported migration or recovery strategy.

## Offline analyzer

`scripts/review_completion_recovery_analyze.py` reads a local JSON evidence file
and prints a sanitized assessment. It has no database connection, remote fetch
or execution capability. Complete case envelopes contain these objects:

```
operation, request, source, order, acceptance, workflow, component
```

`order` is serialized OrderDTO evidence. `workflow: null` explicitly records
that no workflow exists; an omitted workflow field means missing evidence.
Empty objects are allowed as inputs but
do not constitute sufficient evidence; the assessor rejects missing facts. Do
not populate missing fields from guesses. A saved-hash-only case list is also
accepted and produces refusal without creating synthetic operation documents.

Only order/operation identifiers, fixed decision/reason labels and counts are
printed. Customer payloads, snapshots, exception text, tokens and credentials
are not printed. Duplicate order evidence is rejected instead of selecting an
arbitrary row. Orders outside the explicit allowlist cannot be authorized.

## Nine real saved cases: refusal rehearsal

The sanitized evidence classification is in
`operations/review-business-snapshot/CASE_ANALYSIS.json`. Its source is the
previous local `case-analysis.json` evidence artifact: five saved operation
observations from the earlier tool output and four saved operation objects,
matched by order number and deduplicated. Only stored hashes and classifications
were available in that artifact; complete approved source/DTO pairs were absent.

No original operation IDs are invented, and no stored hash is presented as a
freshly recomputed hash. All nine remain:

* evidence: `INSUFFICIENT_EVIDENCE`
* decision: `REQUIRES_REVIEW`
* safe to resume: **0**

This is a rehearsal of refusal for missing evidence, **not a successful recovery
or Production replay**. Synthetic complete-evidence Mongo tests prove the code
contract separately; they cannot establish that any historical order is safe.
In particular, `company/company_name` is not asserted to be the proven cause of
any of the nine cases.

## Before any later Production recovery

Obtain complete provenance-linked approval evidence and authoritative current
snapshots through a separately authorized channel. Reassess each exact operation
offline, retain per-case reasons, and obtain separate explicit approval before
any execution. Missing evidence remains a refusal. Even after approval, use only
the nine-case allowlist, the original operation IDs and the existing transactional
completion path; verify workflow/event uniqueness and `/reviewed` visibility.
No broad backfill, migration or order discovery is part of this change.

## Verification scope

Required isolated tests cover equal representations; all named real business
changes; unknown fields and alias conflicts; acceptance 409; same-operation
recovery; insufficient-evidence refusal; worker/manual concurrency; restart,
timeout and commit failure; and `/reviewed` visibility before and after atomic
completion. The implementation report must distinguish fresh test results from
this specification. This document does not claim that tests have run or that any
Production case recovered.

## Schema and field policy

`schema_version=1` and `normalization_version=1` identify the new saved
business snapshot; they do not redefine #1264's fingerprint version 2 or the
unversioned legacy fingerprint. The stored facts contain source, OrderDTO,
acceptance and approval identity. Typed numeric serialization preserves Decimal
precision and distinguishes booleans, numeric strings, missing keys and nulls.
Known aliases are exactly those established in #1264. Unknown root, nested and
status metadata are fingerprinted; unexpected shapes in transport fields are
unknown as well. Canonical hash covers both facts and unknown fingerprints.

Known status/ingestion projections are separately checked or excluded by an
explicit inventory in `order_review_business_snapshot.py`; new fields cannot
silently enter that inventory. Source/DTO customer, item, shipping and payment
facts remain guarded. Unknown values are not retained as plaintext. Snapshots
are internal order evidence, not a public API projection; their hashes are not
encryption and require the same restricted access as order data.

Completion increments a field on the unified order document inside validation,
so a direct source writer which bypasses owner serialization still conflicts
with the transaction. Acceptance retains its existing configuration fence.
Component semantic source evidence is frozen before provider I/O. A status-only
ingestion can advance a transport timestamp/generation with identical semantic
facts; the fresh component acceptance ticket must still match the current
generation and source revision inside the completion transaction. Changed
semantic component facts or unexplained generation changes are rejected.

Explicit recovery parks its claimed operation in `requires_review` until atomic
completion; a crashed local-only recovery cannot fall back into a provider-capable
worker. Recovery never invokes the provider callback, rewrites confirmation time,
or invents approval evidence. Existing routing to `ready_to_ship` is preserved;
recovery does not force an ineligible order into the reviewed catalog.

## Current evidence limitation

A second inspection of the saved transcript found only collapsed viewer records
for the nine operations (acceptance objects and item arrays omitted), plus hashes
and runtime key lists. There are zero complete original operation documents in
the available offline artifact set. Therefore neither a successful recovery nor
a full-document Mongo rehearsal of these nine is claimed. Complete synthetic
fixtures exercise recovery; the real summaries exercise refusal only.
