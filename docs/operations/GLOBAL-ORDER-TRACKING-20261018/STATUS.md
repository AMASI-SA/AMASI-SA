# Read-only global Android order and physical-piece tracking

Task branch: `codex/global-order-tracking-integrated-20261019`.
Initial Production base: `e015b913ce950d6e5cf52cc0f1893da94b7b30b4`.
Integrated Production base: `16e44c3bd5478854bc3d12a9545f05519e61f22d`.
Draft PR: https://github.com/AMASI-SA/AMASI-SA/pull/1438
Superseded PR1437 is closed without merge; its branch is preserved.
Worktree: `D:/amasi-build/backend-global-order-tracking-integrated-20261019`.

## Contract

GET `/api/mobile/order-tracking/{order_number}` requires a native authenticated
session and fresh `app.action.orders.track` authorization. Managers inherit the
catalog action; employees need an explicit grant, independent of Orders access.
All operational reads are scoped to the authenticated merchant and order.
The response contains no customer address/phone or financial fields.

Each physical piece retains its own identity, dated milestones, supplier and
responsible employee. Current locations follow persisted page evidence rather
than inferring a page from the order status alone. The order timeline preserves
stored Salla transitions and Mezan-only review approvals as separate sources.
Terminal/external status changes do not fabricate completed local milestones;
notices identify the last evidenced stage for the order and each unfinished
piece. Uncertain status, stale/superseded approval and conflicting identities
yield explicit unknown locations. Repeated historical visits are retained.

Local Mezan review can coexist with Salla `under_review`, with matching completed
operation proof and v2 readback proof when applicable. A newer return to review
invalidates the previous approval as current-location evidence. No approval is
replayed, no workflow is reconciled and no indexes or records are written.

## Evidence (2026-10-19)

Root independently ran the final candidate on isolated Mongo 8.0.12 via ASGI:
**40 passed in 18.36s**, exit 0, zero skips. Command:

```text
python -m pytest --noconftest -p no:cacheprovider -q --tb=short
  backend/tests/test_mobile_order_tracking.py
  backend/tests/test_mobile_app_request_context.py
  backend/tests/test_mobile_app_manager_role.py
  backend/tests/test_mobile_app_permission_owner_resolution.py
```

Twenty endpoint tests verify tenant/grant/revoke/manager boundaries, no database
writes, mixed physical-piece locations, partial receipts, direct assembly,
terminal/unknown status, Salla/Mezan timelines, local proof and re-review,
truncation and inconsistent source identities. Twenty existing authorization
tests also pass. External provider transport is forbidden by the fixture.
The real ASGI synthetic response is consumed by the actual Android parser/UI
test in mobile PR280 (18 UI contracts and 7 auth/navigation contracts passed).

The dedicated CI tracking suite passed on source
`8a4af92f5268e65c6612ba5969f40111e849d141` (run 39178684499).
Two general jobs failed because Production advanced to 16e44 after this
candidate's base: the PR workflow required a new shipping test absent in the
old checkout, and the release gate correctly rejected the non-ancestor base.
Merged the current Production source into this task branch without conflicts
(`a4ce75602dbd57854461ed70d937059f059aea5e`). Root reran the same 40 tests:
**40 passed in 28.15s**, exit 0. No guard or test floor was weakened; fresh CI
is required for this integrated candidate. No `/app` or lease was touched.

Final release-history repair: merging the newer Production branch preserved
an intent-changing merge in full history, so the guard correctly still refused
the candidate. Preserved that old branch/PR and cherry-picked only this task's
three commits onto exact Production 16e44 in a new branch. Checkpoint
`3ae031cf8` is byte-for-byte tree equivalent to old `2ec299b517084ab69bc4598d700c73f86e15dd09`
(`git diff --exit-code`, exit 0). The actual `_assert_candidate_source_transition`
now passes with the existing intent unchanged. No guard, intent or production
file was altered to satisfy the check. A new Draft PR supersedes PR1437.

The broader legacy permission suite has an independently reproduced baseline
failure in `test_employee_may_have_mobile_pages_with_zero_mezan_permissions`
(owner_baseline expectation); profile-access collection requires unavailable
`xlsxwriter` in the focused runtime. Neither is claimed as passing. A dedicated
CI workflow executes the relevant 40-case suite with a no-skips floor.

## Boundaries and next action

Final source checkpoint `4a3ac79d73a11132255b502f037abeddcd4b469e`: all **29
applicable GitHub checks passed**, four release/Snapchat jobs were nonapplicable
and skipped. Dedicated tracking run 39240084803 passed. General review run
39240084805 passed 451 tests plus 265 subtests in 276.70s and a separate one-case
acceptance guard, with zero skips/failures/errors. The source release build
classification now passes (39240084548); this is not deployment verification.
Mobile PR280 source `2022718039e8f3fa8c2485d5a34132056cc59ebf` passed
frontend-checks run 39239121115 including the filename change. Temporary local
preview and dedicated loopback Mongo were stopped; their directories retained.

Owner follow-up: employee product PDF filenames now come from the persisted
assigned employee, actual rendered piece quantity, original registry assignment
timestamp converted to Riyadh, and stable file number. The PDF response provides
an ASCII fallback plus UTF-8 `filename*`; the companion Android native downloader
uses that name for both immediate print and later file reprint. No PDF geometry,
content, assignment, permission or database records changed. Missing historical
metadata is explicit and never replaced with the download date. Manual checks
covered UTC-midnight rollover and a real server-header-to-Android-downloader
fixture; existing PDF/viewer/share/session and dependency checks pass. The sample
uses synthetic employee data only. Native file-picker readback remains pending.

Production changed: **no**. No merge, release lease, deployment, APK or OTA.
History is the stored synchronized evidence: this endpoint does not call Salla
to fill missing history. Missing timestamps remain null, not generic update
times. Backend PR1434 (receiving custody/message) and mobile PR279 (shipping
display) are independent changes and must be integrated by the release owner.
The cancelled PR1433 stays excluded. No installed app rollback is authorized.

Next: review this candidate and CI, integrate with the mobile global-search
branch, perform native visual/device UAT, then let the existing release owner
follow the production release guard and one-APK process. A source checkpoint
or Draft PR is not release approval or evidence of deployment.
