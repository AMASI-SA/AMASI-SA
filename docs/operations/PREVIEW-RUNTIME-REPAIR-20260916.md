# Preview runtime repair — 2026-09-16

Task: PREVIEW-RUNTIME-REPAIR-20260916. Scope: isolated Preview frontend only.
User authorizes a new independent terminal and Preview repair; no Production publication or data changes.

## Confirmed diagnosis

Source inspected: `9f8b16998f62cbdb08dc9361bc2e443223d27d17`.
Supervisor `frontend` was FATAL while backend and MongoDB were RUNNING.
The error log explicitly showed `Governed frontend runtime is missing build directory`.
Supervisor ran `yarn start` in `/app/frontend`, entering the strict Production artifact launcher.
A later release rehearsal populated the build, but that artifact embeds the Production API origin.
It must not be served as the accounting sandbox.

The `codex-salla1054-deploy` release lease was active at inspection and remains owned by the other task.
No lease, Production source, `/app` build, backend or database changes are part of this repair.

## Isolated recovery

Source script: `scripts/preview_runtime_repair.py`.
Runtime root: `/opt/mezan-preview-runtime-20260916`.
The script exports tracked frontend files from the exact source commit, excludes all `.env*` files,
copies frontend dependencies independently, and creates a static Vite build with API origin
`https://salla-analytics.preview.emergentagent.com`.
It uses a minimal child environment and does not install dependencies or change the Production build dispatcher.

Activation changes only the existing Supervisor frontend command and directory.
Other service sections are preserved. The original frontend section is retained outside `/app`.
The new frontend runs on the same port 3000 with the existing host validation and security headers.
Preview publishes `/preview-meta.json`; it must not claim a governed Production `build-meta.json`.

## Operations

Run on the authorized Preview host only, using the task-owned terminal:

```sh
python /opt/mezan-preview-repair-20260916.py prepare
python /opt/mezan-preview-repair-20260916.py activate
```

Prepare refuses an existing workspace and changed source SHA; activation refuses unexpected frontend
configuration, an occupied port, or an existing backup. Inspect instead of overwriting such state.

Rollback of only this service configuration:

```sh
python /opt/mezan-preview-runtime-20260916/repair.py rollback
```

Rollback refuses to overwrite a frontend section changed by another task. It restores the prior
startup configuration, which may reproduce the original outage; it does not modify source or data.

## Verification status

- Local Supervisor section isolation check: PASS (backend and adjacent service sections preserved).
- Remote static build: in progress.
- Supervisor activation, local HTTP probes and public browser acceptance: pending.
- Login/account creation/accounting acceptance: not performed in this repair.
- Database isolation is user-confirmed; this repair does not independently certify database/provider isolation.

The runtime is a fixed snapshot. Future Preview source refreshes require a new isolated build and
an explicitly reviewed service switch. Production releases must not overwrite its runtime directory.
