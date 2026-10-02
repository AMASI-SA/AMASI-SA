# Acceptance boundary on frozen PR1240

Candidate `88cc9131027fd6783a46e2e00fc6aaec4788b8fa`, tree
`57483f44efa381ca872262f5a8bc61d2a87cae93`. No code/test/guard changes.
These are acceptance facts, not permission to implement a new feature or gate.

| Gate | Existing contract and implementation | What remains unproven |
|---|---|---|
| Full Business UAT | `scripts/testing/mz2_business_uat/scenario-plan.md` declares a synthetic16-stage setup, independent24-leg oracle and locked final screen. Its last paragraphs explicitly exclude actual stock approval, financial opening and activation. | No supplied combined final operational scenario, authoritative business inputs or accepting actor. Historical16/16setup and technical domain tests are not final Business UAT. |
| Physical-stock approval | `docs/operations/G47/OPENING-INVENTORY.md`; `backend/opening_inventory_service.py`: original JSON count/cost/account/product/variant/component/location/barcode evidence, fresh owner approval grant, verified sealed opening/safe_active, exact per-account valuation and atomic receipt/cost/quantity initialization. No new journal. | No authoritative actual count/cost/location source or physical approver has been supplied for this acceptance. Existing synthetic G47 tests prove contract behavior, not a physical count or business sign-off. |
| Opening acceptance | Native internal writer exists in `backend/accounting_financial_accounts.py:1158`. Actual public routes at1400–1411 are quarantined with409 `opening_onboarding_required`. Onboarding hands off only create/preview/review at `backend/accounting_onboarding.py:406–408`. | A positive full-app opening cycle is not currently reachable through these public routes. Do not expose/remount the internal engine and claim that is unchanged-app acceptance. Clarify whether required acceptance is denial-only or a separately authorized positive gate. |
| Activation acceptance | Internal transition contract exists at `backend/accounting_financial_accounts.py:1344–1400`; public transition at1413–1418 rejects `v2_active` with409 `onboarding_activation_locked`. Existing state/evidence/owner/423/period/Legacy isolation requirements remain. | A positive public-app activation is intentionally unavailable. Internal engine regression is not application activation acceptance. No permission to remove this guard is inferred. |
| Release Readiness | Exact-B GitHub workflow checks build, source/intent, package/reproducibility and guard contracts. `backend/accounting_onboarding.py:237–254` explicitly retains production proof/owner authorization, `production_verified=false`, `inventory_physical_approval_verified=false`, `ready_for_live_post=false` and locked Stage16. | CI success and Acceptance Smoke cannot attest Production or override missing business acceptance. Live holds remain; no claim of Release Ready. |

## Why the positive engine tests are insufficient

`backend/tests/test_financial_accounts_real_mongo.py:116–127` explicitly labels
its fixture **Engine regression harness only**. It removes quarantined test
routes and remounts returned internal engines on its own ASGI app. Its original
assertions are kept and run as technical regression. A successful test named
`test_http_review_post_concurrency_retry_and_append_only_reverse` therefore
proves the native engine/transaction contract, not availability through the
shipped public router.

In contrast,
`backend/tests/test_accounting_onboarding.py::test_handoff_atomic_retry_no_post_activation_or_legacy_leakage`
checks actual onboarding handoff and the public409 barrier with no journal,
settings, warehouse or Legacy mutation. Quarantine tests assert activation
is unavailable. Both contracts must be retained; neither assertion is changed.

## Decisions/data requested, not implementation authority

The user has been asked for the final UAT/inventory source and approver, and
whether Opening/Activation acceptance means an isolated positive cycle or
proof of denial. A positive interpretation alone does not authorize changing
the frozen source, inventing a posting route, removing a guard, or pretending
synthetic data are physical evidence. Any resulting gap must be identified
before further implementation. No new economic writer is needed or proposed
by this audit.

Smoke B already has an explicit independent Acceptance contract and has now
been executed successfully on exact88cc. It remains `PASS_ACCEPTANCE_ONLY`,
`production_verified=false`. No further Production permission is inferred.

Production financial writes by this task=0. Write-control unchanged.
Merge/deploy/Production opening/activation/schedules/lease/publish=NO.
