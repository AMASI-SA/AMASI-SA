# Qoyod header manual local count

Task: QOYOD-HEADER-MANUAL-20261108
Repository: AMASI-SA/AMASI-SA
Branch: codex/qoyod-banner-manual-refresh-20261108
Base: 835703d4b24c1c93da3fd91be74a21720da8f6cc (hotfix/prod-snap-meta-final)

## Result
Header reads one small owner-scoped saved document on mount, without timers or automatic retries. Only an explicit click computes a new count. The POST scans projected local order/invoice fields in batches of 100, with a distributed lease, 60-second cooldown, 30-second timeout and fenced publication. It never calls Salla/Qoyod or starts a reconciliation/sending job. Local-only evidence is labeled with calculation and invoice sync times, and never promoted to verified provider absence. Existing provider security-owner bindings, scheduler and sender remain unchanged.

Count matches invoice-review eligibility and exact real invoice references irrespective of invoice issue date. Canonical delivering alias added to existing invoice-review predicate. Owner/employee authorization and cache isolation preserved. Failed updates retain the prior count; frontend late responses, logout/owner changes and duplicate clicks are guarded. Review link opens invoice-review rather than verified-absence exceptions.

## Verification
- 135 backend tests passed: manual header summary, invoice review, historical header summary, inherited security authorization/Qoyod, scheduled incremental.
- 15 frontend tests passed in QoyodUnsentHeaderAlert, legacy named header regression entry, Layout deferred workspace suites.
- git diff --check passed.
- Independent read-only implementation review found no blocker; added timeout and superseded lease tests as requested.
- Two background-scan tests initially failed due to the host resource governor observing memory pressure. All 135 passed using a synthetic low-pressure cgroup fixture via MEZAN_CGROUP_ROOT (test process only). No production guard changed.
- Tests use mongomock/ASGI and React DOM. Real Mongo lease behavior, governed build, CI and deployed runtime are not yet verified.

## Handoff
Production changed: no. No provider calls, invoice sends, credential changes or deployment.
Next: review remote patch/CI, run governed v5 release preparation only under an explicit deployment authorization and existing release protocol. Do not represent a local mirror count as live provider truth. The first click creates the saved header count; mount never computes it.
Exact checkpoint SHA is recorded by the task branch/PR (not self-referenced in this file).

## Follow-up: security binding recovery and unknown results (2026-11-08)
User authorized fixing the blank/error pages and subsequently sending all remaining eligible invoices. No invoice has been sent during this follow-up. The screenshot's 670 is local unmatched evidence, not certified provider absence.

- Added operator-only `scripts/qoyod_security_owner_binding.py`: dry run by default, explicit independently verified owner id, exact expected old binding (or expect-unbound), active owner validation, atomic compare-and-set plus embedded audit and mandatory approval reference. Does not infer ownership, expose an HTTP self-claim endpoint, weaken authorization, alter credentials, or create financial records.
- Reconciliation GET/refresh exposes `snapshot_available`; unpublished is not equivalent to verified empty.
- Exceptions/reconciliation suppress false zeros and green empty success when no complete result exists. Failed refresh retains results as stale and stops polling; owner binding errors are explained in Arabic on all three pages.
- Fresh verification: 145 backend tests passed (same test-only low-pressure cgroup fixture as above); 37 frontend tests across six suites passed. No real Mongo/runtime/provider or deployment verification.
- Production changed: no. Environment has neither MONGO_URL/DB_NAME nor backend/.env; available browser has only Mezan, no administration terminal. Actual binding missing vs mismatched remains unverified.

Next: obtain authorized server administration access; read actual actor and binding with projections only, independently confirm owner id, run recovery dry-run then apply with approval reference and exact expected binding. Preserve mismatched owner until reviewed. Do not send from local unmatched count: perform provider read/match after binding repair, then use existing idempotent guarded sender for eligible absent invoices, verify invoice/payment outcomes and report remaining blockers. Publish source only through v5 governed protocol under deployment authorization. No automatic header refresh may be introduced.
