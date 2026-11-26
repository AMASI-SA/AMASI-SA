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
