# Evidence branch — WIP, not a release candidate

This documentation-only branch preserves the active snapshot-loading repair and pre-change regression. Do not merge or deploy it. The source task branch is `codex/operational-snapshot-load-fix-20261114`; its local base is the prior verified production M. Implementation and positive verification remain in progress. The preserved regression code is a WIP snapshot, not proof of readiness.

---

# Operational snapshot loading repair — 2026-11-14

## Current stage

Implementation and independent verification are in progress. The user explicitly authorized fixing, merging and publishing Mezan. No source from this follow-up has been committed, merged or published yet.

Repository: AMASI-SA/AMASI-SA. Task branch: `codex/operational-snapshot-load-fix-20261114`. Exact production base J: `4860548f0b032e8e3beca18a4776413cd7e0368b`, tree `9a98153a5b6b69bc6a282613a1b3e354e1fe75b9`. Root owns integration, GitHub writes and the original operator. The implementation owner may edit only the archive reader and its new focused test module.

## Production evidence and scope

The prior payload repair PR1915 was normally merged at J, published as `effdd19` with explicit completion Nov14,04:57AM UTC+3, and officially verified against source `a496dd428241b06d2292286de030c70d6241fcf0` and runtime `rg5-ec82d7649d17b217dd5a6c422bbe4e42138c25fe28236a9f9e6531f16a6b845e`.

The original official verifier exited0 after571.594806323s, from01:59:31.583329UTC to02:09:03.178132UTC, with three probes,582 frontend requests per probe, exact identities, and a fresh inactive guard. Its stdout SHA-256 is `308de6a11d0d5293f6fbdd4b0364d063c63da9dd21faa33ed77aa0e40800d7be`. The prior release is valid, but live freshness acceptance failed: the page retained its12Nov01:07 snapshot with the whole-refresh timeout warning.

After the production runtime log viewer became accessible, root performed one bounded visible diagnostic refresh at02:30:17.361UTC. Both the financial page and log page remained visible during cross-tab observations; no financial confirmation or settlement action was performed. Root captured the current-release runtime tail at02:31:52.895UTC, before the relevant entries rotated out of the100-line UI window.

| Stage/request | Timestamp UTC | Duration | Outcome |
| --- | --- | --- | --- |
| native_evidence |02:30:44.060|23437.488ms|complete|
| snapshot_load |02:31:43.920|59693.907ms|cancelled|
| aggregate |02:31:43.921|59860.253ms|cancelled; contains snapshot_load|
| POST operational-balances/refresh |02:31:43.925|85793.467ms|HTTP408|

This attempt did not reach source_collection or reconciliation. Do not sum aggregate with its child stage. The page settled back to the same old snapshot. The deadline remains85 seconds; this work does not raise it.

Sanitized live evidence and the full previous release chronology are preserved on documentation-only branch `codex/operational-source-fix-handoff-20261114`, checkpoint `62c108e8696b8d54f9d29ad5747815bef3546686`, under `docs/operations/OPERATIONAL-SOURCE-READ-REPAIR-20261114/`. Do not merge or deploy that handoff branch. Issue1006 is at its comment cap; PR1915 conversation comments carry the continuation ledger until this follow-up has its own PR.

## Narrow repair design

The existing visible snapshot loader creates a separately guarded calculation for each immutable audit page. For a packed snapshot with P archive pages, the snapshot-load path performs2P+8 authorization/lease checkpoints, in addition to exact page reads. Each owner-browser checkpoint re-reads the permission/scope inputs and renews the owned lease. This is a code-level operation count; production page count and per-operation latency are not known.

Batch only these repeated guard boundaries in groups of at most8 pages, and only when the active visible-demand context owns the exact database object and owner. Read each page with the existing exact `_id` and `owner_id` query, then fully validate it before reading the next. Keep worker preparation owned, cancellable and joined. Revalidate scope/authorization/lease before and after every bounded group, and retain the existing guarded restoration, copy, mutation and commit paths.

The following remain mandatory:

- Read only manifest IDs from the same stored revision; preserve page order and every event and tail entry.
- Preserve manifest/hash/owner/start/count/BSON validation and fail on the first corrupt or missing page before the next lookup.
- Keep ordinary readers and strict same-session wrappers on their existing behavior. No bulk query, owner scan, parallel session operation or new index.
- No authorization cache, cross-request or CAS-retry cache, discarded history, changed financial formula, increased limit/deadline, background financial work or retry loop.
- No archive mutation, TTL or deletion. Majority page staging and revision CAS remain unchanged.
- On cancellation, join owned I/O/calculation work and expose no partially validated state; on authorization or lease failure, do not return a state or execute a financial callback/commit.

## Verification so far

Before changing the archive reader, the new regression test restored a complete64-page synthetic state, verified equality, exact64 page reads, input preservation and zero nonlease writes, then failed only on the guard-amplification assertion:136 checkpoints versus a maximum24. Pytest actual exit1,1 failed in1.21s. The expected failure is preserved in the implementation evidence; it is not a production measurement.

An isolated prototype retained64 exact page reads and full-state equality while reducing checkpoints from136 to24. With a synthetic3ms delay per database operation, the fixture changed from2406.679ms to677.074ms. These are synthetic benchmark observations, not measured production improvements.

Required positive verification remains: the focused new regression and parity cases; corruption-first-error behavior; both guard-boundary revocation/lost-lease cases; I/O and worker cancellation/join; matching-context opt-in and strict-wrapper fallback; existing archive capacity/encoding/lifetime and demand-refresh suites; an independent review; exact-source CI; the reviewed A/B release chain; original-operator rehearsal; official publication verification; and a new visible financial calculation with an advanced as_of and no stale-refresh warning.

## Preserved and deferred work

The previous order projection remains in production and is inherited unchanged. A separate synthetic reconciliation benchmark proved quadratic CPU costs at20k provider orders, but production did not reach that phase in the captured attempt. That optimization is deferred; it is not this repair's claimed cause or scope.

The original `/app` is at J. Its120 preexisting untracked files and unsaved editor work were preserved by the prior release; do not reset/clean them. The last verified release lease closed normally. A fresh original guard read is required before any later original-host mutation or rehearsal; never reuse or clear another owner's lease.

## Next safe action

Finish the confined implementation and tests, preserve a verified source checkpoint, and obtain independent review. Re-read production before freezing source A; then obtain the exact reviewed candidate artifact and freeze intent-only B. Use a normal merge with unchanged source/intent ancestry and an identical merge tree. After all required gates, perform the real original-host build outside GitHub, prepare/prepublish on our own matching lease, publish once, require explicit newer completion plus official three-probe verification and fresh inactive status, then verify live freshness.

Production changed by PR1915: **YES**, evidence above. Production changed by this snapshot-loading follow-up: **NO**. Fresh successful financial calculation: **NOT YET PROVED**.
