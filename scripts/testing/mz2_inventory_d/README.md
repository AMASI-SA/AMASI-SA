# Stage 10 connected browser proof

This fixture runs the actual AccountingOnboarding UI and session/catalogue API against a UUID-named disposable loopback Mongo database. It seeds synthetic V2 products/variants/components, a legacy-only product and an ambiguous warehouse location. It never imports the production server or uses production credentials.

Use an isolated replica set (`MZ2_TEST_MONGO_URI=mongodb://127.0.0.1:27018/?replicaSet=mz2test`) and the repository backend test dependencies plus uvicorn. Set `PYTHON_DOTENV_DISABLED=1`.

1. Set `MZ2_AB_DIST` to a temporary absolute output directory and run `node scripts/testing/mz2_inventory_d/build.cjs`.
2. Run `python -m uvicorn scripts.testing.mz2_inventory_d.server:app --host 127.0.0.1 --port 18764`.
3. Set `PLAYWRIGHT_MODULE` to the installed Playwright module, `MZ2_D_OUTPUT` to an evidence directory and optionally `MZ2_BROWSER_CHANNEL=msedge`; run `node scripts/testing/mz2_inventory_d/browser.cjs`.
4. Stop the server normally so fixture teardown drops its own disposable database.

The browser blocks external requests, verifies allowed HTTP mutations and checks that non-session collection counts remain unchanged. Backend tests additionally check permission, CAS/idempotency and storage boundaries. Screenshots are explicitly synthetic, not production evidence.
