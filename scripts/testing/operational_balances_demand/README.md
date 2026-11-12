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
Playwright's installed Chromium with its sandbox enabled; an explicitly supplied
`OPERATIONAL_UAT_CHROMIUM` may select another installed executable without
disabling the sandbox. Outputs include exact source hashes,
browser assertions, request counts, and desktop/mobile dialog screenshots.

This is component acceptance, not a substitute for the repository's governed
v5 build, authenticated real-Mongo acceptance, exact-source CI or live probes.

The `Operational Balance Web` workflow runs all six scenarios on an isolated
Ubuntu 22.04 GitHub Actions runner using Node 22.23.2 and Playwright 1.58.2.
It retains the exact checked-out commit/tree, component hashes, request counts,
assertion results and desktop/mobile screenshots as one browser evidence artifact.
