# Salla webhook recovery and daily update-only refresh

## Problem and scope

The public verified webhook route awaited business processing before answering
Salla. Under slow transactions, repeated deliveries repeated that work while
health requests shared the affected web process. The platform incident report
and the local HTTP regression identify this blocking acknowledgement boundary.

The customer-choice recovery in PR1923 adds a second owner transaction for a
stale creation event. That can add contention, but the blocking ingress and
post-processing capture hash predate it. There is no causal evidence that
reverting that fix alone would prevent the incident. This change retains it.

The requested change also replaces the twice-daily Salla reconciliation with
one update-only cycle at **10:00 Asia/Riyadh**, covering the fixed window from
yesterday 10:00 to today 10:00. Immediate verified webhooks continue throughout
the day.

## Durable acknowledgement and processing

Business events are validated and encrypted using the existing private-payload
key, then inserted atomically into `salla_webhook_inbox`. A stable semantic hash
retains merchant, event, timestamps and customer choices; credential fields do
not alter identity. An HTTP success requires acknowledged **majority+journal**
persistence. A persistence/encryption failure returns a retryable 503. Invalid
metadata is 400; oversized accepted payloads are 413. Existing application
installation/authorization lifecycle handlers retain their direct path.

The existing separate background process owns execution. A renewable global
lease serializes consumption across replicas; each item also has a claim token.
The worker rechecks ownership after claim and before settlement, renews leases
during processing and cancellation cleanup, and retries incomplete work with
backoff. The defaults are a 45-second processing deadline, a 90-second lease,
10-second heartbeats, and eight attempts before quarantine. Quarantine retains
the encrypted payload for diagnosis rather than silently discarding it.

Successful items discard their encrypted payload but retain the deduplication
tombstone. A duplicate delivery cannot reset backoff, reopen a completed item,
restore discarded private content or change the pinned owner. A merchant
rebound to another owner is quarantined. Unbound events retry until first
binding can be durably pinned.

Recovery is **at least once** if a process dies after a business write and before
settlement. Existing source revisions, owner transactions and idempotent
outboxes remain authoritative. The worker never deliberately releases a claim
while cancelled business work is still running. A handler that refuses
cancellation indefinitely requires process recovery; falsely releasing its
lease would permit overlapping work.

Core processing failures are audited with fixed diagnostics and then surfaced
to the durable worker. Optional V3 shadow/analytics failure does not replay
successful operational work. A previously failed CAPI local-outbox enqueue is
recovered from current owner-bound canonical state if the original snapshot
has since become stale; current cancellation eligibility remains authoritative.
No new advertising or accounting sender is enabled.

## One daily refresh

A cycle starts between 10:00 and 10:05 Riyadh. Its order and catalogue steps may
resume within that admitted cycle before 11:00. Real failures allow at most
three attempts per step, each bounded to 12 minutes. The data window remains
fixed across attempts. Restart does not repeat a completed day, and a missed
cycle does not start an off-hours catch-up. Legacy half-day pending windows are
not adopted into the new schedule.

Manual orders, manual product imports and both scheduled steps acquire the same
owner lease plus the legacy catalogue lease. This also excludes a catalogue job
still running in the previous version during rollout. Manual work has a
20-minute deadline below the 30-minute lease. Completion, failure, cancellation,
timeout and ambiguous partial acquisition release only the matching tokens.

The scheduled writer checks that an order exists inside the owner/source
transaction. It cannot insert a missing order or seed a missing product.
Unchanged accepted source is a no-op without timestamp, accounting-marker or
watermark churn. Sparse data preserves richer customer options. Changed facts
retain normal billing eligibility, currency evidence and accounting follow-up
markers. Scheduled refresh does not auto-route preparation, observe delivery
or trigger a carrier handoff transition.

## Provider coverage limitation

[Salla List Orders documentation](https://docs.salla.dev/orders/list) documents
creation-date and reference filters; it does not establish `updated_at_gt` as
an accepted modified-since enumeration contract. This is also recorded in the
repository's `docs/salla_orders_v3_shadow_release.md`. Background Order Details
calls are excluded because the existing contract records their potential to
mark orders read. The implementation does not invent either capability.

The bounded order refresh discovers recent creations and existing orders with
verified local webhook/source activity, then uses the documented `reference_id`
lookup for older candidates. At most 500 candidates are considered. A write
requires the provider's **order-level** `updated_at` inside the fixed window.
Creation time, customer update time and local receipt time do not substitute
for that authority. Missing or ambiguous timestamps are counted and skipped.
The catalogue similarly requires explicit provider update timestamps and only
loads detail for existing, eligible products.

The order result therefore reports `coverage_complete=false`,
`coverage_reason=provider_modified_since_filter_unavailable` and a partial
status. Changes to old orders with neither a delivered webhook nor local source
evidence are not claimed as covered. This limitation alone does not cause
repeated sync attempts. Once admitted through the durable ingress, verified
events remain recoverable independently of the daily refresh.

## Verification and operations

The focused workflow is `.github/workflows/salla-webhook-recovery.yml`. It runs
the actual route, encrypted queue, process-death recovery, real Mongo owner
transactions, daily schedule, update-only behavior, customer choices and
outbox regressions, and rejects skipped acceptance cases. The existing
background-worker workflow also verifies actual canonical child startup and
health responsiveness. See `STATUS.json`, the evidence directory and the PR
for exact accepted source and results.

The authenticated webhook monitor now exposes a `queue` summary scoped to the
current owner and connected merchant: unfinished states, oldest backlog age
and expired processing leases. It does not retrieve payloads or scan completed
tombstones. Worker retry and quarantine messages contain fixed codes and counts.

## Release handoff

This task has not changed production. The previous PR1932 lease was closed by
its owner, and PR1933 was subsequently published and verified. Continuation on
2026-11-15 directly observed the shared `/app` guard inactive, a clean tracked
worktree at published `9afb38cf0dc0d8e8cd887f2c2cdb4a6be5a7ca68`, and the
matching verified live source `fc5ff89f5794061bf62a7de283eedc9ecd4ec7c9`.

The old accepted implementation and evidence branches remain intact. The new
release branch carries the exact approved code/test/workflow changes on the
published base, preserving all PR1933 changes. It uses a fresh single-parent
source so the full-history release-intent provenance stays valid. The prior
test receipts describe the old source; new exact-source gates are required.

Recheck the actual shared guard before workspace mutation. Pass the final
source gates, then prepare the new source A / intent-only B
pair through protocol v5. Do not squash/rebase the frozen pair. A separately
authorized single publication must be followed by all three official identity
probes and an inactive guard status. Read-only production verification should
also confirm healthy responses and the new worker/queue diagnostics; the next
10:00 Riyadh cycle is the first live scheduling observation.

Issue #1006 has reached its comment limit. This task's PR and status document
are the durable continuation fallback.
