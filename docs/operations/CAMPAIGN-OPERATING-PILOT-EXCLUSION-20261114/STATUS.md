# Campaign operating basis: disabled pilot exclusion

## Scope and reason

The user requested campaign direct costs, operating expenses allocated with the executive summary's current unrounded expenses-to-sales ratio, and net profit. PR1924 is deployed from source `c484b549e8bca017627ce80749c7895198f04bf8` with merge `77e4acf33e8f5432d97bda5d7208983f0fadc599`, but live numeric acceptance exposed a remaining operating-basis blocker.

Authorized read-only owner endpoints showed an explicitly disabled pilot salary contract with no effective normalized salary history. The canonical executive salary reader excludes it. The campaign reader's bounded projection omitted the pilot policy fields and treated the empty history as a missing contributing cost. Private identifiers, staffing counts and financial values are not recorded here.

## Changes

- Project `status`, `payroll_enabled` and `accrual_policy` with the existing bounded contract read.
- After the shared identity/calendar and salary-history normalization, omit only a contract with empty normalized history and all three explicit disabled-pilot markers.
- Keep valid salary history and legacy effective baselines contributing even if obsolete pilot markers remain.
- Keep missing or invalid costs for contributing contracts unknown; preserve currency, deadline, read-limit, recurring-expense, campaign-profit and frontend behavior.
- Add five synthetic regression cases, including comparison with the canonical executive salary cohort and proof of no contract writes.

## Verification and release state

- Baseline: exact production merge `77e4acf33e8f5432d97bda5d7208983f0fadc599`; inherited release intent remains unchanged.
- Independent static review: PASS for the two-file patch, SHA256 `dee247aa5fe6d2dff46d96537e0c390a60b9644adf91679dd65e1916583aaa0c`.
- Offline probe using the actual shared salary helpers: baseline 46 passed / 1 failed on the disabled-pilot case; candidate 47 passed / 0 failed. This is not a substitute for pytest/CI.
- The scratch environment lacks pytest and mongomock_motor. Exact-source CI is required before an intent-only commit or merge.
- PR1924's single official production verifier is running. This follow-up is isolated; its original build and guard phases must wait for the current owned lease to close.
- No business data, advertising, synchronization, financial settings or paid agent operations were changed.

## Remaining gates

Freeze source A on the exact current production base, pass all applicable A workflows, independently validate its generated intent, freeze intent-only B, pass both clean-clone host adapters and all B workflows, merge ordinarily with the same tree, pass exact-merge push gates, then perform the original build/package proof and official prepare/prepublish/single publication/three-probe verify. Require actual live operating and profit arithmetic acceptance after publication.

The durable continuation ledger is on `codex/campaign-hover-profit-release-handoff-20261114`. Issue #1006 has reached its comment limit; the task status and PR body retain the required checkpoint instead.


## Frozen source checkpoint

PR1926: https://github.com/AMASI-SA/AMASI-SA/pull/1926

Source A4 `1140a3fec8ef77f81fdd0cf768f9a89fb2db4ab0`, tree `d9bf5df2fc8529d484988c5b743160c59aff5315`, sole parent M3 `77e4acf33e8f5432d97bda5d7208983f0fadc599`. Independent GitHub commit, recursive-tree and history review passed: exactly the reviewed operating module, tests and this task STATUS; inherited intent blob `2159398256b11323a9cce8e09f89508bb6f18818` is unchanged.

All11 expected source workflows have started. Actual frontend source coverage passed9suites/50tests; remaining gates are still being observed. Readiness run43625326307; coverage run43625326323. Disabled normal original-build/gate templates are independently reviewed and retained here; no current original-workspace or lease mutation.
