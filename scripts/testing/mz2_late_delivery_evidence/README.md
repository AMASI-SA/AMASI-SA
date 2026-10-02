# Late delivery evidence: connected loopback acceptance

Prepared harness; execution evidence must come from a subsequent run. Use the
integration foundation that preserves Production `83363097`'s
`STORE_DRIVER_NATIVE_ROUTE_PREFIXES`, including `/api/store-delivery/evidence`.
Older foundations correctly fail this harness's real mobile route gate. Do not
bypass that gate or broaden it to make the harness pass.

The actual two product components use their default HTTP client. Actual upload,
list, original-download and review routes run against an isolated UUID database.
Only authentication identity selection is synthetic: persisted fixture users pass
through the real `mobile_app_request_user`; this is not a production login test.
The native fixture seeds its existing opening before the baseline. Two delivered
orders have sealed original C3 evidence, one with an original proof and one with
recorded absent proof. Financial writes are paused before browser execution.

Only late-evidence upload/review POSTs and list/original GETs are exposed at the
API boundary. No financial endpoint is mounted or allowed. Loopback Mongo without
credentials and an explicit replica set is required. No external API is called.
The browser aborts external requests and all unapproved mutations. One upload
response is deliberately lost only after forwarding the real request and seeing
HTTP 200; retry must retain its request ID and return the same attachment.

Acceptance covers approved and rejected evidence, original-image inspection,
view-only permission denial, note validation, driver-visible decisions, mobile
width, unchanged original delivery/C3/financial/control state, and zero monitored
Legacy access. The baseline fingerprints every collection except append-only
late-evidence events, new proof rows marked `origin=late_attachment`, and the
operational owner serialization `revision` alone. All other owner control fields
remain protected. This is bounded connected component acceptance, not Smoke B,
full application navigation, production authentication, or the full business UAT.

## Run manifest

Run only after the parent approves the foundation and task-local services. Use
existing Node 22, Python virtual environment, frontend dependencies and Playwright;
do not install dependencies or load production environment files. Select a fresh
absolute external evidence directory and a fresh stop-file path. Commands below
are a manifest, not evidence of execution. Run from the selected repository root.

```powershell
$env:PYTHON_DOTENV_DISABLED='1'
$env:PYTHONPATH="$PWD/backend;$PWD/backend/tests"
$env:MZ2_TEST_MONGO_URI='mongodb://127.0.0.1:27128/?replicaSet=mz2test'
$env:MZ2_LATE_DIST='<absolute fresh evidence directory>/dist'
$env:MZ2_LATE_STOP_FILE='<absolute fresh evidence directory>/stop-server'
$env:MZ2_LATE_PORT='18772'
$env:MZ2_LATE_ORIGIN='http://127.0.0.1:18772'
$env:MZ2_LATE_OUTPUT='<absolute fresh evidence directory>/browser'
$env:PLAYWRIGHT_MODULE='<absolute existing playwright module>'
$env:MZ2_BROWSER_CHANNEL='msedge'
& '<existing Node 22 executable>' scripts/testing/mz2_late_delivery_evidence/build.cjs
& '<existing Python virtualenv executable>' scripts/testing/mz2_late_delivery_evidence/server.py
# In a second shell with the same environment, after /__test/proof is ready:
& '<existing Node 22 executable>' scripts/testing/mz2_late_delivery_evidence/browser.cjs
# Finally, request graceful fixture teardown (including after browser failure):
New-Item -ItemType File -Path $env:MZ2_LATE_STOP_FILE
```

The server command runs until stopped; if launched as a background helper, use
`Start-Process -WindowStyle Hidden` and capture stdout/stderr externally. Preserve
the exact checkout HEAD/tree/status, runtime versions, command/exit logs,
`dist/source-manifest.json`, browser JSON, screenshots and server shutdown JSON.
The browser records actual request/response statuses and before/after hashes;
the expected failed upload request represents the injected lost response.

Teardown closes the inherited fixture and drops only its UUID database. Retain
the database name from proof and independently verify its absence after shutdown
using the same loopback URI. Never stop the shared Mongo process or remove another
database. On abnormal server termination, clean up only the exact recorded UUID
database after checking its fixture prefix and loopback connection. A missing
cleanup proof or failed integrity check must remain a failed/incomplete run.
