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

### Live access clarification
Code-server administration was recovered at the known project. Release guard reports active:false. A read-only query using /app/backend/.env returned only 3 invoices, versus 8830 on production; it is NOT the production dataset and must not be repaired as production. Installed motor/python-dotenv into isolated /tmp/mezan-qoyod-recovery-deps only. No DB writes. Production Manage > Database is available; View database opens the credential entry form at mongoview.emergent.host. Continue via secure database authentication, then inspect the real binding. Do not copy any credentials to logs, Git, chat or model-visible tools.

Verified implementation checkpoint: 9cefca40ab32eaf9fb356e8f4e68d03b494211b5, tree 053d2083fd90f4c16e1bb590f80094fa2dfdfae5, PR #1822 Draft. Remote task branch remains codex/qoyod-banner-manual-refresh-20261108. No production deployment, owner repair, or invoice sending has occurred.

### Production viewer read-only diagnosis
Secure browser authentication succeeded to Production Database Manager. Database displayed salla-analytics-test_database (393 collections), qoyod_invoices 9141 raw docs, unified_orders 16120. These raw counts include data outside invoice-review filters. Production qoyod_settings main document 6a3e951b5f40fa3bcd76f6d7 has no security_owner_id field; plan_b_auto_send_orders_user_id is present. No field was changed; opened/closed editor without saving, Edit switch returned off. Viewer supports existing field edits but no observed add-field operation. Production credentials were never inspected or printed.

Next handoff: code-server terminal read -s prompt for MEZAN_QOYOD_MONGO, user enters production URI privately. This variable must be used only in this shell, never printed, persisted, or committed, and unset after work. Use exact production database selected in the viewer; first confirm account and binding with minimal projections and no write. Then apply approved owner-binding repair atomically and verify app authorization; do provider scan before eligible idempotent invoice sending. GitHub Issue #1006 ledger update failed because GitHub disables new comments after 2500; PR/status document remains continuity source. No production writes, deployment or invoices sent yet.


### 2026-11-09 resumed session: network blocker
User entered URI privately and detached the terminal. Attached the offered bash session (3757) through VS Code Terminal: Attach to Session; observed QOYOD_CONNECTION_INPUT_ACCEPTED in that session. Code-server login renewed with browserAuth. Release guard active:false. Reinstalled motor into isolated /tmp/mezan-qoyod-recovery-deps after transient dependencies disappeared. Two read-only Mongo connection attempts returned ServerSelectionTimeoutError (15s selection; then 10s selection/5s connect). Sanitized classification: timeout=true, dns=false, refused=false, tls=false, auth=false. This does not prove a firewall cause, only that this preview terminal cannot currently reach the production endpoint. No secret values were printed or copied. No owner binding or invoice write occurred.

Latest production source observed: 18c245748d22615332d10c674812d4362b88ae71; security-owner guard still requires explicit binding. Draft #1822 has not been merged or deployed. Do not publish stale task source over subsequent production fixes/rollbacks. Next safe step: execute reviewed binding-recovery dry-run in an authorized production-connected runtime, confirm actual owner and expected missing binding, then approved atomic repair, provider reconciliation and guarded idempotent send. Do not ask user to repeatedly reenter the URI to resolve a network timeout. Local exec workspace was unavailable this turn; immutable code and status are preserved remotely. Issue #1006 comments remain blocked by GitHub's 2500 comment limit.

### 2026-11-09 explicit production recovery delivery
The merchant reiterated fixing production by the available method. Production viewer independently confirms the exact main settings document and the sender's owner account (role owner); the binding remains absent. Added a release-startup-only, exact-database/exact-document/exact-owner migration. It never infers authority from role or sender setting: the independently approved owner is explicit, and the sender setting is an additional drift check. Missing-only CAS plus embedded audit prevents duplicate execution and preserves explicit null, different bindings, and any prior recovery audit (including revocation). No provider calls, financial writes, polling, credential changes, or HTTP self-claim path. Startup is bounded to three seconds and failure retains fail-closed authorization without blocking availability.

Merged production source 18c245748d22615332d10c674812d4362b88ae71 into the task branch, preserving production rollbacks and courier fixes. Shared /app was inspected: active:false, same production HEAD, only unrelated untracked files. No shared files changed.

Fresh verification: 132 backend tests passed (manual summary, operator recovery, inherited security, invoice review, exact approved migration, startup guard); includes real guard denial before repair, approved owner allowed afterwards, other owner and mobile employee denied, duplicate/revoked/drift/race protections. Test collection initially needed httpx in the fresh local runtime and backend/tests on PYTHONPATH; the final command passed. git diff --check passed. Frontend dependencies/build/release preparation pending. Production changed: no. No invoice sent. Next: frontend regression, governed source A/B build, review immutable artifact; deployment must follow the repository protocol and live outcome verification before provider scan and idempotent sending. Ledger Issue #1006 remains comment-limited; this document and PR #1822 retain continuity.
