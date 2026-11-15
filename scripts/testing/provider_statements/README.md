# Provider statement page: bounded synthetic browser gate

This harness builds the real `ProviderStatements` component, `AuthProvider`,
operational API methods, and shared CSS. A loopback-only server returns synthetic
owner and statement fixtures. Browser interception blocks every non-loopback
request, including optional remote fonts. No production, database, or provider
connection is used. Visibility events are explicitly simulated in Chromium;
this is not an operating-system tab lifecycle test.

Use the repository's pinned Node toolchain and an existing Playwright install:

```bash
PROVIDER_STATEMENT_UAT_OUTPUT=/absolute/repo-external/output \
python3 scripts/frontend_release_toolchain.py exec -- \
node scripts/testing/provider_statements/build.cjs

PROVIDER_STATEMENT_UAT_OUTPUT=/absolute/repo-external/output \
PLAYWRIGHT_MODULE=/absolute/path/to/playwright \
python3 scripts/frontend_release_toolchain.py exec -- \
node scripts/testing/provider_statements/browser.cjs
```

An optional `PROVIDER_STATEMENT_UAT_CHROMIUM` selects an already installed
Chromium executable. The gate checks desktop and mobile RTL layouts, exact plan
labels and fees, unknown amounts, explicit missing-refund acknowledgement,
bounded viewport width, and no automatic POST on mount, file selection, or a
hidden/visible cycle. A source manifest, count-only summary and synthetic
screenshots are written outside the repository. This fixture build is never a
governed production artifact. Backend parser and transaction correctness are
covered by the separate backend tests.

When Chromium execution is unavailable, `PROVIDER_STATEMENT_UAT_SERVE_ONLY=1`
starts only the loopback fixture server and prints its assigned origin. Adding
`PROVIDER_STATEMENT_UAT_SAVED=1` supplies a saved synthetic statement for manual
browser inspection. This mode does not assert a passed browser gate or produce
screenshots. Stop the process after the inspection.
