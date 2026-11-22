# Employee mobile provider statements — 22 November 2026

Date: 2026-11-22 (Asia/Riyadh). This continues the user's existing authorized Android86 / 1.0.71 release, including the final request for a side-menu page to upload payment-platform settlement statements and a separate employee permission.

IMPLEMENTATION_COMPLETE:true
READY_FOR_RELEASE:false
APK_PUBLISHED:false
BACKEND_DEPLOYED:false

Mobile: [PR373](https://github.com/AMASI-SA/amasi-mobile/pull/373), parent `079b5bae883ca6a7d7defc5e1fbffc2cca2192b8`.
Backend: [PR2050](https://github.com/AMASI-SA/AMASI-SA/pull/2050), parent `a063c22d6f0f9f4125032ca9d2d0066cc09e9d27`.
Both task branches remain `codex/assembly-queues-fast-read-20261121`. This file's containing source commit and the current PR heads are the continuation checkpoints; do not treat the old parents as the final expanded source.

## Delivered behavior

- New employee-app side-menu page **كشوف تسويات منصات الدفع**. The employee chooses an allowed platform and an Excel `.xlsx` file, then explicitly presses upload. Supported canonical parsers are Tabby, Tamara and Salla. Files over 10 MiB are refused.
- Ten statement files per history page, exact total and snapshot-bound pagination. A statement opens its own ten-row review on demand. The saved platform, file identity, statement reference, counts, amounts and fees come from Mezan.
- The screen reads only while visible and active. No polling, background directory hydration or automatic upload. Pending reads and private rows are invalidated on account, authority, foreground and navigation changes.
- Explicit green/red result window for three seconds. A failed read is distinct from an empty history. Upload ambiguity keeps the same immutable request ID, selected platform and private file for an explicit retry; it never silently generates a second financial command.
- Native Android selection uses the installed Expo FileSystem picker. Content URIs are copied with `expo-file-system/legacy.copyAsync` into the app's private directory. Persistent attempt ownership is serialized, concurrent identical submissions coalesce, and changed/foreign/traversal paths are rejected. A never-submitted file is cleaned on invalidation; an uncertain submitted file is retained.

## Separate shared employee permission

- Owner-only editor in employee management in both the app and Mezan: **بدون صلاحية / عرض فقط / عرض ورفع**, with allowed platforms. Both editors use the same stored grant and versioned API.
- Literal server-derived `app.page.provider_statements` gates the route, menu and home fallback. App-manager wildcards do not enable the feature. Old servers keep the page hidden.
- Backend reloads the real account, real owner and unique active employee link. Disabling, archiving, relinking or withdrawing access invalidates an old upload.
- GET/PUT `/api/employees-v2/management/employees/{employee_id}/provider-statement-access`. PUT includes expected account scope, policy version and page revision; atomic comparison preserves both complete documents and all unrelated grants. The general-finance and refund editors also preserve the independent statement grant.
- New grants permit reading and uploading selected platforms only. They do not add general balance/report/transfer/refund authority, missing-entry confirmation or saved-file reuse. Existing separately authorized web behavior remains when the new grant is absent. An explicit `none` cannot fall through to an older grant.

## Existing financial writer and mobile API

- GET `/api/operational-balances/provider-statements/mobile`.
- GET `/api/operational-balances/provider-statements/mobile/{64hex}/review`.
- POST `/api/operational-balances/provider-statements/mobile/upload` with `file`, `provider`, `request_id`, `expected_session_scope`, `expected_started_at`.
- The mobile route reuses `operational_provider_statement_reconciliation.upload`, its parser, atomic writer and replay receipt. It adds no second financial writer. Existing automatic matching behavior is retained; confirming missing transactions is not exposed by the mobile grant.
- Selected platform must match the actual workbook before any file/state save, and is part of idempotency identity. Authorization is rechecked before financial CAS. Errors known to occur before any write may return `not_applied:true`; failures after a commit remain uncertain and recover with the same command.
- History is projected metadata; review uses the saved canonical projection without external payment-source hydration. Native routes block confirm, delete and saved-file application. Responses are `no-store, private`.
- The operating-period token `started_at` is validated but its original spelling is preserved, since the canonical writer compares it exactly. The mobile grant parser accepts a valid empty account display name while still requiring exact nonempty account/employee identity; the directory supplies the visible employee name.

## Fresh verification on the delivered source

| Scope | Result |
| --- | --- |
| Actual mobile screen/repository/file-store behavior | 25/25 groups PASS |
| Actual mobile access editor, route/menu, runtime and write gate | 13 groups PASS |
| Full mobile TypeScript | `tsc --noEmit --pretty false`, exit 0 |
| Scoped mobile ESLint | zero errors; one existing employees-screen warning |
| Mezan web permissions/history/API/employee management | 5 suites, 178 tests PASS |
| Integrated backend canonical statements, grants, auth and adjacent policies | 304 tests PASS, zero failures/skips, 5 warnings, 48.75 seconds |
| New backend employee-statement suite | 32 tests, included above |
| Actual synthetic HTTP backend payloads through mobile parsers | 5 contracts PASS: grant, empty, upload, history, replay |
| Existing mobile refund/self-balance/navigation/backend compatibility/auth/source-addition/assembly gates | PASS |
| Independent backend source/contract review | PASS; exact period-token defect found and fixed with three regressions |
| `git diff --check`, backend Python syntax | PASS |

The mobile suite reproduced and verified fixes for account switching during file selection, reopening during an in-flight upload, and cleanup of an unsubmitted private file. The backend suite covers post-commit response failure and same-ID replay, authority revocation during matching, platform mismatch, snapshot drift, and preservation of unrelated permissions.

Tests use the actual application modules with synthetic HTTP/native/storage boundaries and MongoMock. This is not physical-device SAF/UAT, production data validation, a native APK build, or real financial activity. No production refund, settlement, permission grant, notification, bank or platform operation was performed.

The two mobile behavior suites now run in the existing Mobile CI and Android86 combined gate. The backend route gate includes the new suite with a 32-case no-skip floor. No existing gate or immutable scanner/authentication pin was weakened. Old client test loaders now load the real narrow statement write policy. The auth guard accepts exactly the three anchored new import/flag/gate lines and tests missing/moved/duplicate/broadened variants. Existing backend owner-catalog expectations were updated only for the derived capability; owner no-extra-database-read and manager-negative assertions remain.

## Preservation and release state

All preceding queue, readiness notice, employee workload, shipment matching, unread-preview, financial-notification, customer-refund, self-balance and compact global-tracking work is retained from the parents above. The courier count-definition discrepancy (315 versus 254) remains explicitly deferred.

Candidate remains Android86 / 1.0.71, `mezan-web-assembly-pilot`, `com.amasi.sa.mobile`, Expo owner `amasi-sa`, project `f285e8fe-06a6-48b4-b095-f1c42d63f7c3` / slug `amasi`, production API `https://mezansalla.com/api`. Dependencies, locks, native/scanner sources and OTA-disabled policy are unchanged. The profile adds only the narrow statement-write rollout flag; global financial writes remain governed by existing policy.

Fresh production-profile Android export completed successfully on this expanded source: **1,832 modules**, **44 unique assets** (46 metadata references), one **6,827,226-byte Hermes bundle**, SHA256 `e97ffbc0b315a0f26f67d94c42f35b489e4cd86f33bab67681ee1ec3d2cfcbc5`, exit 0 in 46.37 seconds. The 315 application-input files had the same aggregate SHA256 before and after export: `8e64c4024fd4892e040cd2eb573738d7ace78c1cf87a795af20be8b267e733a5`. The resolved profile confirmed Android86 / 1.0.71, the production API, expected package/project and disabled OTA (`NEVER`). No native dependency changed. This supersedes the old application export. **It is not an installable APK or an EAS build.** Exact new-head remote checks are recorded in the final continuation update.

At the preceding exact app head `079b5bae883ca6a7d7defc5e1fbffc2cca2192b8`, Mobile CI run45049152685/job137290207450 and bundle run45049152665/job137290207190 failed before execution (`runner_id:0`, `steps:[]`). No billing/quota/administrative cause is inferred. The current turn's Expo Build-from-GitHub page also timed out; no EAS build was submitted and no credentials/account changes were made. Recheck exact new source checks before any publication; do not replace required remote checks with local success or replay identical failed attempts.

App-first publication is already authorized while Mezan waits; unsupported server features remain capability-gated. Do not ask for the same authorization again. Build only the reviewed exact source through the authorized Expo UI route when available, with Android/internal APK, working directory `frontend`, profile `mezan-web-assembly-pilot`, automatic store submission off, and OTA disabled. Do not activate retired workflow/NOT_COMMISSIONED publishers.

Mezan remains an integration checkpoint, not a v5 source/intent release pair. No merge, production lease, shared `/app` mutation or deployment occurred. Later release must coordinate with the other financial-work conversation, read current production and the actual guard, continue only when the guard is inactive, then follow `AGENTS.md` v5 with reviewed A, intent-only B, fresh reproducibility/CI, explicit DeploymentSucceeded and three identity checks. Never clear another conversation's lease or recreate its financial writer.

Existing authorized continuation automation: `Automation_7fd7e2fc16f8819199903b78592d5757`. Update its source pointers after the paired remote checkpoints; preserve its schedule, app-first preference and deferred courier scope. Issue1006 is at its comment limit; these status files and existing PRs provide the durable handoff.

## Backend and web reproduction

With the repository test dependencies installed and an explicitly synthetic JWT secret:

```sh
PYTHON_DOTENV_DISABLED=1 PYTHONPATH=backend:backend/tests python -m pytest   backend/tests/test_provider_statement_employee_access.py   backend/tests/test_operational_balance_statement_reconciliation.py   backend/tests/test_operational_balance_statement_intake.py   backend/tests/test_operational_balance_statement_fees.py   backend/tests/test_mobile_app_permissions.py   backend/tests/test_mobile_app_permission_owner_resolution.py   backend/tests/test_customer_refund_access.py   backend/tests/test_mobile_app_request_context.py   backend/tests/test_runtime_stability_mongo_auth.py   backend/tests/test_operational_balance_access.py   backend/tests/test_operational_balance_web_page_grants.py   -q --disable-warnings --maxfail=1
```

From `frontend`:

```sh
CI=true npm test -- --watchAll=false --runInBand   src/pages/ProviderStatementAccessDialog.test.jsx   src/services/providerStatementAccess.test.js   src/pages/operational/ProviderStatements.test.jsx   src/pages/operational/providerStatementsApi.test.js   src/pages/EmployeesV2Management.test.jsx
```

Web grants and upload remain lazy and owner-authoritative; background/account changes invalidate stale permission responses. An uncertain permission save requires a fresh read before another attempt. New statement access alone never enables the existing saved-file application button or missing-operation confirmation.
