# Operational refresh source reliability — 2026-11-26

## Active task and authority

The merchant authorized fixing the root cause, merging, publishing directly to
Production and continuing live verification until the problem is resolved.
Keep Mezan light and preserve accounting history, dates, evidence, identities
and replay protection. Do not introduce polling, financial test transactions,
invented bank evidence or an approval shortcut. Use GitHub and the original
Emergent administrative/terminal surface; never use paid agent chat or Preview.
The original Release Guard v5 remains mandatory and unchanged.

Task branch: `codex/operational-refresh-source-root-fix-20261126`.
Base and currently deployed commit:
`570b173d1dc6438624c39a5c511311f2a33be9a3` (PR #2132).
Source A for that release: `d7ea8f0d595cbe698dcd92249aa5de9d9f07ffc2`.
Intent B: `5410a442c592bcb96df6dff5109c034652c53f94`.

## Why the task remains open

PR #2132 removed a redundant full-aggregate copy. It passed exact-source and
intent CI, both clean-clone adapter rehearsals, the original-host rehearsal,
publication and all three official production probes. Its matching lease is
closed and the local development backend was restored.

The first owner-visible manual refresh after publication, started at
2026-11-26T00:42:56.558Z, failed at the existing primary orders read deadline.
The previously saved financial snapshot remained in use. No new successful
financial calculation was accepted. Sanitized console diagnostics at
00:43:38 UTC reported:

| Stage or metric | Observation |
| --- | ---: |
| Native evidence | 13,764.259 ms |
| Raw aggregate read | 76.083 ms |
| Aggregate unpack, including archive reads and authorization | 9,685.698 ms |
| Redundant copy preparation | 0.002 ms |
| Primary orders cursor read | 15,501.483 ms, cancelled |
| Primary orders retained | 18,432 |
| First order row | 5.906 ms |
| Application queue | 0.018 ms |
| Source read budget | 15,000 ms |
| Cancellation join / cursor close | 1.634 / 1.570 ms |

The first fix demonstrably removed its duplicate-copy cost, but does not yet
establish reliable live refresh. First-row latency does not support a missing
index claim; the remaining cursor time includes transfer, driver decoding,
buffered iteration and any competing work. Unpack time is not a pure BSON
decode measurement. Do not change deadlines or narrow financial evidence to
make the failure disappear.

The previous release's durable publication/failure checkpoint is
`be634ebb2d54d4bdf4f4bf381d1c82e1222f446b` on
`codex/operational-refresh-publication-evidence-20261126`.
That documentation-only branch must never be merged or deployed.

## Current work and next safe action

Independent reviews are tracing primary-source materialization and the actual
installed Motor/PyMongo cursor implementation. A separate measured optimization
removes an unnecessary immutable BSON-buffer copy during packet decoding;
complete parity and malformed-packet regression checks remain required.

Observe the next existing scheduled attempt without changing automation
settings. Any further source fix needs exact-source CI, a fresh source A and
intent-only B, both adapter rehearsals, the original-host v5 protocol and live
accounting acceptance. Retain private financial reports outside this repository.
Issue #1006 has reached its comment cap; use this task document and task PR.

Production changed by this follow-up: **no**. Work is in progress. Do not report
completion from publication identity or pre-release accounting evidence alone.

## Verified implementation checkpoint

The next natural scheduled attempt at 03:53 Asia/Riyadh independently failed
at the same 15-second order-source budget. It retained 15,261 rows; first-row
latency was 10.446 ms, total cursor time 15,133.833 ms, and unpack took
11,097.544 ms. The existing retry policy scheduled another attempt at 03:58;
no scheduling setting or retry loop was added by this work.

The first verified follow-up change uses the public `bson.decode` function on
the already validated immutable decompressed bytes. Constructing `BSON(data)`
previously copied the entire buffer before invoking the same decoder. Snapshot
and diagnostic packet hash, length, marker, owner, BSON type, shape and
cancellation checks remain unchanged. This is a measured allocation reduction,
not a claim that all unpack time or the source timeout is fixed.

The new tests reproduced three copy-regression failures on the production
base, with twenty integrity/cancellation cases passing. The minimal decoder
change passed all 83 affected packet/read-lifetime/audit-batch cases in 2.61 s.
Root independently passed the 23 new cases and 20 existing replacement-state
cases: 43 passed in 3.25 s, no skips. Primary cursor work remains diagnostic;
do not treat this checkpoint as ready to merge or publish.

## Wider deadline evidence and implementation decisions

The natural 03:58 Asia/Riyadh attempt completed all 19,143 primary order rows
in 14,990.402 ms, but the unchanged 85-second request budget expired during
reconciliation. Native evidence took 8,777.906 ms, unpack 10,605.911 ms, primary
reads 15,011.137 ms, total source collection 52,665.964 ms and reconciliation
12,937.578 ms before cancellation. Source collection included 15,124.454 ms
for bundles and 14,106.373 ms for the order loop. Profit context took
17,158.217 ms; its product authority was cancelled under its separate 25-second
budget. These stages overlap and must not be summed indiscriminately.

One bounded real-Motor synthetic wire experiment compared current concurrent
primary reads with orders-first admission in A/B/B/A order. It retained exact
payload/query/projection/cap/digests and used a separate pre-encoded responder.
The experiment did not show a consistent total-wall or CPU improvement from
orders-first admission. Keep current four-way concurrency; do not select only
the favorable first run. The saved findings explicitly distinguish synthetic
payload assumptions from observed production facts.

A proposed reuse of primary raw rows for profit is deferred: normalized profit
fields, date-cohort membership, dotted projection behavior, conditional shape
proof and same-revision source writers require a larger freshness contract.
Do not silently substitute full raw documents or reuse uncertain proof.

Further work is deliberately limited to a complete-state copier already used
by the project, a request-local delivery collection index that preserves every
row/duplicate/predicate, and explicit primary-order deadline allocation. The
approved deadline policy under implementation gives only the primary full-owner
order read 20 seconds; all other primary reads retain their 15-second default.
The 85-second outer request limit and any earlier inherited source deadline
remain authoritative, with one cumulative driver deadline across batches and
owned cancellation/cleanup. Query, projection, cohort and the 20,001 overflow
sentinel remain unchanged. This supersedes the old task's all-primary-15s
description for this one callsite; it is not an increase of the total request
budget or a substitute for the measured work reductions. Complete live-save
acceptance is still required before claiming reliability or lower load.

## Integrated implementation and verification

The implementation is limited to five production modules:

- `operational_balance_archive.py`: avoid copying the entire validated BSON
  buffer before each existing packet decode. Synthetic peak allocation fell
  by 31,034,888 bytes for a 31,035,717-byte uncompressed packet; logical values,
  BSON types and input bytes remained identical.
- `operational_balance_engine.py`: use the project's existing exact-container
  copier for the initial complete independent state. Preserve every historical
  field, fallback type, alias/cycle and all other local copies. A synthetic
  53,997,836-byte state through the real reducer had a whole-reconciliation
  median of 3,427.735 ms before and 2,831.984 ms after; these are not live counts
  or a production speed guarantee.
- `operational_delivery_collection_index.py` and its source integration: index
  each unchanged receipt cohort once per attempt by assignment identity. Keep
  the entire original driver/custody/event predicate, row order and duplicate
  occurrences. Unsupported structures use the old scan. A 128-order synthetic
  fixture reduced receipt dictionary reads from 17,280 to 1,152; doubling the
  fixture now doubles this work instead of producing 3.8 times as much.
- `operational_read_runtime.py` and the single primary source callsite: the
  explicit 20-second orders policy described above, with default 15 seconds
  elsewhere, four-way admission, outer deadline clamping and cumulative CSOT.

Root inspected the integrated diff and ran 16 affected offline modules against
the combined implementation: **453 passed in 15.13 seconds, no failures and no
skips**. Exact output is retained in `evidence/combined-offline-regression.xml`.
An earlier independent root check passed 55 packet/copy/replacement cases in
4.95 seconds. These counts overlap and must not be added together.

Peer verification included 83 packet/read-lifetime cases, 109 initial
copy/reducer cases followed by the final 12 strengthened copy tests, 71
budget/runtime/scheduling cases, and 84 new collection-index cases. Independent
source review found no blocker in the index and budget boundaries. Additional
affected offline shipping verification passed 577 cases, with three supplier
preview cases skipped and three real-Mongo delivery cases explicitly excluded
from that local-only command after connection refusal. The local driver-writer
module could not collect because `email_validator` was absent. Full CI must
execute these layers with its real isolated Mongo fixture and complete project
dependencies; local results do not replace those gates. No test was weakened.

The first remote verified checkpoint is
`e79d595fb0fac75650b79613d40c1d1c8eb04051`; retained experiment/decision checkpoint
is `8dcae5038d388fbd977b3056339b94e914927562`. PR:
https://github.com/AMASI-SA/AMASI-SA/pull/2133.

Next safe action: publish this integrated source as final A, require exact-A CI
and its candidate intent, freeze intent-only B, require exact-B CI and both
clean-clone rehearsals, then complete the unchanged original-host v5 release
protocol and live financial acceptance. No follow-up release lease exists and
Production still runs PR #2132. Do not reuse any previous candidate or intent.
