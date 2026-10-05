# Operational Balance Web scope cleanup

PR #1263 is Web only. Android is paused. All Android/APK documents, scripts, logs and patches in this directory are historical evidence, not current Web implementation or build instructions to execute.

## Narrow source change

Relative to 3a41a031a1f037fcf320e41bf0f5bc70a2acd7c0, remove only four Operational Balance catalog entries from mobile_app_permissions.py, three route-map entries from mobile_app_request_context.py, and the /employee/operational-movements route from App.js. Both Mobile files now have zero diff from Production base 68507e1e0c0bfb2c21271e0aaab2cd8ffa768759. No tests deleted or changed.

## Product preservation

`git diff --exit-code 0d8e4147a398a0ddd3928c5e04a66adf67a66379 -- backend frontend ':(exclude)backend/mobile_app_permissions.py' ':(exclude)backend/mobile_app_request_context.py' ':(exclude)frontend/src/App.js'` is empty (exit 0). App.js differs only by removal of the employee route; /operational-balances is unchanged. Opening, balances, manual movements, order bank transfer, idempotency, audit, API, worker and Web tests are unchanged. Accepted Web UAT 12/12 is preserved, not rerun.

The operational backend modules have no direct imports of either Mobile module. Existing server.current_user still invokes the shared mobile_app_request_user adapter: browser sessions pass through before Mobile permissions/route lookup. Therefore there is no operational dependency on Mobile grants or mapping, but claiming no shared authentication import at all would be inaccurate. An executable check against the actual imported adapter passed five browser passthrough cases and five native employee rejection cases without any DB/network access. This restores the pre-PR Mobile route/catalog behavior; no substitute Web grants were added.

## Fresh checks

- Operational Backend: 149 PASS / 0 FAIL.
- Frontend: 43 PASS / 0 FAIL, four suites, 7.742s.
- Additional test_mobile_app_permissions.py and test_mobile_app_request_context.py: 14 PASS / 1 FAIL. Combined Backend run: 163 PASS / 1 FAIL, 35.72s.
- Failure: test_employee_may_have_mobile_pages_with_zero_mezan_permissions expects an exact dict without manager=False and owner_baseline=False. Reproduced unchanged against the Production-base permissions module loaded from git into memory: 1 FAIL, 2.28s. No live Production access. This is pre-existing and not caused by removal of the four entries. It is disclosed, not suppressed or fixed outside scope.
- Backend command: PYTHONPATH includes backend, backend/tests and local dependency site-packages; python -B -m pytest --noconftest -q backend/tests/test_operational_balance*.py backend/tests/test_mobile_app_permissions.py backend/tests/test_mobile_app_request_context.py --tb=short (PowerShell expands the operational glob).
- Frontend: CRA Jest, CI=true, --watch=false --runInBand --resetMocks=false; OperationalBalances, DomainDetails, MovementHistory, MezanV2NavigationShell.employees suites.

Existing operational tests that construct synthetic Mobile principals remain unchanged compatibility fixtures; they do not validate the real Mobile permission bridge. No new Android behavior is claimed.

No rebase, merge, Prepare, Prepublish, deploy, Android build, APK or accounting operation. Production unchanged; Production writes = 0. Final commit/tree and PR state are recorded in PR #1263 and Issue #1006.
