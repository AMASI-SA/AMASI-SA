# Track H local synthetic visual review

Run from repository root with the repository-supported Node 22 toolchain:

`node frontend/node_modules/vite/bin/vite.js --config scripts/testing/mz2_track_h/vite.config.mjs --configLoader native`

Open http://127.0.0.1:4318. The harness imports the actual changed production components. Its dedicated Vite config replaces only the API transport with explicit synthetic fixtures and rejects all writes and unknown reads. It is not referenced by the application entry or production Vite config. No authentication secrets or live data are used.

Check six screens at 1440x1000 and 390x844, report drilldown and Escape/focus return, search, domain selectors, and error/empty/loading/unavailable links. Record screenshots and viewport overflow. Financial figures here are test data only; this is not live backend integration or financial execution proof.
