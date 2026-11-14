# Operational source-read recovery — 2026-11-14

## Scope and authorization

The owner asked to continue after the published financial sidebar pages.
The remaining goal is a successful, bounded manual financial-source refresh.
Prior explicit repair, merge and publication authorization remains in force.
Preserve the user's demand-only policy: no automatic refresh, polling, detached
work, provider synchronization or real financial transaction as a test.

Repository: AMASI-SA/AMASI-SA.
Task branch: codex/financial-source-read-recovery-20261114.
Starting production base: 77e4acf33e8f5432d97bda5d7208983f0fadc599.
This includes the unrelated campaign operating-cost/executive-parity PR1924.
Prior financial pages PR1921 and all report tables must remain intact.

## Resumed evidence

The previous final documentation checkpoint is
1b192b0af21fd7efd48507a58544561f10292505 on
codex/financial-section-pages-handoff-20261114. Root fetched and verified it,
read AGENTS.md, the task status, PR1921 and the final available Issue1006 page.
Issue1006 is already full at2500 comments; do not repeatedly retry writes there.

PR1921 was published and officially verified. Its single post-release manual
refresh retained the dated saved report and displayed an orders source-read
timeout. The actual response status/detail was not captured. Source mapping
identifies unified_orders/operational_source_read_timeout, not a proved live
HTTP status or a proved database root cause. Saved as_of remains14Nov18:22Riyadh.

Current live financial tab still shows that warning and saved timestamp.
No new refresh has been triggered in this continuation. No source/DB/provider
mutation or publication has been performed in this continuation.

Fresh original /app read-only preflight at2026-11-14T17:44:51.295264Z:
HEAD77e4acf33e8f5432d97bda5d7208983f0fadc599, correct production branch,
tracked clean,10 nonignored untracked entries, release guard active:false.
The original editor still has one unsaved Untitled-1. A separate temporary
Terminal3 was created for this task; original terminals1/2 were left untouched.
The new count10 differs from the prior release's120; this continuation has not
changed those files. Re-capture a complete manifest before any future /app work.

## Current investigation

Two bounded read-only reviews inspect production-safe diagnostic access and
source-read cancellation/runtime behavior. Existing logs are sanitized but do
not correlate stages to one request; existing request-local stage timings are
returned only on outer408, not the source-specific409 error branch.

The original /app development database must not be assumed to be production.
Historical records explicitly show different preview and production datasets.
Do not infer the authenticated owner or database from matching report dates.
Do not print credentials, customer rows or financial amounts.

## Next safe action

Establish authenticated request-correlated failure evidence and a verified
production read path. If existing access cannot provide that evidence, add the
smallest bounded request-local diagnostic response using existing timings,
with no additional database work or background behavior. Select any performance
change from primary evidence, preserve full financial-source coverage, and
verify it independently before the normal protocol-v5 release process.

Production changed in this continuation: **no**.
Status: **investigation in progress; no fix or refreshed balances claimed**.
