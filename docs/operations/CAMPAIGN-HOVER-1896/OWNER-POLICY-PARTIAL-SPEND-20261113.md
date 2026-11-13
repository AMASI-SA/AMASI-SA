# Owner correction: available Snapchat spend is usable with missing hours

Decision: 2026-11-13. The owner explicitly accepts valid available Snapchat
campaign spend in the card even when some hourly buckets are missing. This
supersedes the earlier requirement for complete hourly coverage before CPA/ROAS.

## Implementation

- Preserve and convert the sum of observed valid hours, including explicit zero.
- Keep `coverage_complete`, `missing_hours` and observed-hour counts truthful.
- Record `spend_evidence_valid` independently of hourly completeness.
- Missing hours emit `spend_warning=snapchat_hours_incomplete`; they do not null
  spend or block CPA/ROAS. The card shows the amounts and an advisory notice.
- Invalid/conflicting evidence remains unavailable, as do unaligned account-day
  windows, future timestamps and missing recorded currency conversion rates.
- Unknown legacy amounts are not recovered from unaudited `known_spend_native`:
  that field previously also included potentially conflicting evidence.
- Salla order, piece and SAR-sales aggregation remains independent and unchanged.
- Division by zero stays undefined. Zero observed spend does not claim a final
  zero-spend day when hours are missing.

## Scope and release

No provider calls on hover, no new polling/cron, no new DB queries, no change to
sync frequency, accounting eligibility, production branch or release intent.
This is a source change on PR #1905 only; it is not a production deployment.

The existing campaign-hover source workflow runs the revised writer/reader
acceptance tests. The revised UI tests cover available spend/CPA/ROAS with missing
hours and explicit partial zero. CI results must be checked for the exact commit;
writing this note is not evidence that tests passed. Preserve the V5 source A /
intent-only B release process and complete its checks before production merge.
