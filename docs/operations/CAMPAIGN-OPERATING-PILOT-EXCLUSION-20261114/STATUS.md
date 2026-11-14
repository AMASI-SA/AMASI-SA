# Campaign operating basis: disabled pilot exclusion

## Goal and proven cause

The user requested complete campaign direct costs, operating expenses allocated using the executive summary's current unrounded expenses-to-sales ratio, and net profit. PR1924 is published and passed its single official three-probe verification at 2026-11-14T18:14:07.202705Z, with the owned lease closed. Live numeric acceptance found an explicitly disabled pilot salary contract without effective normalized salary history. The executive salary reader excludes it; the campaign reader omitted its policy fields and incorrectly treated it as a missing contributing payroll cost. Authorized owner-scoped reads established the cause without changing business data. No private identifiers, staffing counts or financial values are included here.

## Reviewed change

Project the existing status, payroll_enabled and accrual_policy fields. After shared identity/calendar and salary normalization, omit only empty normalized history with all three explicit markers: status=pilot_only, payroll_enabled is False, accrual_policy=disabled_during_pilot. Valid revision history and a valid legacy baseline still contribute even with obsolete markers. Contributing contracts with missing or invalid costs remain unknown.

Five synthetic regressions compare the actual executive salary cohort, preserve valid revision/legacy history and protect explicit/default contributing contracts with missing history. No operating-ratio formula, currency, read limit, deadline, recurring cost, direct cost, profit rounding or frontend behavior changes.

## New base and superseded release candidate

Base J5 is PR1923 merge `af9caabb3119aeb9cf450af0a8b89d376cef8c74`, tree `b5aba4c6ec2755fb27fe084c2b74f87f9d8fddf6`, which entered production history after the pre-intent check for PR1926. It adds delayed Salla customer-option preservation. Independent full-tree and source review confirmed the pilot source, tests, payroll, expenses, recurring, currency and acceptance workflow remain identical to the previous base, so the same reviewed source/test bytes are reused.

PR1926 remains immutable and is superseded: A4 `1140a3fec8ef77f81fdd0cf768f9a89fb2db4ab0`, B4 `8351754572cfd82833b9901e794275f6598f79f1`. All 11 A4 workflows passed on attempt 1, including 253 backend and 50 frontend source-coverage tests. Its candidate intent passed independent validation, but B4 became unmergeable against the advancing production base and no B4 workflows ran. No B4 publication occurred.

The new source A5 directly descends from J5 and retains J5's intent blob `f44168176bc3a995776f71e4aa61ea4ee9509366` unchanged. Never carry the old B4 intent into A5. Extract a new reviewed intent from exact-A5 CI and create a new intent-only B5.

## Verification status and next steps

- Source module blob `b0576e365eb2a5b4f1e105e2a1be17f1d2393655`; test blob `5ec985aa2307f07d05b87486f59c9fb764205ed7`.
- Independent narrow patch review: PASS; patch SHA256 `dee247aa5fe6d2dff46d96537e0c390a60b9644adf91679dd65e1916583aaa0c`.
- Actual-helper offline regression: baseline 46 passed / 1 failed on the disabled-pilot case; candidate 47 passed / 0 failed. Local pytest was unavailable; A4 CI above executed the real tests.
- Exact-A5 CI is pending and must pass independently. Previous A4 results do not replace it.
- PR1923's separate original release is being prepared by its owner. Respect its guard and preservation state; no original-source, lease or deployment change from this task before the required gates and fresh ownership checks.
- Require all A5 gates, exact candidate validation, all B5 gates and both host proofs, ordinary tree-equivalent merge M5, exact-M5 push gates, original build/package proof, official prepare/prepublish/single publication/three-probe verify and actual live numeric acceptance.

Continuation ledger: `codex/campaign-hover-profit-release-handoff-20261114`. Issue #1006 is full; this task status and PR body retain the required checkpoint. No business-data, advertising, sync, financial-setting or paid-agent changes are authorized or performed by this task.


## Exact A5 checkpoint

PR1927 https://github.com/AMASI-SA/AMASI-SA/pull/1927 is frozen at source A5 `3545ef861c17f1411d6258efb5db4a5aaf52f577`, tree `d0990feaa0514cf06fd09eb601051c116bb8be46`, sole parent J5 `af9caabb3119aeb9cf450af0a8b89d376cef8c74`. Independent commit/history/full-tree review passed: exactly the two reviewed code/test blobs plus updated STATUS; J5 intent unchanged. Exact A5 CI is running (readiness43626531393; coverage43626531408). PR1926 is closed and archived separately with all passing A4 results and its unused B4. Do not resume that old candidate.
