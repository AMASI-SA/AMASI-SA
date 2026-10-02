# C5 isolated 16-stage business setup acceptance

This harness executes the shipped `AccountingOnboarding` component, default HTTP
transport, shipped FastAPI router and replica Mongo. It creates one UUID database,
one setup owner and one separately paused guard owner. Neither is Production.
The initial setup owner's explicitly unpaused fixture enables the existing source
upload API; its control flags never change. The second owner begins paused and
is used only for the separate nonexistent-session404/opening-draft423 assertion.
The latter must never be described as the reviewed setup owner's state.

All source facts and expected preview legs are declared in `source-register.json`
before the browser runs. Existing identities, native paid invoice and catalogue
are synthetic prerequisite records, identified in the fixture manifest. The
browser creates the onboarding session and performs actual inputs, original-file
uploads, saves, reloads and review. It must not call post, transition, activate,
write-control, inventory approval, or any financial writer. No journal is allowed.
Physical inventory values are planned metadata, not an attested count. Stage16
acceptance means the deliberate final lock and separate paused-owner refusal.

JWTs are minted as explicit synthetic authenticated-session fixtures using the
existing auth helper. All business requests use the actual JWT database verifier
and fresh persisted accounting permissions. This is not a login/MFA acceptance
test, nor a deployed full-application/Production proof. Smoke B remains a separate
full-app real-login scenario.

Run only against an explicit loopback replica with dotenv disabled. `build.cjs`
requires `MZ2_C5_DIST`; `server.py` requires `MZ2_TEST_MONGO_URI`,
`MZ2_C5_EVIDENCE`, `MZ2_C5_DIST`, `MZ2_C5_EXECUTE=isolated-authorized`, and a fresh
`JWT_SECRET`. `browser.cjs` requires `MZ2_C5_ORIGIN`, `MZ2_C5_EVIDENCE`, and the
installed Playwright module through `PLAYWRIGHT_MODULE`. It uses headless Edge.
No production server, credentials, network integration or scheduler is imported.

The server writes only its named synthetic fixture and cleans up exactly that
database at shutdown. Before/after fingerprints, sources, response evidence,
browser screenshots and failures are retained externally. No PASS is claimed
from preparation or build alone. Root freezes the exact source before execution.
