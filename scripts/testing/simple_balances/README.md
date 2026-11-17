# Simple balances browser acceptance

This isolated harness imports the actual `SimpleBalances`, `SimpleBalanceAccess`,
`SimpleOrderRefunds`, auth provider and API adapters. A loopback HTTP server supplies
only synthetic records. It does not access a live tenant, replace application
modules, build a governed release, or call a payment provider.

With the locked frontend dependencies installed, run from the repository root:

```bash
export SIMPLE_BALANCES_UAT_OUTPUT=/absolute/path/outside-the-repository/simple-balances-browser
export PLAYWRIGHT_MODULE=/absolute/path/to/playwright
node scripts/testing/simple_balances/build.cjs
node scripts/testing/simple_balances/browser.cjs
```

An installed system Chromium can be selected with `SIMPLE_BALANCES_UAT_CHROMIUM`.
The browser runs with its sandbox enabled and blocks every non-loopback request.
The isolated output includes source hashes, eight full-page screenshots, and
`browser-evidence.json` recording eight acceptance scenarios. Screens cover the
ledger, external entry, refund request, and permissions editor at 1440px and 390px.
Each capture asserts no horizontal body overflow. The permissions table may
scroll inside its own bounded container.

This layer verifies component rendering and transport contracts with fixtures.
The separate backend tests verify authorization, persistence, ceilings and
financial effects. The existing browser-acceptance job runs both this harness
and the established operational financial-position harness without weakening
either gate.
