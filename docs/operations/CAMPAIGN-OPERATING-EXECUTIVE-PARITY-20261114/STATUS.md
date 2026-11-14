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


## Final source and review checkpoint — 2026-11-14 16:28:20 UTC

- Final source candidate A2 `0f5bca7427fed7851008d0f7d64e23414746bdc1`, tree `01ef389fd3b167e602fe8a889210641d7b148d4c`, on `codex/campaign-operating-executive-parity-20261114`; draft PR#1922. Two ordinary source commits fromJ2, no intent-history edits.
- Independent review found and corrected the initial candidate's invalid utility estimate fallback. Only a zero daily contribution inspects an explicit configured estimate; positive actual/historical invoices remain authoritative. Final R2 source/patch hashes matched and root/independent static reviews passed.
- Prior source `22832941d5d61d77ed5c6acb2b3a6565423ce675` source-coverage/readiness passed; those results do not certify the final A2. All required tests/CI must run on `0f5bca7427fed7851008d0f7d64e23414746bdc1` before freezing B2.
- Preceding PR1920 official production verification succeeded three probes at16:22:18.751950UTC, fresh guardinactive16:23:12.874031UTC. Its publication/verification must not be repeated.
- No follow-up original build, lease, merge or publication yet. Next: exact-sourceCI and its reviewed-intent artifact; then intent-onlyB2 with fullCI/hostrehearsal evidence.
