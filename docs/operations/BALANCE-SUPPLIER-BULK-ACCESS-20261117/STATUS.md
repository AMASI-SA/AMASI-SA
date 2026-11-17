# Bulk balance permissions and individual exceptions — final evidence

## State

- Task: `BALANCE-SUPPLIER-BULK-ACCESS-20261117`.
- Repository: `AMASI-SA/AMASI-SA`; Draft PR: https://github.com/AMASI-SA/AMASI-SA/pull/1968.
- Tested source: **`6cae21257e7503abf00beda428f254253ce84848`**, tree **`9cac2fdcdca10ec3d1b085698533b2489e0847fc`**.
- Source branch: `codex/balance-supplier-bulk-access-20261117`.
- Evidence branch: `codex/balance-all-resource-evidence-20261117`. This branch changes documentation only and is **not a deployment candidate**; the tested PR head stays unchanged.
- Production ref re-read at final verification: `8df479ef24b4f8847c26ba18d48e3248f7b673cf`.
- Implementation, relevant frontend/browser acceptance and independent review are complete. Release readiness is **blocked** by the existing dependency audit failure. The operational backend job was still running at the recorded read and is not counted as passed.
- Production changed by this task: **no**. No live employee grants, financial writes, database changes, merge, release intent, shared `/app` modifications or publication.

## User-requested behavior

The existing `نوع الحساب أو الجهة` selector now scopes bulk permission edits to all current accounts/parties or one type. Suppliers, employees, delivery drivers, shipping companies, banks, cash, custody and all other catalog kinds use the same flow. The scope heading and Apply button display the exact group and count.

`استخدام الحسابات والأطراف`, `عرض الحركات`, and `عرض الأرصدة` each start at `بدون تغيير`. Only explicitly selected fields change. A type change clears unapplied bulk selections, while preserving changes already made in the draft. Apply includes resources hidden by search, clears the search to reveal affected rows and retains the selected type.

After bulk application, the owner can edit individual exceptions before `حفظ صلاحيات الموظف`. Allowing every balance and then hiding one supplier/employee balance keeps that resource's movement/use choices independent. Existing operation permissions, workspace enablement, owner authorization and versioned/CAS save behavior remain intact.

Composite `(kind,id)` keys keep drivers separate from shipping companies and employees separate from their custody accounts, including equal IDs. Fully denied records can be omitted because absent grants are denied. There are no wildcard/default grants: new resources require an explicit permission choice.

The separate employee/courier actor-picker classification issue remains unchanged.

## Changes

- `frontend/src/pages/operational/SimpleBalanceAccess.jsx`: generalized bulk scope and independent draft changes.
- `frontend/src/pages/operational/SimpleBalanceAccess.test.jsx`: 19 permission tests, covering global balances/movements, per-row exceptions, all resource kinds, overlapping IDs, scope reset and future resources.
- `frontend/src/pages/operational/simpleBalances.css`: bulk layout and full-width type/search filters on mobile.
- `scripts/testing/simple_balances/fixtures.cjs`: synthetic suppliers, drivers and a shipping company, with deliberate cross-kind ID collisions.
- `scripts/testing/simple_balances/browser.cjs`: bulk scopes, exceptions, one versioned save, reload persistence, mobile bounds and full scope-label readability inside the existing eight scenarios.

No backend schema, dependency, lockfile, security policy, workflow or release-intent change.

## Verification

Pinned toolchain: Node `22.23.2`, Yarn `1.22.22`.

- Baseline RED against the supplier-only component: **5 passed /14 failed /19 total**, exit 1, expected.
- Updated permission suite: **19/19 passed**. Related UI/API/model suites: **43/43 across 4 suites**, zero skipped, exit 0. The final JSX/test files have exactly the hashes from that run.
- Actual-component Vite build: **passed**, 98 modules. Syntax and `git diff --check`: exit 0.
- Independent source/test/backend-contract review: approved with no actionable finding. The small mobile follow-up was separately reviewed.
- Operational Web run **44169376678**, frontend job **134344213767**: **984 tests /62 suites passed**, with missing/skipped-test rejection passed.
- Same run, canonical worker job **134344214185**: **4 passed**, exact source and XML no-skip/failure/error checks passed.
- Same run, browser job **134344213387**: **all 8 simple-balances +14 existing report scenarios passed**, with Chromium sandbox enabled. The owner flow saved once and retained all individual exceptions after reload. Non-owner access remained denied.
- Root downloaded simple browser artifact **14367181785**, verified ZIP SHA-256 **`8c4c2c3cd1fc8dc79fd562439c67fc7b7c9004d60351f3ecbd785f6fe675b4ae`**, matched all **16 source hashes**, and inspected desktop **1440×2472** and mobile **390×5993** screenshots. The complete scope label and search placeholder are now readable and controls remain inside the viewport.
- Full frontend regression before the security audit: **4,230 tests /417 suites passed** at the final source.
- Operational backend job **134344214223** was still `in_progress` at the recorded read; its follow-up and skip-rejection steps were pending. No claim of complete green CI is made.

Intermediate source `333c807e2c438c0175be783fbb6bda79c0daedc1` passed its initial browser scenarios, but root visual review found the long scope name clipped in the two-column mobile filter. The final source fixes that layout and adds text-width checks. The final acceptance above supersedes the intermediate source.

Local Chromium had refused its singleton socket before page launch; that environment failure was not counted as acceptance, and the sandbox was never disabled. A separate local backend-test attempt could not start because pytest was absent; CI backend status is recorded independently.

## Confirmed release blocker

Security Gate run **44169376591**, job **134344036997**, at the final source failed `Audit production frontend dependencies` with exit 1:

- `picomatch`, high severity, **GHSA-fc5q-39xc-vjpf / CVE-2026-112366**.
- Five dependency paths using existing locked versions **2.3.2** and **4.0.7**.
- `Audit exceptions: NONE`.

The subsequent `Reject vulnerable spreadsheet runtime` and `Verify production build and frontend security manifests` steps were **skipped**, not passed. Root and the independent reviewer read the job log. The dependency manifest, lockfile, security workflow and audit checker are unchanged from Production. No bypass or unrelated dependency remediation was introduced. The registry's recommendation/patch-version fields are not proof about current upstream availability.

## Continuation record and next safe action

The Issue #1006 update endpoint acknowledged task entries, but repeated normalized and raw reads still returned its older body/timestamp without them. Ledger read-back is **unconfirmed**. The source/evidence branches, PR and this document are the verified durable continuation record; preserve unrelated ledger content when reconciling that discrepancy.

Resolve the existing dependency audit in a separately reviewed change, execute the skipped security checks, and complete any remaining required CI before considering release. Any later publication must follow the repository's v5 A/B intent, clean rehearsal, serial guard and live verification protocol. Do not merge/deploy this documentation-only evidence branch or represent the feature as already published.
