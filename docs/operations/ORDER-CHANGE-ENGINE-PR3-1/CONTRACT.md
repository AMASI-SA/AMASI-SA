# PR3.1: source change hold contract

Stacked on PR #1274, HEAD `20abc3136b7b793e5dc009f269d0387efc44ec59`.
This revision changes the PR3 replay projection in its own branch, not PR #1274.
PR1 and PR2 implementation files are unchanged. PR4 is not implemented.

## Scope matrix

| Source evidence | Hold projection |
| --- | --- |
| Complete unambiguous ADD, pending application, unused item identity | One item-scoped ADD_CHANGE_HOLD per expected unit |
| Complete confirmed edit/delete | Item-scoped SOURCE_ITEM_CHANGE_HOLD for old item |
| Confirmed replacement | Item stops for both old and new identities; no application |
| Quantity/commercial/financial/order cancellation or reopening | Existing conservative order barrier |
| Incomplete/equal-version conflict/no baseline/uncertain execution/locked stage | Existing conservative order barrier |
| ADD reusing operational or historical item identity | Conservative order barrier; no guessed generation |
| ADD expansion exceeding safe hold scan budget | Conservative order barrier; no partial hold expansion |
| Existing manual, PR2 or historical PR3 order barrier | Preserved byte-for-byte; never converted or released |

Malformed schema remains rejected by PR3 validation; no units can be applied from
it. Valid but incomplete or ambiguous source evidence receives a conservative stop.
An existing conservative singleton is reused without overwriting its original
change identity. Every new event records the exact hold IDs that guard it.

## Identity and PR1 compatibility

ADD records have contract_version=5, authority=order_change_pr3_1,
hold_kind=ADD_CHANGE_HOLD, scope=item, target_id=order_item_id. Their deterministic
IDs bind tenant, order, change_id, order_item_id, unit_index, generation and revision.
Unit indices start at one. Generation zero is used only for previously unused item
identities; existing pieces (including archived), workflow lines, component units,
and historical item holds prohibit treating a reused identity as fresh.

The numerical generation of the future unit is separate from source_generation,
which preserves PR1's digest of the order/source generation. Revision is the
workflow revision at intake, not the source timestamp. PR4 must validate both the
stored identity and fresh current fences; no stale-preview authorization is implied.

PR1 already understands item scope. An old unrelated piece can execute while ADD
is pending. The new item is blocked even before it has physical pieces. Whole-order
operations still reject any active hold, so final assembly/shipping cannot silently
omit a pending item. No PR1 guard is bypassed or changed.

## Release boundary

There is no release endpoint, update, deletion or unit writer in PR3.1. Generic PR1
resume rejects both PR2 v3 and source v4/v5 holds. add_identity_matches is a pure
exact-reference predicate: change_id, order_item_id, unit_index, generation,
revision and types must match; authority/kind/scope/version/status must also match.
It does not authorize release and does not accept a caller-supplied release role.

Only a separately reviewed future PR4 may add the release writer. It must validate
tenant/actor, current revision/source generation, conflicts/cancellation and every
other active hold, then atomically create complete units/options/components/
allocations/assignment/audit/outbox before releasing only exact matching ADD rows.
Matching this predicate alone is insufficient. No future PR4 behavior is claimed
tested by this PR. Manual and PR2 barriers are independent and remain effective.

## Atomicity and retries

Existing operational_owner serialization commits holds, revision, immutable events,
audit/outbox and idempotency results together. Same transport replay returns the
original result before fences; changed payload with same key conflicts. Semantic
replay emits no duplicate holds. Optional paired expected_revision/expected_generation
are validated within the transaction; stale supplied fences roll back without writes.
Omitted fences retain trusted offline source replay ordering, not a public apply API.

No live webhook registration, Salla request, accounting/invoice writer, migration,
Review Completion change, stock allocation or fulfillment unit creation is introduced.
Both existing rollout flags remain OFF by default. Existing historical barriers
remain conservative even after a newer complete snapshot arrives.

## Verification and rollback

Real isolated MongoDB 8.0.12 replica set, no application DB fallback. Acceptance
covers ADD quantity/variant/options, item isolation, manual+PR2 stops, concurrent
ADD/duplicates, ADD+DELETE, stale fences, incomplete/reused identity, safe edit and
replacement projections, order cancellation, active execution, scan bounds, exact
identity mismatch, generic resume rejection, old global barriers, unchanged units/
allocations/financial sentinels, and Mongo validation failures on event/outbox/intake.

Disable new source replay via existing flags to stop new writes. Preserve every
committed hold, event, outbox and idempotency record. Do not migrate old v4 order
barriers or release v5 through rollback. PR1 retains enforcement after flags disable.
Transaction failure leaves pre-existing state intact and requires no recovery.

No merge, deploy, recovery, Salla mutation or Production write authorized.
