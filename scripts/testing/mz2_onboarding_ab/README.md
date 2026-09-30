# Connected A+B source integration proof

This fixture serves the actual `AccountingOnboarding` component with its default
HTTP client against the real Track A router. The only authentication seam is a
synthetic user already inserted by the existing real-Mongo test fixture. It does
not import `backend/server.py`, load deployment secrets, or contact a live system.

Use a disposable loopback Mongo replica set. The existing fixture creates a
UUID-named database and drops it on graceful server shutdown. Initial synthetic
accounts/evidence are seeded with the existing canonical services, then the test
owner is paused. Browser interaction must change only onboarding sessions; a
complete fingerprint of every other collection is compared at the end.

From repository root, with Python test dependencies and frontend dependencies
installed, set `PYTHONPATH=backend:backend/tests` (semicolon on Windows),
`PYTHON_DOTENV_DISABLED=1`, and `MZ2_TEST_MONGO_URI` to the local replica set.
Set `MZ2_AB_DIST` to an absolute output directory outside tracked source.

1. `node scripts/testing/mz2_onboarding_ab/build.cjs`
2. `python -m uvicorn scripts.testing.mz2_onboarding_ab.server:app --host 127.0.0.1 --port 18761`
3. Set `MZ2_AB_ORIGIN=http://127.0.0.1:18761`, `MZ2_AB_OUTPUT` to an artifact
   directory, and run `node scripts/testing/mz2_onboarding_ab/browser.cjs`.
4. Gracefully stop the fixture server.

Install Playwright in the test environment or set `PLAYWRIGHT_MODULE` to its
installed module. `MZ2_BROWSER_CHANNEL=msedge` selects installed Edge on Windows;
otherwise Playwright Chromium is used. No fixture build uses the governed
deployment build entry point or generates a release intent.

Twelve executable scenarios cover create/list/get, cutover save, financial
save/reopen/explicit zero, CAS/idempotency, evidence-backed N/A, bank mapping,
per-account valuation, preview/review lock, readiness hard holds, 16-stage RTL
mobile navigation, and zero financial side effects. Negative HTTP cases use a
separate Node client so intentional 409/422 responses are distinguished from
unexpected browser console/page errors. No post, transition, approval, or
activation endpoint is invoked.
