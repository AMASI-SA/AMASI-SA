# MZ2_OPENING_INVENTORY_V2_UX_READY_FOR_REVIEW

Scope: Track D / Stage 10 only. Delivery record: Issue #1006.

Fresh origin production fetched before work:
- Branch: `hotfix/prod-snap-meta-final`
- HEAD: `728730366cfee6c906172c7c14a2a67251ed1065`
- TREE: `70b4c3d7b501f7c8a31767ceb61d5ac4cb204df9`
- Review branch: `codex/mz2-track-d-inventory` (isolated worktree; unrelated checkout modifications preserved).
- Final review HEAD/TREE and Draft PR are recorded in the Issue #1006 delivery comment, avoiding a self-referential commit hash in this file.

## Behavior and SSOT

`GET /api/accounting-module/onboarding/inventory-catalog` reads only owner-scoped `mezan_products_v2`, `mezan_cost_resources_v2`, `mezan_component_categories_v2` and existing warehouse records. Legacy-only products/components are absent. Components require explicit active status, track_inventory=true and kind!=service. Catalog access is read-only and requires fresh opening-view permission.

Product identity is product_v2_id plus variant_id; product_id remains a compatibility alias in setup metadata. Original V2 options/selections, product/variant SKU, barcode and images are exposed. The searchable picker matches name, SKU, barcode and ID, including variant SKU/barcode. A missing variant identity cannot be replaced by manual customer specifications. Products with options but missing variants remain incomplete until their V2 catalogue is corrected.

Quantity and unit cost produce a read-only decimal half-up total. Summary shows product lines, component lines, quantity (explicitly mixed units) and SAR value. Optional distributions are validated for existing location, quantity, duplicate location within the line, total quantity and barcode match when provided. Incomplete drafts remain saveable; validation issues block marking the financial section complete.

`PUT /api/accounting-module/onboarding/sessions/{id}/inventory-draft` saves bounded typed rows and Stage 10 financial-line entry text only in `mz2_onboarding_sessions`. It shares the existing CAS version, idempotency, fresh permission/owner and locked-session rules. Changes invalidate inventory completion and prior preview. No ledger, physical approval, opening-post or activation path is called.

The UI debounces metadata saves, flushes before Next/Previous, retries uncertain requests with their original key/payload, binds queued saves to their originating session, cancels debounce on restore, locks session switching during save, preserves edits on conflict and warns before unloading unsaved edits. Refresh uses the session ID and stage in the URL to restore server metadata; localStorage is not a source of truth. A network failure is shown as unsaved, never claimed as persisted.

## Warehouse provenance

`backend/warehouse_location_routes.py` and `backend/warehouse_location_v2_routes.py` both call `generate_location_rows` and write `cabinet_generated` audit events. Shared collection names, IDs and barcodes cannot distinguish the producer contract. This read path marks all unproven locations `AMBIGUOUS` and `physical_approval_verified=false`. Financial draft save does not require a location. No producer or warehouse data was changed.

## Fresh evidence

| Acceptance | Evidence |
|---|---|
| A/B/C/D name/SKU/barcode/image | Picker unit tests + real browser local HTTP/Mongo test |
| E/F/G original options, variant ID, separate identities | Picker/validation tests + browser refresh with two variants |
| H/I/J V2-only products/components | Backend catalogue boundary/HTTP tests, component browser selection |
| K exact computed total | Decimal half-up unit test, browser computed 100.00/180.00/15.00 SAR |
| L/M optional physical placement | No-placement save + supplied-location validation + AMBIGUOUS browser evidence |
| N refresh restoration | Component remount test + real browser reload/Next/Previous |
| O no financial write | Backend collection boundary test + browser mutation allowlist/non-session count proof |

- Backend: 60 passed, 0 skipped, disposable local replica set and standalone Mongo. See backend-results.xml.
- Frontend: 93 passed / 10 suites. See frontend-results.json.
- Browser: PASS, actual UI -> FastAPI -> isolated UUID-named Mongo; desktop 1440px and mobile 390px, no horizontal overflow or page errors. See browser-results.json and screenshots. Images/data are explicitly synthetic fixtures.
- Source build: `node node_modules/vite/bin/vite.js build` passed; existing CSS import-order, chunk-size and ineffective dynamic-import warnings remain outside this scope. Local Node 24.19.0 is outside the repository's declared production Node range; CI uses its governed Node version.
- CI: existing source-integration workflow extended with Stage 10 API/browser tests and evidence artifacts. Exact remote check status belongs to the final PR/Issue record.
- `git diff --check`: passed (Windows line-ending notices only).

Run backend with PYTHONPATH=backend;backend/tests, PYTHON_DOTENV_DISABLED=1 and loopback MZ2_TEST_MONGO_URI/MZ2_TEST_STANDALONE_URI:
`python -m pytest backend/tests/test_accounting_onboarding.py backend/tests/test_accounting_onboarding_domains.py backend/tests/test_onboarding_inventory_draft.py --noconftest -q`

Run frontend with CI=true:
`node node_modules/react-scripts/bin/react-scripts.js test --watchAll=false --runInBand --testMatch '**/onboarding/*.test.jsx' '**/onboardingFinancialAdapter.test.js' '**/accountingOnboarding.test.js' '**/onboardingSessionController.test.js' '**/onboardingInventoryCatalog.test.js'`

Browser reproduction: scripts/testing/mz2_inventory_d/README.md.

Limits: catalogue refuses over 10,000 records/source or 20,000 locations instead of silently truncating; draft limits are 1,000 rows and 100 allocations/row. This review does not certify production data or physical stock. Account valuation retains the existing explicit financial-account mapping contract.

Financial writes Production = 0
Merge = NO
Deploy = NO
Post = NO
Activation = NO
