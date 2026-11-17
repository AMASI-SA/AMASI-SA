# Supplier bulk permission editor — 17 November 2026

## Scope and state

- Repository: `AMASI-SA/AMASI-SA`.
- Task branch: `codex/balance-supplier-bulk-access-20261117`.
- Verified Production base: `8df479ef24b4f8847c26ba18d48e3248f7b673cf`.
- User requested assigning supplier permissions together instead of configuring each supplier separately.
- Implementation and focused verification are complete; browser acceptance must run in CI because the local runtime refuses Chromium's process singleton socket (`Operation not permitted`). The browser sandbox was not disabled.
- Production changed: **no**. No live employee grants, financial transactions, database records, release intent, deployment, or shared `/app` files were changed.

## Behavior

The owner permission screen now offers `صلاحيات جميع الموردين`. Use, movement visibility, and balance visibility each start at `بدون تغيير`. Explicit choices are applied to every current supplier from the loaded catalog, including suppliers hidden by search, then shown for review. The displayed count and explanation state that future suppliers are not granted access automatically.

Changes remain a local draft until the existing `حفظ صلاحيات الموظف` action. Individual supplier exceptions can be edited after bulk application. The existing versioned save contract and server authorization remain unchanged. Other resources, overlapping IDs in another resource kind, independent fields, workspace access, and operation permissions are preserved.

The employee/courier account-picker classification issue discussed previously is separate and remains unchanged by this focused supplier feature.

## Changed files

- `frontend/src/pages/operational/SimpleBalanceAccess.jsx`: bulk editor and composite-key draft merge.
- `frontend/src/pages/operational/simpleBalances.css`: desktop/mobile bulk control layout.
- `frontend/src/pages/operational/SimpleBalanceAccess.test.jsx`: six new regression cases.
- `scripts/testing/simple_balances/fixtures.cjs`: second synthetic supplier.
- `scripts/testing/simple_balances/browser.cjs`: real component bulk interactions, supplier exception, unchanged operation checks, and mobile control bounds within the existing eight scenarios.

## Verification

Pinned toolchain: Node `22.23.2`, Yarn `1.22.22`. Frozen dependency installation exited 0 without a lockfile change.

1. Baseline regression before implementation: existing 5 tests passed and new 6 tests failed because the bulk controls were absent. Exit 1, expected RED.
2. Focused updated permission suite: **11/11 passed**, zero skipped, exit 0.
3. Related suites (`SimpleBalanceAccess`, `SimpleBalances`, `simpleBalancesApi`, `simpleBalancesModel`): **35/35 passed across 4 suites**, zero skipped, exit 0.
4. Isolated actual-component Vite build: **passed**, 98 modules, exit 0.
5. Sandboxed Chromium locally: environment failure before any scenario or page launched; not accepted as browser evidence. CI browser acceptance is outstanding.
6. `git diff --check`: exit 0.
7. Independent source/CSS/test review: approved, no actionable findings.

Focused test command from repository root:

```bash
CI=true python scripts/frontend_release_toolchain.py exec -- yarn --cwd frontend test --watch=false --runInBand --resetMocks=false --runTestsByPath src/pages/operational/SimpleBalanceAccess.test.jsx src/pages/operational/simpleBalancesApi.test.js src/pages/operational/simpleBalancesModel.test.js src/pages/operational/SimpleBalances.test.jsx
```

## Next safe action

Preserve this source on its task branch and open a Draft PR against `hotfix/prod-snap-meta-final`. Inspect exact-head operational frontend and browser acceptance results and screenshots. This is a source checkpoint, not a governed release artifact or proof of live publication. Any later release must follow the repository's v5 A/B intent, clean rehearsal, serial guard, and live verification protocol.
