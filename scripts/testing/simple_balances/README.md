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
The isolated output includes source hashes, twelve full-page screenshots, and
`browser-evidence.json` recording twelve acceptance scenarios. Screens cover the
ledger, external entry, refund request, and permissions editor at 1440px and 390px,
plus compact salary/history, period statement and reviewed receipts on mobile.
Each capture asserts no horizontal body overflow. The permissions table may
scroll inside its own bounded container.

The approved supplier payout scenario reads only bootstrap metadata and its
selected supplier before a funding kind is chosen. It then requests the exact
source-options page for that kind; it cannot fetch the home, whole directory,
legacy context, or another funding group. The remaining legacy screen scenarios
retain their existing transport contracts.

This layer verifies component rendering and transport contracts with fixtures.
The separate backend tests verify authorization, persistence, ceilings and
financial effects. The existing browser-acceptance job runs both this harness
and the established operational financial-position harness without weakening
either gate.

For manual inspection of the same actual components and synthetic endpoints,
set `SIMPLE_BALANCES_SERVE_ONLY=1` and run `browser.cjs --port 4174` after building.
This serves the fixture only; it neither launches browser automation nor writes
to any live API. The statement print scenario stubs only the system print dialog
and verifies that a fresh `purpose=print` request precedes it.
