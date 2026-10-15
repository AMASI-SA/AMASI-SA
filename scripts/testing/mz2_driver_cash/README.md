# C3 physical cash: isolated browser acceptance

This harness mounts the actual exported `DeliveryPaymentModal`, shared
`DriverPhysicalCash`, and H2 `DriverCashReconciliation`. The default product HTTP
client calls actual FastAPI delivery, proof-upload, cash-reader and reconciliation
routes backed by a UUID-named loopback Mongo replica-set database. It does not
replace API responses, ledger readers, financial identities or financial writers.
It does not exercise the complete driver-app navigation or production auth.

Authentication and the external Salla transport are explicitly test-only. The
existing manual native fixture creates synthetic opening facts and journals;
the existing native COD and settlement writers seed one verified cash handover.
An existing operational-only handover is separately seeded with its recorded
account identity and cannot be presented as a native journal. A historical cash
collection intentionally lacks physical confirmation. All seeding precedes the
financial baseline. No live external system is contacted.

Financial writes are paused for browser execution. Only exact operational
delivery/proof-upload/reconciliation POST paths are allowed by the fixture and
browser. The fixture's source-change endpoint changes one synthetic assignment
after delivery to test evidence preservation; it is not a business cancellation
flow. One browser transport failure is injected **after forwarding the real
delivered request and receiving its committed HTTP 200**. The actual modal then
retries the retained payload and proof. No replacement success response is used.

The proof endpoint fingerprints every native ledger, audit, sequence, owner
control, settings, shipping event/evidence/setup, canonical account/opening fact,
daily movement and imported source-file row. Only the existing owner transaction
`revision` serialization token is excluded; every write-control/activation field
is retained. Operational evidence and append-only reconciliation links are
expected changes, so the full-database hash is separately recorded, not claimed
unchanged. The existing Mongo Legacy-access monitor stays active throughout.

Use existing runtime dependencies and an unused port. Pick a fresh external
output directory and a new stop-file path for each run:

```text
MZ2_TEST_MONGO_URI=mongodb://127.0.0.1:27130/?replicaSet=mz2c
MZ2_CASH_DIST=<absolute external output>/dist
MZ2_CASH_STOP_FILE=<absolute external output>/stop-server
MZ2_CASH_PORT=18770

node scripts/testing/mz2_driver_cash/build.cjs
python scripts/testing/mz2_driver_cash/server.py

PLAYWRIGHT_MODULE=<existing installed playwright module path>
MZ2_CASH_ORIGIN=http://127.0.0.1:18770
MZ2_CASH_OUTPUT=<absolute external output>/browser
MZ2_BROWSER_CHANNEL=msedge

node scripts/testing/mz2_driver_cash/browser.cjs
```

Start background helpers without a visible window. `GET /__test/proof` becomes
available after actual seed writers complete. Creating the configured stop file
gracefully closes the fixture and drops its unique database; never stop the
shared Mongo service. Retain browser results, source manifest, API log,
desktop/mobile screenshots, server output and independent database cleanup proof.
The expected browser console network error is the injected lost response.

Seven checks cover unknown history, explicit amount/confirmation before POST,
real evidence upload, actual-vs-expected variance, exact HTTP replay, actual UI
lost-response retry, multiple-order totals, explicit native/operational matching,
source-change preservation, mobile width, unchanged financial data/controls and
zero Legacy/external browser traffic. This is bounded source UI acceptance,
**not Smoke B or the full 16-stage business UAT**. Production writes remain zero.
