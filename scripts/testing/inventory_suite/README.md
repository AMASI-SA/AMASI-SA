# Isolated review of the real Mezan inventory frontend

Test infrastructure only; never deploy this authentication or these data fixtures. The review app labels all data as synthetic. Production routes, balances and provider credentials are not used.

- `preview-server.cjs --isolated-test-data`: UI4174/API4175 on127.0.0.1 only; serves the actual `frontend/build` with a response-only loopback CSP, forwards native requests only to4176, and uses explicit display fixtures for other screens.
- `native-preview-api.py --isolated-test-data`: accepts only a validated loopback replica, creates `inventory_preview_<uuid>`, and uses the original purchase/count/entry/product-draft services and synthetic integration seed. Provider publishing is blocked by middleware. Graceful exit drops its own UUID DB; a forced kill may leave that disposable fixture DB.
- `frontend-browser-check.cjs`: isolated Chrome context, actual compiled App/guards/Layout, controlled API responses;21 desktop/mobile captures/assertions including form/reload errors, recovery, navigation and focus.
- `native-workflow-browser.cjs`: real HTTP gateway/ASGI/native Mongo writes;15 captures/assertion groups for canonical purchase options, partial count review/post/audit, standalone entry and product drafts. Start from a fresh preview API database: pending-valued standalone entries correctly block later accounting posting readiness.
- `native-vite.config.mjs`: optional development config. Acceptance used the actual repository Vite build config and compiled App.

## Local prerequisites and boundaries

Use the repository's frontend/backend dependencies and pytest/pytest-asyncio/httpx plus the existing integration-test imports. Playwright uses installed Chrome (`channel: chrome`) in its own profile; the user authorized this local test. `MEZAN_PLAYWRIGHT_MODULE` can point to an installed Playwright package; `MEZAN_INVENTORY_BROWSER_OUTPUT` selects a durable screenshot directory. Neither is a runtime provider.

Verified dedicated fixtures: replica27653 (`inventoryLocal`), standalone negative-test instance27654, and the older eligibility fixture's replica27462 (`operationalphysical`). Test helpers accept loopback URIs and use unique disposable database prefixes. Never point them at production or reuse an unrelated database.

## Build and start

From `frontend`, set `REACT_APP_BACKEND_URL` to `http://127.0.0.1:4175` and `NODE_ENV` to `production`, then run:

```text
node node_modules/vite/bin/vite.js build --mode production
```

This compiles source for local testing only; it creates no reviewed release identity/artifact. Do not run `yarn build`, the deployment adapter, release preparation or spoof GitHub Actions to create this preview.

From the repository root, in separate terminals with the local Python environment and Playwright installed, set `MZ2_TEST_MONGO_URI` to the dedicated loopback replica above, then run:

```text
python scripts/testing/inventory_suite/native-preview-api.py --isolated-test-data
node scripts/testing/inventory_suite/preview-server.cjs --isolated-test-data
node scripts/testing/inventory_suite/native-workflow-browser.cjs
node scripts/testing/inventory_suite/frontend-browser-check.cjs
```

Open `http://127.0.0.1:4174/inventory-v2`. All browser requests outside UI4174/API4175 are blocked during tests. Stop only these owned processes when review ends.

## Tests

Use `node frontend/scripts/test-inventory-suite.cjs <frontend-relative test paths>` for scoped Jest on Windows. The wrapper resolves the existing CRA/Jest configuration and locked package exports inside a `.codex` checkout. The full accepted path set is recorded in `docs/operations/INV-UI-20261120/evidence/verification.json`.

For backend tests, add `backend` and `backend/tests` to `PYTHONPATH`, set the dedicated local fixture URIs and run `python -m pytest --noconftest -q <test paths>`. `MZ2_TEST_STANDALONE_URI` uses27654; `OPERATIONAL_TEST_MONGO_URI` may use the27653 replica with its independent UUID DB. The older eligibility suite also requires its own27462 replica. Never load a production `.env` or import `backend/server.py` for this fixture.
