# Native Operational Balance permission review

Approved contract: `operational_balance_movements_write` and `operational_balance_reports_read`. They have no parent/child requirement and do not imply one another. The existing owner-only employee permission assignment API stores them independently. The generic app-manager role does not silently expand into these two explicit grants; the existing owner super-admin contract is preserved.

Native entry may read context/entity choices and POST a new movement without a receipt. Only reports_read permits GET reports, movement history and obligation detail. Native opening/finish, entity creation, audit, receipt upload/download, freeze, supplier return and balance correction remain denied even to a native owner. Web membership-based access remains unchanged.

The native bridge retains the real actor for operational requests. The operational router applies a method/path allow-list, re-reads active actor/owner and live grants, and repeats authorization at the persistence boundary. Native staff cannot acquire the merchant-shaped principal at the owner-only permission assignment endpoint.

## Fresh evidence

- New isolated Mongo/ASGI permission suite: **34 PASS / 0 FAIL**. Covers all four combinations and both directions, unauthorized/disabled users, direct forbidden endpoints, retry/no duplicate, permission revocation at persistence, owner grant/revoke and Browser behavior preservation.
- Operational + native request context + employee management suites passed together before the final added owner-assignment case: **200 PASS / 0 FAIL**; the extra owner case subsequently passed in the 34-case suite.
- Expanded final Backend regression including existing mobile permission suites: **207 PASS / 1 pre-existing FAIL**. The unchanged failing `test_employee_may_have_mobile_pages_with_zero_mezan_permissions` expects a dictionary omitting `manager` and `owner_baseline`. Reproduced the same failure on untouched #1263 base c511722f. No suppression or test modification.
- Native PR #257: full yarn typecheck/verification chain PASS; dedicated permission/render/controller tests **21 PASS / 0 FAIL**; final tsc PASS.
- Native tests exercise JSX trees with native/Expo boundaries stubbed and deterministic read races; they are not Android device UAT. No APK was built in this permission-only step.

Existing tests which previously applied permission-free Web expectations to a native principal were updated to exercise the Browser contract. Native receipt access assertions now reject, matching the approved restricted app scope. Web product tests were not deleted.

## Isolation and scope

Tests use local Mongo 127.0.0.1:27316, disposable operational_balance_test_* databases, and in-process ASGI clients. No Production credentials or server startup.

Diff against c511722f shows zero changes to Web frontend, operational service/engine/store/sources/worker, server.py or release files. Backend changes are authorization only. No accounting writer introduced.

#1263 remains c511722fef3f16dafe77b1c79a862762fbc3c4cb; Production remains a7977f4cfc1ef0a721fc25d783661332f32fe3b6 (git ls-remote verification). The companion Draft PR is stacked on #1263 without pushing to that branch. No merge/deploy/prepare/prepublish, no #1265/#1269 or lease #1042 changes. Production writes = 0.

## Changed files

- `backend/mobile_app_permissions.py`
- `backend/mobile_app_request_context.py`
- `backend/operational_balance_routes.py`
- `backend/tests/test_operational_balance_security.py`
- `backend/tests/test_operational_balance_app_permissions.py`
