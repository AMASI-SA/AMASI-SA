# PAUSED creation: common reservation contract (design, not runtime)

This bounded follow-up closes the explicitly documented policy-local PAUSED
creation gap in PR1624. It grants no launch, enable, first-launch provenance,
automatic resume or external-provider writer fencing. Runtime remains literal
false; operational flags remain off. No live calls, credentials or production
configuration are required to implement or test this contract.

## Current evidence

`google_ads_automation_creation_authority.py` currently orders claim, whole
private ledger and generation on one creation policy document. Original receipt,
independent verification, ownership/correlation, and positively known no-campaign
proof decide settlement. Quota is never refunded. These invariants must survive.

The reviewed common guard currently covers all six policy writes and standing
reservation/settlement, but not these PAUSED claims. Its LaunchAllocation command
is storage-only and is not a substitute for this contract.

The real reader `read_google_creation_native_context` currently omits native
budget resource/shared/reference-count fields. Strict tests alone cannot supply
production proof. A bounded reader hunk must request required official v25 fields
and normalize them using the existing standing inventory contract. Malformed,
shared, missing, duplicated or conflicting native identity fails closed. No
planning behavior or funding authority is added by the reader.

## Atomic reservation

A distinct PausedCreationReservation command runs in the retained common guard
transaction, on the same session and epoch. It reloads persisted owner, exact
proposal/fence, saved intent/choice, canonical product and draft, plus current
policy/ledger/generation. It rechecks original proposal and intent bindings,
product/inventory freshness and existing policy/template/native requirements.

The command atomically writes creation claim, whole ledger, generation, bounded
common allocation and exact account active claim. Expected scope, revision,
configuration digest, generation and ledger are fenced. The amount comes only
from the explicitly approved creation policy. Reserved enable budget stays zero.
Idempotency derives from the existing creation identity, original proposal and
execution claim, never an invented campaign identifier or new authority token.

The existing native submission floor remains mandatory after acknowledged
reservation. A committed reservation alone is not permission to send an ad.

## Common commitments

Use only explicit owner `max_account_daily_budget_native` limits of applicable
present policies, with a conservative minimum where both apply. Do not invent a
limit from creation counts, daily quota, missing fields or a default. A legitimate
creation-only account does not require a standing policy. Each present policy's
scope, currency, timezone and binding must be proved; ambiguity fails closed.

A fresh complete native inventory is bounded to 500 campaigns. Active allocations
and creation ledger are each bounded to 100. Count native ENABLED commitments,
unreflected standing reservations, and PAUSED creation holds once. Retain existing
reserved/owned creation holds even after revocation. Every such ledger entry must
map to exactly one proved allocation; unmapped legacy state blocks new claims.
No synthetic migration or silent omission turns legacy exposure into zero.

A known created PAUSED campaign still holds its approved amount. A later complete
inventory can count that same positively identified ENABLED campaign natively
instead, once, after exact budget-resource/amount/currency proof. REMOVED can stop
a hold only through positive verified native evidence for the exact original
resource; absence, not-found, filtered inventory, network errors and timeout
cannot release it. Existing readers excluding REMOVED simply cannot prove that
release. Google UI and other external writers cannot be serialized by Mongo;
this limitation remains explicit.

## Atomic settlement and cancellation

PausedCreationSettlement reloads the original saved intent, proposal, fence and
claim inside the same guard transaction. Existing independent readback,
resource coverage, correlation-label and ownership proofs are preserved. Only
those proofs can map an allocation to a created PAUSED campaign and its original
verified native budget resource. Missing or contradictory mapping fails closed.
The transaction clears only its exact account claim and updates creation ledger,
generation and common allocation together. It never adopts a manual campaign.

Positively known no-send/cancel/rejection with no accepted or partial receipt can
record no_campaign/settled. Quota and consumed identity are not refunded. The
unclaimed cancellation barrier is also a common-epoch transaction, so a delayed
authorizer cannot race an acknowledged cancellation. It grants no authorization
or fabricated monetary allocation.

Unknown provider send, commit ACK, saved receipt or readback retains the original
reservation, claim, identity and native fence. No resubmit, speculative refund,
non-atomic fallback, cleanup-success downgrade or automatic retry is allowed.
Reconciliation must prove the original durable operation and terminal outcome.

## Lifecycle and owner controls

Use the reviewed retained original-task/CSOT lifecycle, public explicit raw commit
ACK, bounded per-process pending registry, original deadline and no cancellation
of an in-flight driver Future. Known precommit abort and ambiguous attempted
commit remain distinct. Cleanup does not establish a lost commit result.

Save/activate/revoke remain the mandatory common policy adapter. Revocation needs
Mongo capability and persisted owner authentication, not OAuth or a provider call.
No diagnostic result is a reusable migration or execution capability. Production
commit, namespace/validator/index permissions, legacy reconciliation and current
native budget evidence remain rollout blockers even if fixture CI passes.

## Owned paths and acceptance

Own common guard, creation authority/settlement, a narrowly scoped helper if
needed, the actual native reader hunk, dedicated tests/transactional fake and CI.
Keep retail/current resume behavior and native gateway submission semantics.

Acceptance must cover actual SDK reader required fields; creation-only and
cross-policy caps; standing/creation/revoke races; missing legacy mappings even
when revoked; exact PAUSED native ownership; ENABLED reflection without double
counting; positive REMOVED versus absence; bounded ledger/allocation/inventory;
old generation and identity replay; canonical product/draft drift; offline OAuth
revocation; commit-close/server errors and one-wire commit; unknown provider
receipt/dispatch retention; cancellation barrier and no quota refunds. Use real
replica tests with controlled concurrency and fault injection, plus independent
review before acceptance. No production call is part of fixture verification.
