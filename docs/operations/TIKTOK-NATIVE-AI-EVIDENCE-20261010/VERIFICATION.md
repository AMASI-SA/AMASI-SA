# Native campaign attribution and AI observations

Exact native campaign IDs now participate in Salla order attribution through
the owner's connected TikTok account snapshots. No manual product association
is needed to establish campaign identity. Product links can enrich an already
proven identity. Financial eligibility and historical attribution coverage are
not inferred from identity or TikTok conversions.

The campaign table offers an owner-only Arabic AI observation for one selected
campaign. The server revalidates the account, identity, selected Riyadh dates
and complete native reports before any cache lookup or model call. The existing
runtime OpenAI key/model is reused without exposing credentials. Only an
allowlisted campaign context and aggregate projected ledger record evidence
are supplied. Financial orders, sales and profit remain null.

## Resource contract

- One complete analysis per worker, including source reads; immediate refusal
  without a waiter queue when occupied or the governor blocks work.
- Workspace page remains bounded; the AI reads exactly one campaign.
- Evidence read: at most 501 projected records to enforce a 500-record cap,
  1.5-second Mongo deadline and three-second overall evidence deadline.
- Model context at most 8,000 UTF-8 bytes, one call with automatic retries
  disabled, 1,800 output tokens, 40-second generation deadline.
- Five-minute cache, at most 32 entries, tenant-specific and keyed by the
  freshly verified campaign/date/metric/evidence context.
- Fifteen-second minimum interval for new analyses per owner.
- No media download, provider mutation or financial/accounting writer.
- Filter changes unmount the old analysis before another request can begin;
  cancellation and sequence checks prevent late responses from being shown.

## Fresh execution evidence

Tested source: d683983b342ad3744112fb83a710bc34f171296a.

Focused checks: 70 backend cases and eight UI cases passed. Affected regression:
132 backend cases passed (one existing Starlette warning), and 34 frontend
cases passed across ten suites. Exit codes were zero. Source isolation and
`git diff --check` passed; the isolated worktree was clean.

Real Mongo integration demonstrates native identity resolution, tenant and
account separation, ambiguity/refusal, selected Riyadh date boundaries,
projected evidence, overflow refusal and native workspace-to-ledger-to-model
collaboration. Fake model boundaries never use live keys or network calls.
Deterministic tests demonstrate busy refusal before source reads, cancellation,
bounded cache, rate limiting, malformed/incomplete output refusal and stale UI
responses. CI includes the new backend and frontend contracts.

The first identity baseline failed its intended missing native ID case; the
old AI route returned 404 in five cases and the old UI lacked the analysis
button. The first integrated UI check exposed two requests on a date change;
the selection-bound rendering change corrected that race.

Isolated Mongo measurements:

| Workload | Observed result |
| --- | --- |
| 5,000 campaigns, 150,000 daily facts | 25 campaigns, 750 fact rows, 24,778 response bytes, 593,940 peak Python bytes, 0.1152 seconds |
| 5,000 entities per hierarchy level, 30,000 facts | 90 sequential provider calls, 11,757,593 peak Python bytes, 2.5618 seconds |
| Native order lookup in 5,000 campaigns | One identity, zero catalogue arrays transported, 21,586 peak Python bytes |

These are isolated tests, not measurements of Production RSS or latency.

## Release and remaining evidence

PR1324 is a draft pending the governed A/B release and complete CI. Production
remains the previously verified 094d0ef7fa001d3b3794f2600c77795190ddb1a8.
Live model use is pending the new release. Campaign mutation/creation, organic
publishing, comments and business messages remain later ordered phases with
actual TikTok capability checks; this slice does not claim those operations.

Unknown financial results and missing historical coverage remain explicit.
Exact attribution record counts are observations of available ledger records,
not paid order counts or complete Salla sales totals.
