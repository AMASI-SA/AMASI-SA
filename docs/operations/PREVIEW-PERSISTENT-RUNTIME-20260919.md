# Preview persistent runtime recovery — runtime verified 2026-09-19

Scope: isolated Preview only. Production was not changed by this task. Accounting phase P01 remains open.

## Root mechanism
Supervisor retained backend/frontend commands beneath /opt after the corresponding private runtime files disappeared. Both programs failed with ENOENT. findmnt showed /app on its own ext4 mount and /opt on the container overlay. This proves the runtime persistence mismatch; the exact event/actor that removed the old files was not established.

## Applied recovery
scripts/preview_persistent_runtime.py pins the complete application tree at e82918d130f42b228346b2cbd3d70ff4daf21bf3. The isolated source, frontend build, installed dependencies, private signing state and pinned Node toolchain now live beneath /app/.worktrees/preview-runtime-state-20260919. Supervisor uses the persisted repair-worktree launcher, not missing /opt files. A disposable Yarn download cache lives in /tmp and is not required to run the prepared application.

Retained the previously approved host-bound Preview authentication policy with matching pinned auth source hashes, independent sessions, ownership/roles and login guards. Python external socket egress is denied and the exact expected automatic startup handler list is replaced with process-local authentication/readiness setup only. The complete application source is checked against a prepared manifest at launch. No application-file overlays or Production release identity are fabricated.

## Fresh evidence
- Release guard was inactive before preparation and activation; only this task's Preview reservation was acquired.
- Python compilation passed.
- Private frozen-lockfile dependency installation completed; whole Preview frontend build completed successfully (existing bundle/dynamic-import warnings remain).
- Boundary and source manifest verification passed.
- Exactly four Supervisor fields changed: command and directory in backend/frontend. Every unrelated program section was retained.
- Frontend /preview-meta.json and backend /api/preview-auth-policy returned HTTP 200 with the same pinned source SHA.
- Requests sent locally with an unauthorized Host or Origin were rejected with HTTP 403.
- Browser opened the real Preview login page instead of 502.
- Explicit supervisorctl restart backend frontend succeeded. Both processes remained RUNNING and both metadata probes still returned HTTP 200 with the pinned SHA. Browser reload again displayed the login page.
- Private before/after fingerprints of the existing financial collections matched exactly; no financial posting, reversal or fixture mutation was performed.

## Limits and remaining accounting work
A complete platform container replacement was not deliberately triggered; the demonstrated test is service restart plus verification that runtime dependencies reside on the persistent mount. Platform changes that discard /app or replace Supervisor configuration would need separate recovery.

Local Mongo was verified to be standalone. The candidate's transaction requirement is intentionally preserved. Financial end-to-end acceptance therefore still requires a separately isolated replica-set test database (or another approved transactional topology); this task did not convert the shared database. Login and accountant workflows after credential entry remain to be tested. The application emits the expected unavailable Production release identity message in this Preview-only package; the explicit Preview metadata provides its actual source identity.

Earlier attempts and cleanup: shared node_modules disappeared before copying; a private install then hit ENOSPC. Only this task's generated failed-install artifacts were removed. The package cache was moved off the limited persistent volume. Another task's active release lease correctly blocked the retry until its owner completed deployment. No foreign lease was removed.

This recovery PR is tooling evidence, not authorization to merge/deploy Production or close P01.
