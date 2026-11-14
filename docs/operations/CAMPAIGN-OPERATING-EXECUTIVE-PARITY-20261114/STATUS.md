# Campaign operating allocation: executive parity

Task: CAMPAIGN-OPERATING-EXECUTIVE-PARITY-20261114

## Scope and observed gap

Follow-up to PR #1920. The published card exposes the requested direct costs, operating allocation and net profit rows. Live read-only acceptance found operating allocation/net unknown while the executive operating row remained numeric. No customer data or campaign figures are retained here. The live cause has not been attributed conclusively; a narrow lookup in the original workspace database did not identify the campaign owner, so it did not invoke a financial calculation.

Source review proved two mismatches affecting valid cohorts: the campaign reader rejected recurring obligations whose shared executive calculator legitimately contributes zero; payroll validation considered contracts before filtering excluded employees and noncontributing days. A redundant outer deadline also erased the operating reader's structured timeout reason.

## Candidate changes

- Reuse the shared recurring daily contribution, including valid zero, for the executive expense numerator. Negative, nonfinite or unreadable results remain unavailable.
- Apply owner-scoped employee identity and day eligibility before validating cost; retain original contract amounts, shared payroll/calendar methods, duplicate rejection, read caps and deadlines.
- Let the operating reader's existing three-second timeout return its structured result; direct cost/settings deadlines stay unchanged.
- Preserve full expense/sales ratio, monetary rounding, negative profit behavior, and direct-cost completeness requirements. No provider calls, writes, allocations, scheduling, or refresh jobs are added.

## Verification state

Base is reviewed production M `29ec1e07eb7d1a23647cadff5bac26149accfa99`; its tracked release intent is unchanged in this source candidate. Root static review passed; independent review and repository CI remain pending. The isolated actual-function payroll probe passed 42 cases (the original failed 31); this is not pytest or a Mongo integration run. New repository tests cover canonical zero-contribution parity and timeout propagation as well as payroll scopes. Actual repository suite outcomes must be recorded before readiness.

The candidate has not been merged, built in original /app, prepared or published. The preceding release's already-started official verification must finish and the guard must be inactive before changing original /app or starting its next release rehearsal. A2/B2 must retain ordinary ancestry; B2 changes only the CI-produced reviewed intent. Do not reuse the old lease or runtime identity.
