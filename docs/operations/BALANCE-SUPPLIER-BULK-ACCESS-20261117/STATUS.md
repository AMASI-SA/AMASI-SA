# Supplier bulk permission editor — 17 November 2026

## Scope and state

- Repository: `AMASI-SA/AMASI-SA`.
- Task branch: `codex/balance-supplier-bulk-access-20261117`.
- Verified Production base: `8df479ef24b4f8847c26ba18d48e3248f7b673cf`.
- User requested assigning supplier permissions together instead of configuring each supplier separately.
- Implementation and focused/CI/browser verification are complete at source `d1c1a9a74318d563bcbd0594e2ad55392043cfd6`, tree `5ccf7c3baabb98991d34fd98c0bac0a1ef491b8c`, Draft PR #1968. The local Chromium runtime denied its singleton socket; the unchanged sandboxed browser acceptance subsequently passed on GitHub.
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
5. Sandboxed Chromium locally: environment failure before any scenario or page launched; not accepted as browser evidence. Exact-source GitHub acceptance subsequently passed all 8 simple-balances scenarios and 14 existing report scenarios with the sandbox enabled. Root downloaded the simple-balances artifact, verified its ZIP digest and all 16 source hashes, and visually inspected desktop and mobile screenshots.
6. `git diff --check`: exit 0.
7. Independent source/CSS/test review: approved, no actionable findings.

Focused test command from repository root:

```bash
CI=true python scripts/frontend_release_toolchain.py exec -- yarn --cwd frontend test --watch=false --runInBand --resetMocks=false --runTestsByPath src/pages/operational/SimpleBalanceAccess.test.jsx src/pages/operational/simpleBalancesApi.test.js src/pages/operational/simpleBalancesModel.test.js src/pages/operational/SimpleBalances.test.jsx
```

## Release blocker

The existing Security Gate run `44159187708`, job `134308099197`, failed at `Audit production frontend dependencies` for high-severity picomatch advisory `GHSA-fc5q-39xc-vjpf` / `CVE-2026-112366` (five dependency paths, locked versions 2.3.2 and 4.0.7). Root independently read the audit log. `frontend/package.json`, `frontend/yarn.lock`, the security workflow and audit checker are unchanged from Production; this feature did not introduce the dependency. The audit response does not advertise a patched version; that is not an independent claim about current upstream availability.

The whole frontend regression before this audit passed **4,222 tests / 417 suites**. Later spreadsheet-runtime and security-manifest/CSP steps did not execute after audit failure. No exception, bypass, dependency change, or unrelated remediation was introduced. **Release readiness is blocked**, although this feature's tests and browser acceptance passed.

## Final exact-source evidence

- PR: https://github.com/AMASI-SA/AMASI-SA/pull/1968 (open Draft, unmerged).
- Source: `d1c1a9a74318d563bcbd0594e2ad55392043cfd6`; source tree: `5ccf7c3baabb98991d34fd98c0bac0a1ef491b8c`.
- Operational Web run: `44159187713`.
- Frontend job `134308098681`: **976 tests / 62 suites passed**, no skips; root independently verified artifact `14362672133` (ZIP SHA-256 `bf44883814614112259a15cfd10afb8c58a2fc9ab304844caa0d31bd480fb2f2`).
- Browser job `134308098152`: **8 simple-balances + 14 existing report scenarios passed**, including bulk application, an individual exception, a single save, and 390px control bounds. Artifact `14362297705`, ZIP SHA-256 `dd074c0ce115c772f343391bd4ec1002a6b9d4306076330fb3e3a2d39bcf1197`.
- Source and evidence are preserved separately so documentation does not change the tested PR head.
- Latest complete/check-pending details are in `EVIDENCE.json`; frontend/browser success does not assert other pending gates succeeded.

## Next safe action

The feature is prepared for review; Production remains on the verified base. Resolve the separately scoped existing dependency audit failure, execute the skipped security checks, and complete remaining PR gates before any release decision. Any later publication must follow the repository's v5 A/B intent, clean rehearsal, serial guard, and live verification protocol. This evidence branch is not a deployment candidate. No live employee was selected or granted permissions by this task.
