# Preview runtime repair — 2026-09-16

Task: PREVIEW-RUNTIME-REPAIR-20260916. Status: SERVICE_RECOVERY_VERIFIED.
Scope: isolated Preview service only. No Production publication or Production data changes by this task.

## Confirmed causes

Source snapshot: `9f8b16998f62cbdb08dc9361bc2e443223d27d17`.

1. Supervisor frontend was FATAL. Its command `yarn start` entered the strict Production
   artifact launcher; stderr confirmed `Governed frontend runtime is missing build directory`.
   A later release rehearsal restored the Production-origin build, but did not restart the
   failed service. Serving that artifact as an accounting sandbox would target the live API.
2. The backend process was RUNNING but returned 503 with `initialization_failed`.
   Its prior boot had no verified release identity. The log confirmed
   `ValueError: verified release source_git_sha is required for startup`.
   Boot identity is captured once, so newly materialized files alone could not recover that process.

## Changes applied to Preview

### Frontend

Source script: `scripts/preview_runtime_repair.py`.
Runtime root: `/opt/mezan-preview-runtime-20260916`.

The script exported 692 tracked frontend files from the exact source commit, excluding `.env*`,
and copied dependencies independently. Static Vite build succeeded (4.20 seconds reported by Vite)
with API origin `https://salla-analytics.preview.emergentagent.com`.
No dependency install, Production dispatcher change or shared artifact write was used.

Only the existing Supervisor frontend command/directory were switched to the independent snapshot.
It serves port 3000 using existing host validation and security headers.
The snapshot publishes `/preview-meta.json`, and `/build-meta.json` returns 404 instead of
claiming a governed Production frontend identity.

Index SHA-256: `03a1bb1690e9ea03e196d5407adc05b890c6913a65da70ff044295edde84e9f1`.
Compiled JavaScript contained the configured Preview API origin (seven occurrences).

### Backend

Source script: `scripts/preview_backend_restart.py`.
Runtime copy: `/opt/mezan-preview-runtime-20260916/backend_restart.py`.

After confirming the Preview backend uses a loopback Mongo host, the expected command,
failed readiness state and an existing release identity, the script restarted only that service.
It added `PYTHONDONTWRITEBYTECODE=1` to prevent new bytecode in the shared release workspace.
Release verification, authentication and authorization remain unchanged. No development/test
startup override was enabled, and no source or identity file was modified.

The backend remains the existing `/app/backend` runtime and retains its valid-release dependency.
This repair does not claim fully independent backend source deployment.

## Fresh verification

- Supervisor section replacement boundary checks: PASS for frontend and backend.
- Static Preview build and launcher compilation: PASS.
- Frontend local `/login`: HTTP 200.
- Preview metadata: HTTP 200, JSON, correct snapshot and Preview API origin.
- Backend `/api/ready`: HTTP 200, `ready:true`, phase `ready`.
- Backend `/api/health`: HTTP 200; verified identity, critical hashes, frontend build,
  backend runtime source and release-control binding all true.
- Public browser: the supplied Preview link resolved to
  `https://salla-analytics.preview.emergentagent.com/login` and rendered the complete sign-in form.
- Frontend and backend RUNNING; Mongo process remained unchanged (PID 75151).
- Shared `/app` tracked and staged diffs: empty.
- All unrelated Supervisor service sections: byte-for-byte unchanged.
- Release lease: active initially, owned by `codex-salla1054-deploy`; final read was inactive.
  This task did not prepare, verify, abort, clear or otherwise mutate that lease.
- Runtime evidence: `/opt/mezan-preview-runtime-20260916/verification.json`.

No account creation, accounting posting, permissions acceptance or cutover was performed.
Database isolation is user-confirmed; loopback Mongo was observed. Provider isolation is not
independently certified by this service-repair task.

## Recovery and maintenance

Prepared runtime and rollback records are outside `/app`.
Frontend rollback restores only its saved Supervisor section, refusing to overwrite later changes:

```sh
python /opt/mezan-preview-runtime-20260916/repair.py rollback
```

This restores the old configuration and may restore its outage. It does not alter data.
Backend before/after sections are retained as `backend-section-before.txt` and
`backend-section-after.txt`; do not replace the entire Supervisor configuration.

Future Preview frontend source refreshes require a new isolated snapshot/build and reviewed service
switch. Keep its API origin on Preview. Backend restarts still require valid release artifacts;
coordinate any shared `/app` change with the active release owner.

Next: sign in to Preview and resume the existing accounting acceptance workflow.
Keep Preview test results separate from Production accounting/cutover acceptance.
