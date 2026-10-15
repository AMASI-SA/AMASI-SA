# H2 synthetic review evidence

Run from repository root with Node22:

`node frontend/node_modules/vite/bin/vite.js --config scripts/testing/mz2_track_h/vite.config.mjs --configLoader native`

Open `http://127.0.0.1:4318/?page=home`, then a panel under مراجعات اليوم. The harness aliases only the real transport; actual UI components are rendered. New H2 fixtures use the exact native E/F/G read contracts recorded in their contract documents. They do not make those routes exist on the H base backend.

All writes and unknown reads throw. No customer records, API credentials, external calls, cutover, policy activation or financial actions. Available, empty, loading, error and unavailable query states are synthetic. Prepaid fixture supports the explicit date2026-10-01 only; other dates return a fixture-unavailable blocker.

Screenshots are local viewport captures at1440x1000/390x844. Production was not opened or mutated during H2 integration verification.
