# Dashboard cost, spend and city correction

## Scope and current state

- Task branch: `codex/dashboard-cost-spend-city-20261112`.
- Draft PR: https://github.com/AMASI-SA/AMASI-SA/pull/1893
- Previous remote checkpoint: `ac0736496775a0df549760850fb87f0ae79c18ed`.
- Initial reviewed production-branch base: `570867c02ff2486750e4a99d574c25a8fef6c428`.
- Observed live source before this task: `361978f854cecf572a8f3d3f0074f41fd91fcfd0`; the intervening financial-demand-only change is preserved.
- State: WIP checkpoint; backend regression suite passed; frontend dependency installation and execution remain in progress.
- Production changed by this task: **no**. No merge, release intent, lease, publish, account selection or provider write has been performed.

## Confirmed mechanisms

1. A negative selected parent answer resolved correctly, but the cost resolver discarded the saved Salla visibility condition of a legacy dependent name field. An absent hidden field was therefore incorrectly unresolved.
2. Two option bindings may represent one service only when an exact parent/value visibility condition, service identity, quantity and legacy field relationship prove that equivalence. Unrelated charges must remain separate.
3. The platform spend card exposed a saved partial subtotal while the executive card gated its total on full financial availability. Independent card refresh state could also diverge even when both readers used the same endpoint.
4. Recent orders displayed the raw English city value without a display translation.
5. The owner clarified that only one Google account is active; inactive discovered accounts must not define required spend coverage. Both current Google consumers use explicitly selected accounts, falling back to all connected API accounts when none is selected. A bounded owner selection operation is now implemented but has not been applied. Missing spend for a selected account must remain unknown.

## Implemented at this checkpoint

- Resolve conditional visibility from saved exact option/value IDs; preserve missing, invalid, conflicting, cyclic and deleted-choice uncertainty.
- Collapse only a uniquely proven selected legacy service alias. Retain frozen historical cost precedence.
- Show the available subtotal with an incomplete annotation; preserve unknown final financial totals, CPA, ROAS and profit.
- Share the accepted display snapshot between the two dashboard cards using the existing reads; reject old-period and out-of-order responses. Add no polling or provider synchronization.
- Translate the observed Saudi/Gulf city aliases synchronously for display. Preserve unknown labels and delivery-address precedence.
- Add `scripts/set_google_reporting_accounts.py`: dry run by default, explicit owner and expected/selected account sets, bounded exact scope, transactional compare-and-set updates to selection fields only, audit and fresh readback. No provider calls or account/report deletion. The command refuses application if transactions are unsupported.
- Add the selection and city tests to the existing PR-only dashboard workflow; no recurring job was added.

## Verification

From `backend`, using the focused workflow dependency versions:

```sh
python -m pytest --noconftest -p no:cacheprovider -q \
  tests/test_report_option_identity.py tests/test_order_cost_authority.py \
  tests/test_order_cost_identity_projection.py tests/test_sold_product_cost_details.py \
  tests/test_product_option_shared_cost_engine.py tests/test_authority_review_regressions.py \
  tests/test_order_option_cost_snapshots.py tests/test_product_field_cost_support.py
```

Result: **124 passed**, exit 0. Independent review reproduced and closed the deleted-parent-choice edge. The tests use synthetic business identities; no live customer payloads or credentials are stored here.

A separate private replay of the observed order's cost-relevant option IDs, nested selected values and bindings reproduced the original unresolved legacy field, then produced complete cost with the same amount after the fix. No live payload was committed.

Google verification from `backend`:

```sh
python -m pytest -q tests/test_google_reporting_account_selection.py \
  tests/test_google_dashboard_spend.py tests/test_google_ads_reporting_v1.py
```

Result: **96 passed**, exit 0, including 36 new selection cases. Existing Google readers select exactly the requested fixture account after the operation, preserve all reports, and retain unknown missing spend. Transaction tests use a driver-boundary staging double; actual server transaction support still requires verification in the operator environment.

`git diff --check`: pass. Frontend tests: pending completion of the pinned frozen-lockfile dependency install. Independent review found a complete-to-complete display update edge and its correction is in progress; this checkpoint is not ready to release. Browser acceptance after deployment: pending.

## Next safe action

Finish frontend execution and independent snapshot-propagation review, finish city-source coverage review, then create a new verified checkpoint. The one-time Google selection operation still requires authorized access to the existing operator environment. Do not publish from this WIP checkpoint. Before any separately authorized release, re-read the latest production branch and shared Release Guard, preserve other work, and follow the v5 A/B protocol in `AGENTS.md`.
