# C2 driver review history: isolated browser acceptance

This harness renders the actual H2 `DriverPanel` and its default HTTP client.
It serves the existing native FastAPI readers against a UUID-named disposable
Mongo replica-set database. It is source UI acceptance, **not Smoke B or the
full 16-stage business UAT**, and never loads the production server or data.

Startup uses the delivered test-only native fixture and actual writers to seed
55 decisions: ordinary rejections, rejection revision 1 followed by actual driver
resubmission and POS approval revision 2, a verified imported-bank approval and
an existing native reversal. A separate rejection's event is removed before the
baseline to prove missing-revision coverage. It creates then revokes a synthetic
viewer's persisted permission while retaining stale test-auth claims.

After seeding, financial writes are paused. The entire database is fingerprinted;
no collection is excluded. Browser traffic is GET/HEAD only and loopback only.
The existing Mongo Legacy-access monitor remains active. The fixture drops its
database on graceful shutdown. Synthetic authentication is only in this harness;
production authentication itself is not being tested.

Use the existing project Python environment and frontend dependencies. Choose a
fresh external output directory and a new stop-file path for each run:

```text
MZ2_TEST_MONGO_URI=mongodb://127.0.0.1:27130/?replicaSet=mz2c
MZ2_HISTORY_DIST=<absolute external output>/dist
MZ2_HISTORY_STOP_FILE=<absolute external output>/stop-server
MZ2_HISTORY_PORT=18769

node scripts/testing/mz2_driver_history/build.cjs
python scripts/testing/mz2_driver_history/server.py
```

Start the local server without a visible window when using a background helper.
Its `GET /__test/proof` becomes available after all native seed operations finish.
The browser requires a locally available Playwright package and Chromium/Edge:

```text
PLAYWRIGHT_MODULE=<existing local playwright module path>
MZ2_HISTORY_ORIGIN=http://127.0.0.1:18769
MZ2_HISTORY_OUTPUT=<absolute external output>/browser
MZ2_BROWSER_CHANNEL=msedge

node scripts/testing/mz2_driver_history/browser.cjs
```

Create the configured stop file to request graceful shutdown. Do not stop a
shared Mongo service. Retain `browser-results.json`, desktop/mobile screenshots,
`mobile-dimensions.json`, `dist/source-manifest.json`, and server output. The
expected browser console 403 is the intentional revoked-permission scenario;
unexpected page errors, external traffic or database changes fail acceptance.

The browser checks native source markers, explicit incomplete coverage, actual
50-row cursor pagination over all 55 decisions, server filters, POS canonical
identity/name and accountant, separate bank identity, historical rejection and
reversal labels, mobile document width, revoked access and unchanged data.
No UI, financial adapter, writer, guard or test assertion is replaced.
