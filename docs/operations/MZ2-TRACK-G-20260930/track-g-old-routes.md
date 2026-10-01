# Track G opening route reachability

Scope: MZ2 opening mutations exposed by the production router. No production database, opening post, activation, deploy, release lease or merge was performed.

## Registered production chain

`server.py` mounts `make_financial_provider_apps_router(db, current_user)` on the `/api` router. The factory begins with the legacy provider router prefix `/financial-provider-apps`, installs P01 opening and financial-account handlers, wraps financial mutations with `protect_accounting_routes`, then adds the separate flat onboarding router. HTTP dispatch uses the first matching route; the production-factory test asserts each quarantined path is registered exactly once.

Let **P** = `/api/financial-provider-apps/accounting-module` and **O** = `/api/accounting-module/onboarding`.

| Endpoint | Classification and behavior |
| --- | --- |
| GET P/opening-balances | Historical P01 evidence; read-only, `diagnostic_only=true`, `live_actions_enabled=false`. Retains historical account/employee context only, never onboarding SSOT. |
| POST P/opening-balances/preview | Former public alternate writer; HTTP 409 `opening_onboarding_required`. No P01 engine call. |
| POST P/opening-balances/approve | Former posting bypass; same quarantine. |
| POST P/opening-balances/activate | Former activation bypass; same quarantine. |
| GET P/financial-accounts/opening-balances/drafts | Retained owner-scoped historical evidence. |
| GET P/financial-accounts/opening-balances/drafts/{draft_id} | Retained owner-scoped historical evidence. |
| POST P/financial-accounts/opening-balances/drafts | Public alternate draft mutation quarantined. Internal `create_draft` retained. |
| POST P/financial-accounts/opening-balances/drafts/{draft_id}/preview | Public alternate mutation quarantined. Internal `preview_draft` retained. |
| POST P/financial-accounts/opening-balances/drafts/{draft_id}/review | Public alternate approval quarantined. Internal `review_draft` retained. |
| POST P/financial-accounts/opening-balances/drafts/{draft_id}/post | Public posting quarantined. Internal `post_draft` retained without any public registration. |
| POST P/financial-accounts/opening-balances/drafts/{draft_id}/reverse | Public alternate opening mutation quarantined. Internal reversal engine retained. |
| POST P/financial-accounts/transition, target=v2_active | HTTP 409 `onboarding_activation_locked`; no transition engine call. |
| POST P/financial-accounts/transition, target=transition_blocked | Existing permissioned financial barrier only, retained. Does not activate or post. |
| POST P/financial-accounts/opening-balances/evidence | Immutable evidence setup retained for onboarding. No journal/post/activation. |
| Financial-account definitions/list/create/update/archive | Canonical registry setup retained; cannot post an opening journal. |
| POST/PUT O/sessions and section/cutover metadata | New onboarding setup; separate allowlisted setup boundary. |
| POST O/sessions/{session_id}/preview and /review | Authoritative session compile/review; no journal. |
| POST O/sessions/{session_id}/opening-draft | Only onboarding composition calls retained canonical create/preview/review engines, after session review and snapshot revalidation, inside the owner transaction. Pause remains enforced on this financial handoff. No post/activation handler is consumed. |

Unauthenticated or unauthorized callers are rejected before quarantine as appropriate. A paused financial owner may receive the existing pause error before HTTP 409; both paths fail closed. The quarantine handlers intentionally accept no old payload contract, so even stale or crafted payloads cannot reach any writer.

## Engine and caller tracing

- P01 `create_opening_preview`, `approve_opening_preview`, `activate_p01` remain importable for historical synthetic regression fixtures. Production HTTP routes no longer call them. Repository search finds their remaining direct consumers in the historical backend test suites.
- Canonical lifecycle engine function objects are returned by `install_financial_account_routes`. Production `accounting_onboarding.handoff` consumes only `create`, `preview`, `review`. The internal `post`, `reverse`, `transition` objects are retained for engine regression and a future separately authorized live gate; no request parameter can select these functions.
- `post_opening_journal_v2` has one production module caller, the retained canonical `post_draft` engine. The quarantined route cannot reach it. Generic V2 journal creation has a separate opening/reversal type exclusion contract.
- Existing real-Mongo lifecycle tests explicitly install an **engine-only test harness**. This is not a configuration switch in production. Public quarantine tests use both installers and the assembled production router factory.

## Browser reachability

- `AccountingWorkspace` routes the `opening-balances` page to the sixteen-stage onboarding flow.
- Its `financial-accounts` page retains canonical account setup, the link to onboarding and read-only historical opening details. Removed controls include alternate opening draft/preview/review/post/reverse and V2 activation.
- The legacy `AccountingOpeningBalances` component and legacy service functions remain import-safe historical code; no active workspace mounts that component. Backend quarantine applies even if a stale client calls these service URLs.
- `AccountingFinancialAccounts` can still request the transition to `transition_blocked`; this only closes writers.

## Other legacy surfaces and reporting

The broader legacy-active state is also quarantined. A legacy writer transition guard alone is insufficient: before migration the owner may still be `legacy_active`.

| Legacy public endpoint | Opening-specific boundary |
| --- | --- |
| POST `/api/accounting/migration/run` with `dry_run=false` | HTTP 409 before snapshots, cutoff writes or posting. Original migration engine remains unregistered. `dry_run=true` remains read-only and returns LEGACY diagnostic metadata. |
| PUT `/api/ad-accounts/{cp_id}/opening` | HTTP 409 for all opening changes, including zero/debt clearing; original engine has no public registration. |
| POST `/api/accounts` | Nonzero `opening_balance` rejected before account/transaction insertion. Zero-balance account setup preserved. |
| POST `/api/accounts/{account_id}/transactions` | `transaction_type=opening_balance` rejected before mutation. |
| DELETE `/api/accounts/{account_id}/transactions/{tx_id}` | Stored opening transaction cannot be deleted. |
| DELETE `/api/accounts/{account_id}` | Cannot delete an account with an opening balance or opening transaction, preventing implicit opening deletion. Ordinary zero accounts remain deletable subject to existing constraints. |
| POST `/api/ledger/entries` | `entry_type=opening_balance` rejected for both draft and auto-post. Internal ledger engine remains unchanged. |
| POST `/api/ledger/entries/{id}/post` | Stored opening draft rejected before posting. |
| POST `/api/ledger/entries/{id}/reverse` | Stored opening entry rejected before reversal. |
| POST `/api/ledger/groups/{id}/reverse` | Every leg is checked before any reversal; a group containing opening entries is rejected. |

Account update has a fixed metadata allowlist and no opening amount/date fields. Ad account create initializes zero; its update schema/allowlist permits only name, notes and external account ID. These routes cannot replace the quarantined opening endpoint. Generic adjustment contracts allow only settlement/writeoff/adjustment and do not accept opening types. Ad historical spend migration is a spend importer, not an opening producer; it is not changed here.

The old `_ensure_opening_balance_seeded` entry point is now a read-only preflight. If an account has an existing posted ledger entry, or no nonzero stored balance, it returns normally. If a nonzero unseeded opening would be required, it raises HTTP 409 before any seed. The original implementation survives as `_seed_legacy_opening_engine` with no registered-route caller. Therefore existing ordinary movements remain possible, but no public caller silently copies legacy balances into opening journals.

Verified lazy-seed reachability (all prefixes include `/api`):

- `/accounting/employees/{emp_id}/advances`, `/custody`, `/custody/return`, `/settle`;
- `/accounting/suppliers/{supplier_id}/pay`;
- `/accounting/external-persons/{cp_id}/grant` and `/collect`;
- `/accounting/bank-transfer`, `/accounting/expenses`;
- `/accounting/couriers/{courier_id}/cod-deposit` and `/cod-settle`;
- `/ad-accounts/{cp_id}/topup`.

These reach the preflight directly or through `_enforce_sufficient_funds`; no listed path reaches the retained seeding engine. The predicate is independent of `legacy_active`/`v2_active`, so a legacy-active owner cannot bypass onboarding.

GET `/api/accounting/financial-position` remains a historical report. Its response explicitly declares `report_scope=LEGACY`, `diagnostic_only=true`, `read_only=true`, `mz2_authoritative=false` and links to the MZ2 report route. Both existing historical Financial Position pages display **LEGACY** and a read-only diagnostic label. MZ2 financial position remains a separate route, with no mixing of these totals.

## Verification

`python -m pytest backend/tests/test_accounting_opening_quarantine.py -q`: **12 passed**, including all eight public alternate routes, activation denial, preserved read/setup routes, no duplicate route shadowing, and the actual assembled router factory. The isolated HTTP tests disallow all database access after mocked authentication; owner transaction Mongo behavior is covered independently by integration tests. 

`MZ2_TEST_MONGO_URI=mongodb://127.0.0.1:27128/?replicaSet=trackg python -m pytest backend/tests/test_financial_accounts_real_mongo.py backend/tests/test_accounting_opening_quarantine.py -q`: **27 passed** (15 real-Mongo canonical engine contracts plus 12 public quarantine checks; zero skips).

From `frontend`: `CI=true node node_modules/react-scripts/bin/react-scripts.js test --watchAll=false --runInBand --testMatch '**/AccountingFinancialAccounts.test.jsx'`: **11 passed**. Covers preserved registry CRUD/retry identity, historical FX and explicit zero evidence, five draft states exposing no alternate actions, and no activation control while transition-blocked. The explicit test glob avoids the Windows Jest 27 path-glob separator issue under `.worktrees`; no test behavior or production code is changed for that workaround.


## Expanded legacy boundary validation

`MZ2_TEST_MONGO_URI=mongodb://127.0.0.1:27128/?replicaSet=trackg python -m pytest backend/tests/test_legacy_opening_quarantine.py -q`: **18 passed**, zero skips. Twelve HTTP cases cover opening creation/apply, draft posting, reversal and deletion; four preflight cases cover positive/negative unseeded balances and preserved existing-ledger/zero paths. Two real-Mongo cases prove dry-run leaves collection counts unchanged and ordinary zero account creation produces no opening transaction or journal.

## Unresolved P02 native writer integration

The broad adjacent suite failed all twelve `test_mz2_shipping_p02.py` cases in their historical fixture: `activate_p01` cannot verify a legacy opening after native-only verification is enforced. This is an honest integration blocker, not a skipped test or a passed regression suite.

Changing the fixture alone cannot resolve it. `accounting_shipping_p02.py` uses `ledger_core.post_txn_group` for courier fee, COD sale and driver fee; its duplicate-sale query reads `general_ledger`. `accounting_shipping_settlements.py` uses the same legacy writer. A valid native opening requires `v2_active`, while `ledger_core.post_txn_group` calls `assert_writer_allowed(..., "legacy")` and therefore rejects that state. Native balances and a legacy shipping writer cannot form a valid runtime contract.

Required dependency: port the P02 posting and duplicate-event paths to native V2 ledger services, then convert the fixture and assertions to native collections. This Track neither imports excluded shipping changes nor restores legacy fallback nor bypasses tests' activation/write gates. P02 remains locked; no production shipping, opening or activation write was performed.
