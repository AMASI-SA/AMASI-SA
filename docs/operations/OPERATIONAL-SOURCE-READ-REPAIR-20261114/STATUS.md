# Operational source-read repair — 14 November 2026

## Current status

Investigation in progress; no fix, release intent, merge or publication by this task yet. The user explicitly authorized fixing, merging and publishing. The accepted behavior remains page-owned computation: refresh only while visible and cancel/join work on hide or exit. No background calculation, polling, financial confirmation, settlement or payout is authorized as a test.

## Continuity and current base

- Task branch: `codex/operational-source-read-repair-20261114`.
- Production base read: `304e8793a15fd3269fd6f6d4cfba903d1760e285` (PR #1912), including the prior campaign changes from #1911.
- Previous balances release #1906 is already merged and officially verified. Its later live application check failed; do not republish that source/intent.
- Previous handoff: [STATUS](https://github.com/AMASI-SA/AMASI-SA/blob/a496614da4a569817fd1e959b25989ae62cbe31b/docs/operations/OPERATIONAL-BALANCES-FOLLOWUP-20261113/STATUS.md).
- Issue #1006 has reached its comment cap; #1902/#1906 are balances continuation threads.
- No coordinator marker or repository trigger exists. Root owns this isolated branch and integration; subagents review read-only.

## Proved application result and remaining gap

At 2026-11-13T22:30:18.040Z, the visible financial page settled with the source-read failure warning and retained `as_of=12 November 2026 01:07` (UTC+3). Its newer report-render timestamp did not demonstrate a successful calculation. The exact message maps to the SourceReadError handler in `source_collection`, inferred from source rather than a captured HTTP response. Current code preserves cause/collection in HTTP409 details, but the UI discards these fields and the failure exits before state/audit persistence. The exact failing collection and cause remain unproved.

Independent current-source review maps this error to the first 32-source batch. It reads owner-only records with an exact post-read owner check, a 20,000-row ceiling and a 15-second per-read deadline. There is no evidence yet justifying changing those safeguards or dropping historical orders. Earlier orders can still generate post-opening delivery, custody and shipping evidence.

## Work completed in this continuation

- Read latest repository instructions, previous handoff and relevant release records.
- Fetched production and created a clean isolated task branch from current base.
- Restored browser/operator UI and verified signed-in account role using ordinary authenticated GET; no cookies/tokens were extracted.
- Fresh original `/app` guard returned `active:false`; `/app` HEAD was `304e8793a15fd3269fd6f6d4cfba903d1760e285`, with empty tracked/index diffs. This is an observation, not a retained lease or authorization to interfere with another task.
- Another Ready release owner is preparing its rollback/rehearsal/publish flow. Do not change its source, artifacts, lease or active work. Fresh status and production head are required before any later operator mutation.

## Next safe action

Use a bounded read-only metadata/count diagnostic on the configured operator DB to test for a current capacity blocker, after positively binding the owner. It must not run refresh/collect_sources/reconciliation, calculate balances, import server startup, write data or print sensitive values. Label the DB provenance accurately; a matching saved timestamp alone is not proof of production DB identity. If no decisive safe evidence is available, add narrowly whitelisted request-failure diagnostics, reproduce one visible attempt, and fix the demonstrated cause. An instrumentation-only release is not completion.

Keep the existing owner scope, full financial evidence and cancellation guarantees. Add cause-specific regression coverage and obtain independent review. Freeze a new source A and intent-only B under protocol v5, preserve both with normal merge, then publish through the original adapter and complete official verification plus a fresh visible-page acceptance check.

Production changed by this task: **NO**. No customer data, account identifiers, order identifiers, financial amounts or credentials are included in this record.
