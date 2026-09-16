# Login authentication replica-startup checkpoint

## Status and boundaries

- Date: 2026-09-16.
- Status: local root-cause fix verified at the covered test seams; production recovery is unverified.
- Repository: `AMASI-SA/AMASI-SA`; task branch: `fix/auth-replica-startup-20260916`.
- Original investigation base: `244145aeb55a8bdb7c4559651e27b99822878627`. The scoped fix was transferred cleanly to reviewed upstream base `1dec19e1ab9df15739a48b3dff90b531519c4e6f` in an isolated worktree.
- The user authorized commit, push, PR, merge, and deployment by responding "continue" to the explicit request for those actions after the remaining checks pass. This supersedes the earlier local-only boundary for this login fix, not for accounting work.
- Candidate preparation has not changed Production, financial data, or auth policy. Record exact source/intent SHAs, PR/check states, and later deployment evidence in Issue #1006; do not infer live recovery from a checkpoint.
- Existing accounting changes belong to the separate `MZ2-FIN-CUTOVER-001` task and must be preserved, not staged with this fix by default. Its live permission scenario remains blocked.

## Fresh upstream comparison

Read-only GitHub inspection found branch head `1dec19e1ab9df15739a48b3dff90b531519c4e6f` (merge of PR #1045).
At that exact commit, these complete files are byte-for-byte equal to this checkout's pre-fix HEAD:

- `backend/auth.py`
- `backend/startup_guard.py`
- `frontend/src/context/AuthContext.jsx`
- `frontend/src/reviewCustomerWaiting.js`

The `_local_startup` section of `backend/server.py` is also identical to the pre-fix section and lacks per-replica auth installation. This confirms that the identified source defect remains in that upstream snapshot. It does not identify the currently deployed production artifact or prove live replica routing.

Source: https://github.com/AMASI-SA/AMASI-SA/commit/1dec19e1ab9df15739a48b3dff90b531519c4e6f

## Proven mechanism and local changes

`run_release_startup` runs global initialization once per release; followers run local initialization only. Previously, auth middleware installation was reached only through `seed_admin` in global initialization. ASGI middleware belongs to each process, not to the shared database, so a follower could become ready without those guards.

The canonical password-login function then returns HTTP 200 with password-only tokens, while `get_current_user_from_db` rejects those tokens for employees requiring email OTP and privileged users requiring MFA. This can occur on one follower; cross-replica routing is not required.

Changes:

- `backend/auth.py`: expose `install_process_local_auth_security`; retain the old private entry point as a delegating compatibility wrapper. Keep the existing seed caller and auth policies.
- `backend/server.py`: await auth installation first in `_local_startup`, before workers and readiness. An installation failure propagates instead of advertising readiness. Existing app-level flags prevent duplicate installation on the leader; followers repeat identical index definitions during installation.
- `frontend/src/context/AuthContext.jsx`: reject login when session hydration returns no user, preventing a false success message. Preserve all challenge paths.
- `frontend/src/reviewCustomerWaiting.js`: restrict protected review-queue polling to the review page. This is an adjacent request-scope fix, not the proven root mechanism.
- Regression files: `backend/tests/test_auth_security_replica_startup.py`, `frontend/src/context/AuthContext.test.jsx`, and `frontend/src/reviewCustomerWaiting.test.js`.
- `.github/workflows/runtime-stability.yml`: run the replica/startup regressions and review-queue polling test in CI; include their paths in triggers and use Node 22.23.2.

An earlier cookie-deletion-race hypothesis was rejected by a separate ASGI experiment: deletion on an injected Response before raising HTTPException was not emitted in the exception response. Do not present that hypothesis as the cause.

## Verification evidence

The new backend regression file covers 14 cases. It executes the canonical login function extracted from `server.py`, real email-OTP/MFA middleware, token/cookie helpers, and the current-user policy. Database persistence, mail delivery, and selected setup boundaries are test doubles; this is not a full deployed-app/browser test.

- Unprotected canonical login returns 200; the current-user policy rejects its password-only token with 401.
- Protected employee login returns 202 email OTP and clears password-only cookies; correct verification issues session cookies accepted by the current-user policy.
- Owner bootstrap returns 202.
- Owner with enrolled TOTP receives a challenge; a six-digit code outside the accepted window returns 401 without session cookies; the correct code returns 200 and a session accepted by the current-user policy. Time is fixed to avoid TOTP-window flakes.
- Installer order, compatibility, and error propagation are checked. Executing extracted local startup with an installation failure leaves readiness unset.
- Existing leader/follower startup tests confirm release-global versus process-local execution.

Independent combined run: **91 passed**, exit 0, in 10.98 seconds. The 16 warnings are short test-only JWT keys in older MFA/mobile tests, not observed production key findings.

Run from the repository root:

```bash
uv run --offline \
  --with pytest==9.0.3 --with pytest-asyncio \
  --with fastapi==0.140.0 --with motor==3.3.1 \
  --with python-multipart==0.0.31 --with bcrypt==4.1.3 \
  --with PyJWT==2.13.0 --with passlib==1.7.4 \
  pytest -q \
  backend/tests/test_auth_security_replica_startup.py \
  backend/tests/test_startup_guard.py \
  backend/tests/test_email_otp_security_v1.py \
  backend/tests/test_mfa_security_v1.py \
  backend/tests/test_auth_cookie_only_browser_v1.py \
  backend/tests/test_mobile_session_security.py \
  backend/tests/test_login_security_v1.py \
  backend/tests/test_progressive_login_security_v1.py \
  backend/tests/test_runtime_stability_mongo_auth.py \
  backend/tests/test_admin_seed_hardening_v1.py
```

The previous verification turn also ran Node 22.23.2 function-level checks for the frontend false-success guard, preservation of all four challenge flags, and review-page polling. These are not React/Jest or browser end-to-end results.

## Remaining checks and safe next step

- Frontend `node_modules`, React/Jest, and the required backend `httpx`/`httpcore` and `webauthn`/`cbor2` dependency sets are unavailable locally. Full frontend, Passkey, and ASGI boot suites and a governed release build remain unverified. No dependency or lockfile substitution was made.
- Authorization now covers the reviewed login fix through deployment after the required checks. Do not ask again for the same granted authority; do not broaden it to other tasks or bypass a failing check.
- Preserve this isolated candidate as source A and a Draft PR. Re-run required checks using complete dependency sets in GitHub CI, including the new regressions and existing frontend/Passkey suites.
- The current `Mezan Release Readiness` workflow builds candidate A twice and uploads `release-v5-reviewed-intent-candidate`. Validate/download that exact artifact, commit only `release/release-intent-v5.json` as B, and require the reviewed-B clean-clone rehearsal before merging with A/B ancestry preserved. CI does not deploy.
- Follow the current `AGENTS.md` Release Guard v5 source-A/intent-B protocol. Do not reuse another task's intent, lease, build, or production verification.
- After an authorized deployment, verify the governed runtime identity, then test employee email OTP and owner TOTP through the browser, including a reload that retains the authenticated session. Credentials and codes must use the secure browser-auth handoff.
- Do not claim that live login is restored until that deployed verification succeeds.
