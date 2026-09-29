# Track B — independent component checkpoint

Status: **TRACK_B_INDEPENDENT_CHECKPOINT_SAVED_WAITING_FOR_TRACK_A**.
Independent 16-stage UI/domain work is complete within the approved checkpoint scope.
This is not `TRACK_B_ONBOARDING_DOMAINS_READY_FOR_INTEGRATION`.

## Identity and authority

- Branch: `codex/mz2-onboarding-domains-ui-20260930`.
- Isolated worktree: `C:/Users/amasi/.codex/worktrees/mz2-track-b-20260930`.
- Pinned production base: `1af50d92360beccd5ba83d1dc3028e41c62afdb8`.
- Production base tree: `c25b1beb687ef4d4f8fa2d93b7f77b46c1fb7014`.
- Original independent checkpoint: `6c8a343846cd097952f0cac415e46e8d0c23fe0e`, published on the development branch and preserved locally as `codex/mz2-onboarding-independent-checkpoint-20260930`.
- Current local checkpoint was carried onto shared source base `1d9d65d8576d5bc852f52262b84af150356b9385`, tree `82ed0d05eee935d22c7a4050f21e66cdb9f4f551`, before this checkpoint-only stop. This is a source identity, not a fresh assertion of the live Production identity. No further base transfer is performed here.
- All current work is preserved in one checkpoint commit above that shared source base. Obtain its exact HEAD/tree from Git. This checkpoint is not a final integration candidate.
- UX authority: [Issue #1006 comment 5899581442](https://github.com/AMASI-SA/AMASI-SA/issues/1006#issuecomment-5899581442).
- Independent-work boundary: [comment 5899641518](https://github.com/AMASI-SA/AMASI-SA/issues/1006#issuecomment-5899641518), reinforced directly by the owner in this task.
- Contract references observed before stopping: [early V1 5900935996](https://github.com/AMASI-SA/AMASI-SA/issues/1006#issuecomment-5900935996), clarification 5900990152, and shared-source-base comment 5900811126. These do not constitute verified final integration or permission to proceed beyond this checkpoint.
- Cutover target: `2026-10-01 00:00 Asia/Riyadh`; the UI does not silently populate the operator's cutover field.

## Completed independent work

All 16 specified stages are represented in a controlled RTL presentation component. The component has no route installation, network client, storage fallback, autosave, session API, posting, or activation action. Its internal view model is not a proposed Track A wire contract. Unbound save actions are disabled and explicitly say changes are not saved on the server.

Domain editors cover bank/cash identities, provider balance/bank/evidence, employee salary/advance/custody, supplier payable, external persons, per-courier contracts and opening balances, drivers, inventory, advertising, and capability-gated expense/payment-fee classifications. The last screen is locked; P02 remains visibly LOCKED. Unsupported supplier advances, automatic amortization, and future-fee learning are not claimed.

Each courier keeps its own draft keyed by actual identity. Validation distinguishes empty amounts from explicit zero, checks VAT and COD boundaries, rejects overlapping or gapped tiers, and requires explicit zero commissions. Changing other entity/product/variant identities clears dependent facts instead of copying the former entity's amounts/evidence. Pending external-person creation disables concurrent row mutation.

Inventory supports product/variant, customer specification fields, integer product quantity, fractional component quantity, exact registered component unit, unit cost/total, warehouse location, barcode, and preparation state. Decimal multiplication and half-up cent rounding use scaled integers (`1 × 10.075 = 10.08`). No quantity/cost initialization or import is called.

Section presentation distinguishes incomplete, complete, not applicable, explicit zero, missing evidence, and reconciliation problems. Not-applicable cannot advance progress without reason/evidence or while rows remain; cutover cannot be not-applicable. Positive or missing amounts cannot appear as an explicit-zero section. Final section status persistence still belongs to Track A.

## Files

- `frontend/src/pages/accounting/onboarding/OnboardingWizardView.jsx`
- `frontend/src/pages/accounting/onboarding/onboardingStages.js`
- `frontend/src/pages/accounting/onboarding/onboardingDecimal.js`
- `frontend/src/pages/accounting/onboarding/OpeningCourierEditor.jsx`
- `frontend/src/pages/accounting/onboarding/OpeningEntityEditor.jsx`
- `frontend/src/pages/accounting/onboarding/OpeningInventoryEditor.jsx`
- `frontend/src/pages/accounting/onboarding/OpeningTermsEditor.jsx`
- Five corresponding `*.test.jsx` files.
- `backend/accounting_onboarding_domains.py`
- `backend/tests/test_accounting_onboarding_domains.py`
- This report and five fixture screenshots.

The domain module is **unrouted**. It provides owner-scoped identity/catalog projections and an external-person contact creation helper. Callers must enforce fresh actor permissions and resolve owner scope when Track A's contract is integrated. It is not a new public API.

## Fresh evidence

Frontend, from `frontend`:

```powershell
$env:CI='true'
node node_modules/react-scripts/bin/react-scripts.js test --watchAll=false --runInBand --testMatch '**/onboarding/*.test.jsx'
```

Result: **26 passed, 5 suites passed**, exit 0. The explicit testMatch avoids CRA's default glob mishandling `.codex` in the worktree path. Existing local dependency installation was reused through a junction; no dependency manifest or lockfile was changed.

Backend, from `backend`, with `PYTHONPATH` pointing to that directory:

```powershell
& 'C:/Users/amasi/.codex/tmp/mz2-track-b-python/Scripts/python.exe' -m pytest --noconftest tests/test_accounting_onboarding_domains.py -q
```

Result: **9 passed**, exit 0. Disposable in-memory collection doubles only; no Mongo connection. Assertions cover owner isolation, identity-only projections, ambiguity rejection, canonical cash/currency, distinct advertising account identities, component units/options, and external contact create/select. Read-only discovery performs zero writes.

An isolated Vite bundle containing the actual new components built successfully. A loopback fixture harness was rendered with headless Edge; five screenshots were captured, no browser JavaScript errors occurred, and the 390px mobile cutover viewport has no horizontal overflow. This is component/browser evidence, **not** an application integration build or save/resume proof.

Screenshots contain conspicuously labelled synthetic data:

- [Cutover desktop](screenshots/01-cutover-desktop.png)
- [Cutover mobile](screenshots/01-cutover-mobile.png)
- [Independent courier draft](screenshots/07-courier-draft.png)
- [Product and fractional component inventory](screenshots/10-inventory.png)
- [Final review states](screenshots/15-review.png)

Local review harness and capture scripts are retained at `C:/Users/amasi/.codex/tmp/mz2-track-b-review`; they have no backend connection and are not application routes.

## Proven gaps / deferred adapters

1. Existing `/counterparties` now has the smallest optional phone extension in its existing create/update schemas (40 characters, existing owner checks). Four in-memory route tests cover this extension. No new endpoint or accounting-core change was introduced; no live contact was created.
2. Production opening-inventory routes lack a read-only catalog context route. A projection helper is prepared, without changing `opening_inventory_service`. Inventory-account mapping remains empty with an explicit warning until the opening contract supplies it.
3. Advertising `external_ref` is untyped text, not proof of a link between an advertising profile and its financial wallet/payable. Canonical ad financial identities are exposed separately, with mapping warnings; no guessed join.
4. Core-owned prepaid/accrued classifications and fee capabilities are supplied as props, never invented. The UI keeps unavailable capabilities clearly blocked.

## Sole integration blocker

**Track A contract + shared base reconciliation.** Final contract/source compatibility and the approved shared base must be verified together before final binding. Early V1 documentation alone does not prove application integration or persistence of all sixteen stages.

Consequently, actual server persistence, session restoration, evidence uploads, accounting preview/review binding, final advertising/inventory account mappings, application route/permission wiring, and integrated tests remain deferred work under that single dependency, not independent completed capabilities. No final adapter or parallel session/save-resume API is installed. No integration-ready claim is made.

## Additional source drafts preserved at stop

The checkpoint also preserves previously prepared, **unbound and provisional** `accountingOnboarding.js`, `onboardingSessionController.js`, and `onboardingFinancialAdapter.js`, with their tests. They are not imported by an application page or route. They target the documented early Track A contract; they do not define backend endpoints. They must be revalidated against Track A's final source before use. Courier terms and inventory quantity manifests are not claimed to persist through financial-session V1.

Fresh checkpoint checks: **26 UI component tests + 9 domain tests PASS**, and **16 additional adapter/controller/client tests + 4 contact-route tests PASS**. Five existing synthetic-fixture screenshots are preserved. Network clients are mocked and backend collections are in memory; these checks are not live save/resume proof.

Resume order when authorized after PR #1195 stabilizes: freshly read Production HEAD/tree, reconcile the approved shared base, safely rebase/cherry-pick this checkpoint if required without losing work, and rerun all Track B tests. Then read Track A's published final contract in Issue #1006 and bind only to that contract. Add save/resume/no-financial-autosave integration tests before freezing an integration candidate. No further base transfer or final binding is performed in this checkpoint-only step.

Checkpoint publication is limited to pushing this development branch for preservation. No merge, deployment, Release Intent, Production/Preview DB access or writes, Live Post, financial write-control change, or P01/P02/G47 activation occurred. `release/release-intent-v5.json` remains unchanged.
