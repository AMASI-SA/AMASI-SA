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


## MFA first-enrollment follow-up — prepared, awaiting user secret entry

Status: SUPERSEDED_BY_USER_PASSWORD_ONLY_REQUEST. The waiting configure process was cancelled before secret entry. No bootstrap credential was created. Service recovery above remains verified;
successful privileged sign-in is still unverified.

The user supplied a Preview login screenshot showing `mfa_bootstrap_not_configured`.
The backend code emits this 503 only after the password route returns 200 for an Owner/Admin
without completed MFA enrollment. Read-only host inspection confirmed:
- effective `MFA_BOOTSTRAP_CODE` absent, not merely too short;
- existing JWT configuration present (no value read back or logged);
- /app HEAD remains `9f8b16998f62cbdb08dc9361bc2e443223d27d17`, tracked diff empty;
- MFA source SHA-256 `9dc25dfc20c08093d0becfdf7ed6a66748824d7e1ba9ce00c41e9172b0eaefda`;
- the previously repaired backend command remains unchanged.

Prepared `scripts/preview_mfa_setup.py`, installed outside /app at
`/opt/mezan-preview-runtime-20260916/preview_mfa_setup.py`.
Its SHA-256 is `2dbed157711db917bd9919462e8b31ca90ec16deeca055cf3e0f4944de7d2dba`.
No bootstrap credential has been generated, stored or changed by this preparation.

The interactive configure action requires the user to choose and repeat a new ASCII secret of
at least 12 characters through hidden terminal input. It never prints the secret, puts it in
argv or writes it into /app. After entry it repeats the preflight, stores an owner-only file
outside /app, changes only the backend Supervisor command to a secret-loading launcher,
restarts only that service and checks readiness and all five release verification flags.
Authentication, roles, enrollment rules, databases and Production files are not modified.
Existing secrets, changed source/configuration and active release leases cause refusal.

Fresh preparation evidence:
- local compilation, backend section isolation, rollback, concurrent-change rejection,
  owner-only file mode and refusal to overwrite a secret: PASS;
- remote `check`: PASS, no service configuration changes;
- focused existing MFA tests:
  `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/app/backend /root/.venv/bin/python -B -m pytest -q -p no:cacheprovider /app/backend/tests/test_mfa_security_v1.py -k 'bootstrap_code or password_only'`
  => 2 passed, 9 deselected (0.34s); only an existing dependency deprecation warning.

Next safe action in dedicated Terminal 6:

```sh
/root/.venv/bin/python -B /opt/mezan-preview-runtime-20260916/preview_mfa_setup.py configure
```

User personally enters the new secret twice. No account password or authentication code should
be sent in chat. The browser credential-creation rule requires this user handoff.
After completion inspect the sanitized `mfa-verification.json`, then repeat normal Preview
login with secure credential entry and let the user complete authenticator enrollment.
Do not bypass enrollment, set account MFA flags in Mongo, or mint an authenticated session.

If activation needs reversal, `preview_mfa_setup.py rollback` restores only the recorded
backend section, refuses changed configuration and retains the private file. This may restore
the original MFA enrollment block. Never re-run configure over a partial installation;
inspect its private-file and Supervisor state first.

Production changed by this follow-up: NO. No merge, publication, account creation, accounting
mutation or permission acceptance. Remote script/configuration verification does not prove
successful login; that acceptance remains pending.


## Password-only Preview — activated and runtime-verified

The user explicitly changed the requirement: this isolated Preview must accept email and
password only, without OTP, authenticator enrollment or passkey requirements.
This supersedes the preceding bootstrap-secret setup and its next-action instructions.

Current status: PASSWORD_ONLY_RUNTIME_VERIFIED; interactive account sign-in still pending.
No Production authentication configuration or source was changed.

### Isolated adapter

- `scripts/preview_password_runtime.py` is installed only at
  `/opt/mezan-preview-runtime-20260916/preview_password_runtime.py`.
- Active adapter SHA-256:
  `02e3dbac817c44da6b009396d472d5f5223f79c7db8c86088df70d229339b10b`.
- Supervisor changes only the Preview backend command to the adapter's `serve` action.
  The base /app source and identity files are unchanged.
- The adapter refuses another container hostname, a non-Preview frontend origin,
  a non-loopback database, or changed hashes of the five reviewed authentication modules.
- In this process only, it removes second-factor policy for login/access/refresh and skips
  TOTP, passkey and email-OTP middleware installation. Password verification, disabled-account
  checks, revocation, role/permission enforcement and login-attempt protection stay intact.
  It does not mutate account roles or MFA enrollment in Mongo and does not fabricate MFA success;
  tokens retain their truthful `mfa:false` claim.
- A new owner-only session-signing file is generated outside /app. Preview sessions use this
  independent key, so they are not base-runtime/Production sessions. Existing Preview cookies
  require a new login. No secret value is printed, committed or returned by diagnostics.
- The outer HTTP boundary rejects Production Host/Origin.
- `/api/preview-auth-policy` explicitly identifies this separate Preview adapter and fingerprint.
  Base `/api/health` verification attests the unchanged /app package, not Production-equivalent
  behavior of the additional Preview policy.

### Verification and resolved activation defect

The initial adapter constructed the base app before Uvicorn's event loop, which caused Motor
startup to fail with a Future attached to a different loop. This was corrected by using the
Uvicorn application factory so the app is created inside the serving loop. The corrected
service was restarted and freshly verified; no release guard was bypassed.

- Final acceptance and existing session-revocation regression command:
  `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/app/backend:/opt/mezan-preview-runtime-20260916 /root/.venv/bin/python -B -m pytest -q -p no:cacheprovider /opt/mezan-preview-runtime-20260916/test_preview_password_runtime.py /app/backend/tests/test_auth_session_revocation_v1.py`
  => **25 passed** in 1.20s; one existing dependency deprecation warning.
- Owner/Admin/accountant/viewer login, authenticated-user resolution and refresh need no second factor;
  wrong passwords, disabled/inactive accounts and revoked sessions still fail.
- Non-Preview runtime and Production request origins fail closed.
- Default base policy still requires a second factor when the adapter is not applied.
- Live local readiness, health and explicit Preview policy endpoints: HTTP 200.
- Readiness `ready:true`; policy `authentication:email_password`,
  `second_factor_required:false`, `session_signing:preview_only`.
- Base release health: all five source/identity verification flags true.
- A local request with Production Host was rejected with 403; no Production HTTP request was sent.
- Frontend PID 117486 and Mongo PID 75151 unchanged; corrected backend PID 130800 running.
- /app tracked and staged diffs empty; unrelated Supervisor sections byte-identical.
- Bootstrap secret absent; independent session key mode 0600.
- Sanitized evidence: `/opt/mezan-preview-runtime-20260916/password-verification.json`.

No real test-account creation, accounting mutation, permission acceptance, Production publication
or Production data change. Browser login acceptance remains a separate final check.

Rollback restores only the recorded Preview backend section and refuses later config changes:

```sh
/root/.venv/bin/python -B /opt/mezan-preview-runtime-20260916/preview_password_runtime.py rollback
```

This re-enables the previous MFA policy and may restore the original missing-bootstrap block.
The private signing file is retained. Do not rerun activate over an existing installation.

Next: secure email/password entry on the existing Preview /login form, verify a protected page,
then hand off to the accounting conversation. Do not run the superseded bootstrap configure step.


### Preview browser-cookie origin correction

The first browser attempt after activation returned 403; sanitized backend request-status logs
confirmed login/refresh rejection and authenticated-user 401. This was not evidence of a wrong
password. The unchanged base BrowserSecurityMiddleware trusts FRONTEND_URL independently of CORS.
The runtime inherited the previous frontend origin, so Preview requests carrying stale cookies
were rejected by the existing CSRF policy.

The Preview adapter now binds FRONTEND_URL and CORS_ORIGINS to its exact Preview origin before
importing server. The CSRF middleware remains intact; the outer Preview boundary still rejects
Production origins. No .env or Production setting was changed.

A targeted regression first failed for the intended reason (403 instead of 204); after the fix
the complete focused suite passed **24 tests**. Corrected live ready/health/policy remain HTTP 200.
A POST to local /api/auth/refresh with the Preview Origin and a deliberately invalid test cookie
now reaches session validation and returns 401 Invalid refresh token, proving origin acceptance
without admitting an invalid session. No real credentials were used in this probe.

Current checkpoint before interactive login: 63eb8f7a51c2bf0c8cec4c35d27ef4e187572327.
Browser account login still requires a fresh secure credential submission after this correction.


### Verified gateway Origin — final cookie-session recovery

The cloud browser's remaining refresh 403 was traced by a one-time, credential-free
boundary diagnostic. The request Host was allowed; the gateway supplied HTTPS Origin
`salla-analytics.cluster-12.preview.emergentcf.cloud`, the same Preview upstream already
identified in the original outage. No cookies, passwords, tokens or request bodies were logged.

The final adapter trusts exactly the public Preview origin and this verified Preview gateway
origin. It configures CORS accordingly and retains the original BrowserSecurityMiddleware
with this two-origin CSRF allowlist. It does not disable CSRF or allow Production origins.
The adapter still refuses every other runtime host and requires loopback Mongo and pinned
base auth sources.

Final code/test checkpoint: `9ed29085b738811e9d09d69d017a48868e2d4cfb`.
Fresh focused tests: **25 passed**, 1 existing dependency warning, 1.20s.
Live ready/health/policy: HTTP 200, ready true, all five base release flags true.
Deliberately invalid refresh-cookie probes: public Preview 401; gateway Preview 401;
Production Origin 403. Thus both Preview origins reach session validation while invalid
sessions and Production-origin requests remain rejected.

Fresh cloud-browser evidence: the session-error screen cleared after retry and redirected to
the ordinary Preview /login email/password form. No successful account login is claimed yet.
Current backend PID 130800; frontend and Mongo unchanged; /app tracked/staged diffs empty.

Next: one secure email/password submission on the recovered /login form, verify a protected
page, and record final authentication acceptance. No bootstrap or OTP setup is needed.
