# Source-collection repair — diagnosis checkpoint

This is a documentation-only checkpoint, not a release candidate. Production remains PR1916 M `9c71435f4a19977c09f4331dee8fddbfa12a45eb`, tree `c5bd7fb6c357e17c17104282d1d175676e95ed3e`, platform bfa6a5f. Its publication and official three-probe verification passed, but live financial acceptance failed.

## Proven live boundary

The visible attempt begun2026-11-14T03:57:55.990Z completed native evidence in2926.310ms and snapshot loading in9084.282ms. Source collection was cancelled after72942.769ms at the85040.132ms total-request deadline (HTTP408). Reconciliation did not begin. The report retained the previous as_of and showed a refresh failure. Exactly three attempts occurred on PR1916; no further attempt is planned before a code change.

## Current work

Fresh remote production and local FETCH_HEAD both match the full J above. A new isolated source branch is `codex/operational-source-collection-fix-20261114`. No source edits are frozen. Review found no per-source DB authorization amplification in the primary batch. Its four-way reads, the collision preflight, secondary reads and repeated order-level CPU scans must be distinguished. A bounded synthetic measurement of fee, assignment and cost scans is in progress. No live database CLI, credentials or additional financial request is used.

## Preserved constraints

Complete owner data, history, contradictions, row caps, cancellation, authorization and the85-second request deadline remain required. No payment, confirmation, settlement, payout or provider test write. Original /app has120 unrelated untracked files and unsaved Untitled-1; this follow-up has not changed it or created a lease.

## Next safe action

Use the measurement to select the smallest proven improvement within source_collection. Add fixed-name substage timing where necessary without values, IDs, counts or exception text. Verify parity and safety regressions, independent review, exact-A CI and official artifact before freezing intent-only B; then normal merge, original build, guarded publish, official verify and one fresh visible acceptance.

Issue1006 reached the2500-comment limit; its last entry documents continuation via task PRs. Preserve this STATUS and the task PR at each material milestone.
