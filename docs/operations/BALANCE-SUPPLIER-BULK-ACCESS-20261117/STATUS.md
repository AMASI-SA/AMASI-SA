# Bulk balance permissions and individual exceptions — 17 November 2026

## Release continuation — 18 November 2026

- The owner has authorized merge and publication of PR #1968. This continuation completes that release; it does not grant or change any live employee permission.
- Resumed the exact verified source `6cae21257e7503abf00beda428f254253ce84848` and read the separately preserved final evidence at `a438415004046f1cd4a813c5f946bf4d55925492`.
- Fresh GitHub inspection confirms all four Operational Balance Web jobs in run `44169376678` passed. In particular, backend job `134344214223` finished with 5,126 tests, plus 16 shipping tests and 18 subtests; the earlier pending status below is superseded.
- Updated onto reviewed Production base `68035e55018d5b75bdd6b9e0ab6000d7528a956f` without conflicts. Production already contains the reviewed picomatch security backports from PR #1970. This task changes none of those dependency, lockfile or security-policy bytes.
- The implementation and test files are unchanged from the reviewed feature head. The diff against the new base remains the same six task-owned paths. The inherited release intent stays identical to the new base pending a new governed candidate build.
- Fresh integrated CI, strict security audit, a new source A / intent-only B, both clean-clone rehearsals, normal merge, serial Release Guard and live verification remain required. Prior source results are historical evidence, not acceptance of this updated candidate.
- Production changed by this continuation: **no**. No lease, shared `/app` mutation, deployment, financial-data write or live permission grant has occurred.
- Issue #1006 is at the repository's documented comment limit; its read also failed with a transport error in this session. Preserve its existing history. PR #1968 and this status are the canonical continuation record; final identities and verification evidence will be recorded in the PR after source freeze.

The historical source checkpoint below describes the earlier implementation phase. Its no-release wording is superseded only by the owner's explicit authorization above; all release and permission safeguards remain applicable.

## Scope and state

- Repository: `AMASI-SA/AMASI-SA`.
- Task branch: `codex/balance-supplier-bulk-access-20261117`; existing Draft PR: https://github.com/AMASI-SA/AMASI-SA/pull/1968.
- Verified Production base: `8df479ef24b4f8847c26ba18d48e3248f7b673cf`.
- This follow-up extends the supplier-only source `d1c1a9a74318d563bcbd0594e2ad55392043cfd6`. Its earlier CI evidence remains separately preserved at `2c15c3899f27dfb0fc14e244553ee25b153cfe7d`; those results are not acceptance of this extension.
- The user requested bulk permission choices for all suppliers, employees, delivery drivers, balances and movements, followed by individual exceptions such as hiding one employee's balance.
- Implementation, focused verification and independent source review are complete. Exact-source CI browser acceptance must be recorded after this source checkpoint. This is not a governed release artifact.
- Production changed: **no**. No live employee grants, financial transactions, database records, release intent, deployment, or shared `/app` files were changed.

## Behavior

The existing `نوع الحساب أو الجهة` filter selects the bulk scope. `جميع الحسابات والأطراف` includes every current resource in the loaded owner catalog. Selecting a specific type applies only to that type, including matching resources hidden by the name search. The bulk heading and Apply button identify the scope and exact count.

`استخدام الحسابات والأطراف`, `عرض الحركات`, and `عرض الأرصدة` are independent and each starts at `بدون تغيير`. Only explicitly selected fields change. Changing the type clears unapplied bulk choices so they cannot silently widen to all resources. Apply clears the search to reveal the affected rows and keeps the selected type. Previously applied draft changes remain intact when the type changes.

After applying, the owner can edit any individual row. For example, allow every current balance, then uncheck one supplier or employee balance while leaving movement access unchanged. Changes remain local until `حفظ صلاحيات الموظف`. Save keeps the existing version/CAS contract. No operation permission or workspace enablement is added by the bulk editor.

Resources remain keyed by `(kind, id)`. `store_driver` (delivery drivers) remains separate from `courier` (shipping companies); `employee` remains separate from `employee_custody` even when IDs match. Fully denied rows may be omitted because the backend denies absent grants. No wildcard or default grant is introduced: future resources need an explicit permission choice.

The separate employee/courier actor-picker classification issue remains unchanged by this feature.

## Changed files

- `frontend/src/pages/operational/SimpleBalanceAccess.jsx`: general bulk scope, independent draft merge and scope reset.
- `frontend/src/pages/operational/SimpleBalanceAccess.test.jsx`: 19 permission tests including global balances/movements, individual exceptions, overlapping IDs, scope changes, and newly added resources.
- `frontend/src/pages/operational/simpleBalances.css`: responsive bulk layout and full-width type/search filters on mobile so the complete selected scope stays readable.
- `scripts/testing/simple_balances/fixtures.cjs`: synthetic suppliers, drivers and a shipping company, with deliberately overlapping IDs across kinds.
- `scripts/testing/simple_balances/browser.cjs`: extends the existing permission scenario to all resources, independent fields, driver isolation, exceptions, one versioned save, reload persistence and 390px control bounds. Scenario count remains eight; no gate was relaxed.

No backend, dependency, lockfile, security policy, workflow, release intent or live data changes.

## Verification for this extension

Pinned toolchain: Node `22.23.2`, Yarn `1.22.22`.

1. Baseline RED against unchanged supplier component: **5 passed / 14 failed / 19 total**, exit 1, expected. Global scope and driver/employee bulk controls were missing.
2. Updated permission suite: **19/19 passed**, zero skipped, exit 0.
3. Related suites (`SimpleBalanceAccess`, `SimpleBalances`, `simpleBalancesApi`, `simpleBalancesModel`): **43/43 passed across 4 suites**, zero skipped, exit 0. Root and independent reviewer read Jest JSON; the component/test hashes remained unchanged during the test run.
4. Isolated actual-component Vite build: **passed**, 98 modules, exit 0. Independent reviewer matched all 16 manifest source hashes.
5. Browser/fixture syntax and `git diff --check`: exit 0.
6. Independent source, tests and browser-fixture review: no actionable defect. Approval covers checkpointing and CI review; browser execution is still outstanding.
7. Backend compatibility review: explicit resource grants, read/write authorization and versioned Save support this feature without a schema change. A separate in-memory backend-test attempt could not start because this local Python lacks pytest; it is not counted as passed.
8. Local Chromium previously failed before page launch because the runtime denied its singleton socket. The sandbox is unchanged. Intermediate source `333c807e2c438c0175be783fbb6bda79c0daedc1` passed 984 operational frontend tests, all 8 simple-balances and 14 report browser scenarios. Root verified the browser ZIP digest and all 16 source hashes, then visually found the long all-resource scope label clipped in the two-column mobile filter. This follow-up stacks those two filters on mobile and adds a text-width assertion to the existing scenario. CI must run on the final source again; the intermediate results do not prove this final CSS/browser change.

Focused test command from repository root:

```bash
CI=true python scripts/frontend_release_toolchain.py exec -- yarn --cwd frontend test --watch=false --runInBand --resetMocks=false --runTestsByPath src/pages/operational/SimpleBalanceAccess.test.jsx src/pages/operational/SimpleBalances.test.jsx src/pages/operational/simpleBalancesApi.test.js src/pages/operational/simpleBalancesModel.test.js
```

## Existing release blocker

Security Gate run `44168261320`, job `134340050967`, on intermediate source `333c807e2c438c0175be783fbb6bda79c0daedc1` repeated the existing production dependency audit failure for high-severity picomatch advisory `GHSA-fc5q-39xc-vjpf` / `CVE-2026-112366` (five paths, existing locked versions 2.3.2 and 4.0.7). The full frontend regression before that audit passed 4,230 tests / 417 suites. The dependency manifest/lockfile and security gate were unchanged from Production and remain unchanged here. Subsequent spreadsheet-runtime and security-manifest/CSP steps did not execute after that failure. No exception, bypass or unrelated dependency remediation was introduced. Prior test success does not establish release readiness.

## Next safe action

Preserve this source on the existing task branch, update Draft PR #1968 and the task's own Issue #1006 ledger entry, then inspect CI for the exact new source. Download browser evidence, verify its source hashes and inspect desktop/mobile screenshots. Preserve final verification evidence separately from the tested source head.

The Issue #1006 update endpoint acknowledged the task entry, but subsequent normalized and raw reads still returned the older body and timestamp without it. Ledger read-back is therefore unconfirmed; the task branch, PR and this status document are the verified durable continuation record. Preserve the unrelated ledger body when reconciling this discrepancy.

Any later release requires resolving the existing security gate failure and following the repository's v5 A/B intent, clean rehearsal, serial guard and live verification protocol. No new live permission grant or release is part of this source checkpoint.
