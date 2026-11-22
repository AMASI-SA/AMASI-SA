# Operational home loading recovery — 22 November 2026

## Scope and current state

The owner reports that `https://mezansalla.com/operational-balances` remains on
the balances loading screen. Prior explicit authorization to complete and publish
this repair remains active. This is a new follow-up incident after the completed
PR #2058 publication; the prior publication must not be repeated as if unfinished.

Repository: `AMASI-SA/AMASI-SA`.
Task branch: `codex/operational-home-loading-recovery-20261122`.
Verified production base: `1c4008e315c35ec4419e76400a9faddb94b9d5a2`.
Prior deployed source: `a8e655aabbeaef64486a1a80f13551dc97dd9259`.
Prior release: `rg5-9d69963c0ee7f5c299595495519175af5074f703f4e318b82b870b0407554a49`.
Prior completed evidence: `73b2ec5ce57a48e496929d2a9d212afb2cba17a8`,
on `codex/operational-context-release-evidence-20261122`.
The new checkpoint's exact SHA is the containing commit, retrievable from this
task branch; subsequent checkpoints will record its full predecessor explicitly.

Production changed by this incident task: **no**.
New lease, publication, rollback, resource upgrade, financial/provider/sync/backfill
write: **none**. Emergent administration and code-terminal access only; no paid
Emergent agent chat.

## Fresh observations

Observed on 22 November 2026, approximately 11:16–11:29 UTC
(14:16–14:29 Asia/Riyadh):

- Cold live navigation reached the application's unavailable-session recovery
  screen before the finance route could mount. This is an availability signal,
  not evidence that credentials were rejected or that the user's session ended.
- Production branch remains the base above. Emergent's latest displayed completed
  publication is 22 November 06:10 Riyadh, consistent with the previous release.
- Emergent Monitoring displays severe resource pressure over its last-24-hours
  window: CPU 84% with 100% peak, memory 65% with 100% peak, error rate 13% with
  16% peak. These are platform display values, not per-request measurements.
- Production View Logs at 11:27:28–11:27:50 UTC show internal upstream timeouts
  for `GET /health`, recurring at five-second intervals. Application timing logs
  in the same window show health requests around 0.9–1.9 seconds, with one
  32-millisecond sample.
- Other concurrent, independently occurring requests are slow: assembly orders
  reads around 112–114 seconds, a delivery status write around 64 seconds, account
  summary reads around 16–26 seconds, and multiple ordinary delivery/event
  requests around 4–7 seconds. This investigation did not initiate those writes.
  These observations establish broader service pressure, not its specific cause.
- Two local-executor public liveness/readiness samples reached an eight-second
  transport timeout. Executor transport/proxy time is not a backend measurement.

The exact screenshot text comes from the initial workspace-context gate.
Its initial request is
`GET /api/operational-balances/workspace/context?ad_bank_fx=true&provider_positions=true&home_preview=true`.
This route already uses the scoped current financial-state reader and does not
read historical audit-page bodies. PR #2058 repaired the distinct legacy context
route; repeating that change would not address this new home path.

## Source investigation and bounded repair plan

Read-only frontend review identified recoverability gaps in this context gate:
the default visible-read lifecycle has no overall deadline while waiting for a
prior physical request; hidden/offline deferral and an invalid authentication
gate may display the same loader with no active request. Normal Axios rejection
already produces an error. These gaps are confirmed in source but not yet the
demonstrated trigger in production.

A narrowly scoped frontend follow-up is being prepared using the existing
single-attempt policy, preserving identity checks, physical-request serialization,
and explicit retry after cancellation/offline interruption. Other balance reads
retain their current default behavior. It will require focused red/green tests.

The current financial report still decodes/copies current state and builds full
provider detail before compact home cards. Catalog queries are largely serial.
Cancellation/deadline coverage has gaps in those helpers. These are investigation
candidates, not established live causes or approved claims of a speedup.

The CodeServer workspace is Preview, separate from Production. Its process,
memory, or database timings must not be described as live Production metrics.
No internal diagnostic credential will be introduced or security policy weakened.

## Verification, preservation, and next action

Completed: fresh production ref and PR #2058 metadata comparison; exact prior
evidence branch fetch and SHA verification; applicable AGENTS.md, task continuity,
and Issue #1006 inspection; read-only independent frontend/backend path reviews;
production administration and live-page observations described above.
No new implementation test has completed at this checkpoint. No new PR or CI
result exists. This is a **WIP diagnosis checkpoint**, not release-ready code.

Next: obtain bounded current runtime evidence through supported administration or
the authorized terminal; resolve the service pressure demonstrated in the logs;
finish the scoped loader recovery and its focused regression tests; independently
review any patch and follow protocol v5 with a fresh source/intent pair, actual-host
rehearsal, owner lease, exactly one authorized publication, and three original
guard probes. Live acceptance must include a cold initial home navigation.

Preserve unrelated shared /app files and leases. Check the original guard's
fresh inactive status before any /app mutation or rehearsal. Do not reset hard,
clear another owner's lease, call paid chat, or test with real financial writes.
Durable task documents and PR metadata carry this handoff; no Issue #1006 comment
or other outbound message was sent.
