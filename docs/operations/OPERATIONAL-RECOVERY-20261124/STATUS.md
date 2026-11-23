# Operational balance recovery — 24 November 2026

## Ownership and release boundary

The current owner explicitly requested implementation, tested integration,
publication through Emergent administrative controls/terminal, historical
source-backed recovery and direct verification. Paid Emergent chat is excluded.
The root coordinator alone owns commits, integration, publication and runtime
mutations. Three reviewers own bank dates, source coverage and isolated
automation respectively.

Reviewed source base J is the already merged production branch commit
`28f6d4ca0fc2b7ea7f140432d8e32c768823b6a4` (PR #2096). It contains merged PR #2092.
Neither merged PR will be merged again. The separate draft PR #2095 is left
untouched. At intake, direct production identity remained PR #2087 source
`b69d438a20ae123a0c30deb5b73599551dbe9559`, release
`rg5-2ee2cc1f78b161b33fc25370288e2daaa6b26d15af6d483d2016c49b0fd0b418`.
The shared Emergent checkout already advanced to J under its other task owner.
Our initial read-only checks observed the original guard inactive and preserved
the clean tracked tree and unrelated untracked files.

## Source corrections under verification

- An unmatched automatic bank credit requires an authenticated original event
  timestamp. Existing matching manual/automatic postings are checked first;
  missing historical proof remains an explicit issue. Audit recording time is
  independent of the movement's original time.
- Compact entity coverage is saved with the same financial CAS and validated
  against owner, revision and calculation time. Daily payroll and advertising
  coverage uses actual saved evidence. Event-driven sources remain unknown
  when completeness cannot be established; absence is never a fabricated zero.
- Opening or revealing an operational page reads saved health only. A deliberate
  manual refresh retains the original authorization, lease, cancellation,
  deadline, uncertain-outcome and confirmed-readback contracts.
- Independent automation is explicitly enabled by the current owner. It has a
  serial isolated child, generation revocation, bounded lifetime, existing owner
  refresh lease and repeated current-owner/accounting authorization. A successful
  attempt alone never asserts complete financial coverage.
- The settings panel is owner-only, cancels unnecessary reads on exit, requires
  fresh saved readback and forbids repeating an ambiguous setting write until
  status is verified.

## Verification checkpoint

The two new stale-page/day-boundary regression cases failed on the original
manager because a page opening issued a financial POST. After the change,
the manager, hook lifetime, automation settings and workspace-loading selection
passed: 60 cases across four frontend suites on Node 22.23.2.
Bank original-date, arbitration and rollout unit tests and entity-coverage tests
are independently exercised. Final totals and required CI remain pending.
No test substitutes a new balance timestamp for ledger recovery evidence.

The local execution environment cannot start the official Mongo server; this is
not treated as an integration pass. The existing real-Mongo GitHub acceptance
gates must execute and pass on the final immutable candidate.

## Current operational outcome

Work in progress. No new release from this task has been published and no
financial recovery is claimed. No production provider/balance mutation has been
performed by this task at this checkpoint. The next steps are independent
review, final CI, reproducible A/B build, immutable intent B, fresh shared guard
and source checks, clean official adapter rehearsal, original guard publication,
then direct production identity and actual financial-delta verification.

After freezing A/B, append subsequent release evidence in the PR continuation;
do not change governed source or rewrite the frozen pair. Preserve a truthful
separation between provider evidence gaps, recovered original-day records,
unknown dates and ordinary current observations.
