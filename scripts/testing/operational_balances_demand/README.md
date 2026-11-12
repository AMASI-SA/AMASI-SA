# Demand-only balances component acceptance

This fixture uses the real financial report, native pending-order dialog,
visibility reader, table rendering and stylesheet. All amounts and identities
are synthetic. It has no production credentials, database access or release
output. Its HTTP server binds loopback, accepts only fixture requests, and the
browser blocks every external destination.

Run `fixture.py <absolute output directory>` with the backend test environment,
then `build.cjs` and `browser.cjs` with the pinned release Node binary. Set
`OPERATIONAL_BALANCE_UAT_OUTPUT` to the same repo-external directory and
`PLAYWRIGHT_MODULE` to an installed Playwright package. The browser defaults to
the system `/usr/bin/chromium` executable. Outputs include exact source hashes,
browser assertions, request counts, and desktop/mobile dialog screenshots.

This is component acceptance, not a substitute for the repository's governed
v5 build, authenticated real-Mongo acceptance, exact-source CI or live probes.
