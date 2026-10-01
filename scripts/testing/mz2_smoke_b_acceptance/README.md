# Acceptance-only Smoke B — prepared, not executed

User scope: after C3 approval, exercise the existing paused barrier on a disposable
local Acceptance instance. This is not Production verification and never updates
production_verified, write-control, activation, opening state, or product guards.

The root task clarified that idle polling loops in an isolated test process are
allowed. Full server startup is retained. Qoyod pipeline, Qoyod automatic sender
and Salla token maintenance are unconditional startup tasks; the database has no
integration credentials, due jobs, orders or provider setup. A test-only Python
audit hook denies all non-loopback connections instead of faking provider replies.
Any such attempt fails Acceptance even when application code catches the refusal.

## Prepared files

- preflight.py: read-only HEAD/TREE/status and file-hash manifest; no app/Mongo import.
- probe.py: real HTTP password login, canonical pause read, missing session GET404,
  valid opening-draft POST423 and trusted whole-database comparison. No auth override.
- run.py: explicit execution flag, fresh UUID database, minimal child environment,
  supported APP_ENV=test and TEST_RELEASE_STARTUP_KEY=test:... startup, actual
  server.app/uvicorn, readiness wait, probe, evidence and exact PID/database cleanup.

## Execution, only after C3 completion and root go

Use the existing repository virtualenv Python, with the new harness committed:

    python scripts/testing/mz2_smoke_b_acceptance/run.py --execute-after-c3-approved --mongo-uri "mongodb://127.0.0.1:27130/?replicaSet=mz2c" --port 18769 --evidence <new-external-evidence-directory>

The launcher refuses dirty source. It seeds only a random synthetic owner and
explicit paused owner control. No opening/session/provider credentials are seeded.
The password/JWT secret are generated in memory; dotenv loading is disabled through
supported PYTHON_DOTENV_DISABLED=1 (verified in installed dotenv/main.py). No local
secret files are read. Login initialization precedes the fingerprint baseline.

The whole-database fingerprint includes documents, indexes and collection options
for every collection. No auth, lease or financial collection is excluded. If an
idle background worker or auth audit changes anything, the strict comparison fails;
retain the full collection-level evidence and classify the specific mutation before
any further claim. Do not silently weaken the comparison or infer financial safety
from counts. Raw document contents/password hashes/JWTs are not persisted.

Recorded identity includes actual HEAD/TREE, complete backend source hashes, runtime
health output, child PID, exact URI/database, hello replica identity, and post-probe
source recheck. The supported test startup key is never labelled a verified
Production release identity. The resulting label is ACCEPTANCE_PROBE_PASS only;
production_verified remains false in the evidence. A failed run is not retried into
PASS and its artifacts are retained.

No probe, server startup, Mongo write, authentication, financial action, activation,
production access or scheduled production work has been executed during preparation.

## Real auth environment adapter (run3 follow-up)

Full startup requires SMTP configuration even for Owner, because email OTP remains
mandatory for employees. The runner now binds a genuine ephemeral loopback SMTP
receiver, sets supported SMTP host/port with local plaintext transport, and keeps
messages in RAM only. This does not disable OTP. The synthetic Owner follows the
real existing TOTP policy: password plus generated test bootstrap secret → actual
202 setup challenge → RFC6238 authenticator code from server-returned setup secret
→ real /auth/mfa/verify200. Unexpected password-only success is rejected. Tokens,
setup secrets and codes are redacted from artifacts. No auth dependency override
or generated provider response is used. /ready initialization_failed now aborts
immediately rather than polling to timeout. These changes require a new clean
committed harness checkpoint before rerun.

Canonical control URL: `/api/financial-provider-apps/accounting-module/write-control` (installed on existing prefixed financial-provider router; also used by AccountingWriteControl.jsx). Onboarding uses separate `/api/accounting-module/onboarding` setup router. Run4 authenticated successfully but used the wrong unprefixed control URL and is retained as a failed harness run.
